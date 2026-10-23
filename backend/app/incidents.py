"""What happened around a broken rule: the 30 s before and after it.

Pure: data in, facts out (no database, no LLM). routers/incidents.py feeds it
one violation of one rule (rule_results.violations[i]) plus the flight data,
eye data, AOI zones and MAVIS callouts of that window. It measures:

  onset      when the value started heading for the limit (not when it crossed it)
  gaze       where the pilot was looking before it went wrong; when they last /
             first looked at the instrument that shows it; any long stare
  response   first real control input after the onset; when the value turned
             back; time back inside the limit; overshoot / second breach
  workload   pupil size vs the pilot's own session median
  context    phase, turn, flap change, MAVIS callouts in the window

and turns those into findings with the numbers behind them. Every number a
later LLM explanation uses comes from here, so the explanation can't invent
them. Times in the result are seconds relative to the breach (t = 0).
"""
from __future__ import annotations

import math
from datetime import datetime

from .aoi_roles import METRIC_ROLES, ROLE_LABELS, role_of

PRE_S = 30.0            # look this far back before the breach ... (defaults: each view can choose)
POST_S = 30.0           # ... and this far past the recovery
MIN_WINDOW_S, MAX_WINDOW_S = 10.0, 120.0     # what a view may choose
LOOK_GAP_S = 10.0       # no look at the instrument for this long before the breach = not monitored
FIXATION_S = 5.0        # one unbroken look this long at something else = attention stuck
SLOW_ACTION_S = 2.0     # corrective input this long after noticing = slow to act
QUICK_RECOVERY_S = 3.0
HIGH_WORKLOAD = 0.15    # pupil this much (15 %) above the pilot's session median
BLINK_GAP_S = 0.3       # a look continues across a blink / dropout shorter than this
ONSET_FLAT_S = 1.5      # the build-up started where the value was last steady for this long
TOL_MULT = {"lenient": 1.5, "standard": 1.0, "strict": 1.0}   # MAVIS sop/evaluate.py STRICTNESS

# IRIS flight_data column for each rule metric (MAVIS telemetry names)
_COLUMN = {"ias": "airspeed_kt", "alt_msl": "altitude_ft", "alt_agl": "altitude_ft", "vs": "vertical_speed_fpm",
           "bank": "bank_deg", "pitch": "pitch_deg", "hdg": "heading_true_deg", "flaps_deg": "flaps_deg",
           "rpm": "rpm", "throttle": "throttle_pct"}
# "still moving toward the limit" tolerance per metric (noise)
_EPS = {"ias": 0.5, "alt_msl": 10, "alt_agl": 10, "vs": 50, "bank": 1.0, "pitch": 0.5, "hdg": 1.0,
        "flaps_deg": 0.5, "rpm": 20, "throttle": 0.01}
_UNIT = {"ias": "kt", "alt_msl": "ft", "alt_agl": "ft", "vs": "ft/min", "bank": "°", "pitch": "°", "hdg": "°",
         "flaps_deg": "°", "rpm": "rpm", "throttle": ""}
# controls that can correct each metric, and what counts as a real input (change within 1 s)
_CONTROLS = {"ias": ("throttle", "elevator"), "alt_msl": ("elevator", "throttle"), "alt_agl": ("elevator", "throttle"),
             "vs": ("elevator", "throttle"), "bank": ("aileron", "rudder"), "pitch": ("elevator",),
             "hdg": ("aileron", "rudder"), "rpm": ("throttle",), "throttle": ("throttle",), "flaps_deg": ()}
_CONTROL_COL = {"throttle": "throttle_pct", "elevator": "elevator_position", "aileron": "aileron_position",
                "rudder": "rudder_position"}
_CONTROL_STEP = {"throttle": 5.0, "elevator": 0.05, "aileron": 0.05, "rudder": 0.05}   # throttle in %
_CONTROL_LABEL = {"throttle": "throttle", "elevator": "stick fore/aft (elevator)", "aileron": "stick left/right (aileron)",
                  "rudder": "rudder pedals"}


def _t(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, datetime):
        return v.timestamp()
    return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()


def _num(v):
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def rule_bounds(rule: dict, strictness: str | None = None) -> tuple[float | None, float | None]:
    """(lo, hi) the value had to stay within; None = open on that side."""
    if rule.get("kind") == "band":
        m = TOL_MULT.get(strictness or "standard", 1.0)
        tgt = _num(rule.get("target"))
        lo = tgt - _num(rule["tol_lo"]) * m if tgt is not None and rule.get("tol_lo") is not None else None
        hi = tgt + _num(rule["tol_hi"]) * m if tgt is not None and rule.get("tol_hi") is not None else None
        return lo, hi
    return _num(rule.get("min_value")), _num(rule.get("max_value"))


def metric_value(row: dict, metric: str, elevation_ft: float | None):
    col = _COLUMN.get(metric)
    if col is None:
        return None
    v = _num(row.get(col))
    if metric == "flaps_deg" and v is None and _num(row.get("flaps_handle_pct")) is not None:
        v = _num(row["flaps_handle_pct"]) / 100.0 * 30.0
    if metric == "alt_agl" and v is not None:
        v = v - elevation_ft if elevation_ft is not None else None
    if metric == "throttle" and v is not None:
        v = v / 100.0
    return v


def _zone_for(zones, x, y):
    for z in zones:
        if min(z["x1"], z["x2"]) <= x <= max(z["x1"], z["x2"]) and min(z["y1"], z["y2"]) <= y <= max(z["y1"], z["y2"]):
            return z["name"]
    return None


def gaze_target(e: dict, zones: list[dict]) -> str | None:
    """Same as the Review page (utils.js effectiveAoi): an 'Instruments' sample
    inside a drawn zone becomes that zone; blinks / no target -> None."""
    if e.get("blink") or not e.get("aoi"):
        return None
    if e["aoi"] == "Instruments" and e.get("gaze_point_x") is not None and e.get("gaze_point_y") is not None:
        z = _zone_for(zones, float(e["gaze_point_x"]), float(e["gaze_point_y"]))
        if z:
            return z
    return e["aoi"]


def _segments(eye: list[dict], zones: list[dict], t0: float) -> list[dict]:
    """Eye samples -> looks: [{t0, t1, name, role}] (relative times), merging
    across blinks / dropouts shorter than BLINK_GAP_S."""
    segs: list[dict] = []
    for e in eye:
        t = _t(e["ts"]) - t0
        name = gaze_target(e, zones)
        if name is None:
            continue
        if segs and segs[-1]["name"] == name and t - segs[-1]["t1"] <= BLINK_GAP_S:
            segs[-1]["t1"] = t
        else:
            segs.append({"t0": t, "t1": t, "name": name, "role": role_of(name)})
    return segs


def _fmt(v, metric):
    if v is None:
        return "–"
    u = _UNIT.get(metric, "")
    if metric == "throttle":
        return f"{v * 100:.0f}%"
    return f"{v:.0f}{(' ' if u and u != '°' else '')}{u}" if abs(v) >= 10 else f"{v:.1f}{(' ' if u and u != '°' else '')}{u}"


def analyse(rule: dict, violation: dict, flight: list[dict], eye: list[dict], zones: list[dict],
            coaching: list[dict] | None = None, *, strictness: str | None = None,
            elevation_ft: float | None = None, pupil_baseline_mm: float | None = None,
            clock: dict | None = None, other_violations: list[dict] | None = None,
            pre_s: float = PRE_S, post_s: float = POST_S) -> dict:
    """pre_s / post_s: how far before the breach and after the recovery to look (default ±30 s)."""
    metric = rule.get("metric")
    t0, t1 = float(violation["t0"]), float(violation["t1"])
    lo, hi = rule_bounds(rule, strictness)
    eps = _EPS.get(metric, 0.0)
    out: dict = {"rule": {k: rule.get(k) for k in ("id", "title", "kind", "metric", "severity", "phases",
                                                  "source_quote", "source_page")},
                 "bounds": {"lo": lo, "hi": hi, "unit": _UNIT.get(metric, "")},
                 "breach": {"t0": t0, "t1": t1, "dur_s": round(t1 - t0, 1), "phase": violation.get("phase"),
                            "peak": violation.get("peak"), "peak_text": violation.get("peak_text")},
                 "window": {"from_s": -pre_s, "to_s": round(t1 - t0 + post_s, 1)}}

    # ---------------------------------------------------------------- flight series
    pts = []
    for r in flight:
        t = _t(r["ts"]) - t0
        if -pre_s - 1 <= t <= (t1 - t0) + post_s + 1:
            pts.append({"t": round(t, 2), "v": metric_value(r, metric, elevation_ft), "phase": r.get("phase"),
                        **{c: _num(r.get(col)) for c, col in _CONTROL_COL.items()},
                        "bank": _num(r.get("bank_deg")), "flaps": _num(r.get("flaps_deg"))})
    vals = [p for p in pts if p["v"] is not None]
    out["series"] = [{"t": p["t"], "v": p["v"], **{c: p[c] for c in _CONTROL_COL}} for p in pts]

    # which side was broken: the peak's side
    peak = _num(violation.get("peak"))
    upper = (hi is not None and peak is not None and peak > hi) or (lo is None)
    toward = 1.0 if upper else -1.0          # +1: going wrong = value rising

    # ---------------------------------------------------------------- onset
    # walk back from the breach while the value was still heading for the limit;
    # stop at the first move away, or once it has been steady for ONSET_FLAT_S
    pre = [p for p in vals if p["t"] <= 0]
    onset = 0.0
    if pre:
        i = len(pre) - 1
        onset_i, flat = i, 0.0
        while i > 0:
            step = toward * (pre[i]["v"] - pre[i - 1]["v"])
            if step > eps * 0.1:                 # still going wrong
                onset_i, flat = i - 1, 0.0
            elif step >= -eps:                   # steady (within noise)
                flat += pre[i]["t"] - pre[i - 1]["t"]
                if flat > ONSET_FLAT_S:
                    break
            else:                                # was moving the other way: it started after this
                break
            i -= 1
        onset = pre[onset_i]["t"]
    out["onset"] = {"t": round(onset, 1), "lead_s": round(-onset, 1),
                    "value_at_onset": pre[[p["t"] for p in pre].index(onset)]["v"] if pre else None}

    # ---------------------------------------------------------------- response
    resp: dict = {"recovered_s": round(t1 - t0, 1)}
    after = [p for p in vals if p["t"] >= onset]
    reversal = None
    for a, b in zip(after, after[1:]):
        if a["t"] >= 0 and toward * (b["v"] - a["v"]) < -eps:      # first move back away from the limit
            reversal = a["t"]
            break
    resp["turned_back_s"] = round(reversal, 1) if reversal is not None else None
    first_input = None
    for ch in _CONTROLS.get(metric, ()):
        seq = [p for p in pts if p[ch] is not None and p["t"] >= onset]
        for i, a in enumerate(seq):
            # a real input: the control moves by at least a step within 1 s of here;
            # its time is when it first moved, not the start of that second
            moved = next((b for b in seq[i + 1:] if b["t"] - a["t"] <= 1.0
                          and abs(b[ch] - a[ch]) >= _CONTROL_STEP[ch] * 0.2), None)
            later = next((b for b in seq[i + 1:] if b["t"] - a["t"] >= 1.0), None)
            if moved is not None and later is not None and abs(later[ch] - a[ch]) >= _CONTROL_STEP[ch]:
                if first_input is None or moved["t"] < first_input["t"]:
                    first_input = {"t": round(moved["t"], 1), "control": ch, "label": _CONTROL_LABEL[ch],
                                   "change": round(later[ch] - a[ch], 3)}
                break
    resp["first_input"] = first_input
    post = [p for p in vals if p["t"] > t1 - t0]
    overshoot = None
    if post:
        if upper and lo is not None and any(p["v"] < lo for p in post):
            overshoot = min(p["v"] for p in post)
        if not upper and hi is not None and any(p["v"] > hi for p in post):
            overshoot = max(p["v"] for p in post)
    resp["overshoot"] = overshoot
    resp["rebreached_s"] = next((round(float(v["t0"]) - t0, 1) for v in (other_violations or [])
                                 if 0 < float(v["t0"]) - t1 <= post_s), None)
    out["response"] = resp

    # ---------------------------------------------------------------- gaze
    segs = _segments(eye, zones, t0)
    out["gaze_segments"] = [{**s, "t0": round(s["t0"], 2), "t1": round(s["t1"], 2)} for s in segs]
    roles = METRIC_ROLES.get(metric, ())
    primary = roles[0] if roles else None
    zone_roles = {role_of(z["name"]) for z in zones}
    gaze: dict = {"primary_role": primary, "primary_label": ROLE_LABELS.get(primary) if primary else None,
                  "primary_zone_drawn": primary in zone_roles or primary in ("outside", "instruments")}
    win = [s for s in segs if s["t1"] >= -pre_s and s["t0"] <= 0]
    covered = sum(min(s["t1"], 0) - max(s["t0"], -pre_s) for s in win)
    gaze["coverage"] = round(max(0.0, covered) / pre_s, 2) if eye else 0.0
    share: dict[str, float] = {}
    for s in win:
        share[s["name"]] = share.get(s["name"], 0.0) + max(0.0, min(s["t1"], 0) - max(s["t0"], -pre_s))
    tot = sum(share.values()) or 1.0
    gaze["share_before"] = sorted(({"name": k, "role": role_of(k), "share": round(v / tot, 3)} for k, v in share.items()),
                                  key=lambda x: -x["share"])
    looks = [s for s in segs if primary and s["role"] == primary]
    last_before = max((s["t1"] for s in looks if s["t0"] <= 0), default=None)
    gaze["last_look_before_s"] = round(-min(last_before, 0), 1) if last_before is not None else None
    first_after = min((max(s["t0"], onset) for s in looks if s["t1"] >= onset), default=None)
    gaze["first_look_after_onset_s"] = round(first_after, 1) if first_after is not None else None
    longest = max(win, key=lambda s: min(s["t1"], 0) - max(s["t0"], -pre_s), default=None)
    if longest:
        gaze["longest_look"] = {"name": longest["name"], "role": longest["role"],
                                "dur_s": round(min(longest["t1"], 0) - max(longest["t0"], -pre_s), 1)}
    out["gaze"] = gaze

    # ---------------------------------------------------------------- workload
    pup = [(_num(e.get("pupil_diam_left_mm")), _num(e.get("pupil_diam_right_mm"))) for e in eye
           if -pre_s <= _t(e["ts"]) - t0 <= 0 and not e.get("blink")]
    pup = [sum(x for x in p if x is not None) / len([x for x in p if x is not None]) for p in pup if any(x is not None for x in p)]
    wl = {"pupil_mm": round(sum(pup) / len(pup), 2) if pup else None, "baseline_mm": pupil_baseline_mm}
    wl["vs_baseline"] = (round(wl["pupil_mm"] / pupil_baseline_mm - 1, 3)
                         if wl["pupil_mm"] and pupil_baseline_mm else None)
    out["workload"] = wl

    # ---------------------------------------------------------------- context
    phases = []
    for p in pts:
        if p["phase"] and (not phases or phases[-1]["phase"] != p["phase"]):
            phases.append({"t": p["t"], "phase": p["phase"]})
    pre_ctx = [p for p in pts if onset - 10 <= p["t"] <= 0]
    turning = max((abs(p["bank"]) for p in pre_ctx if p["bank"] is not None), default=0) >= 15
    flaps = [p["flaps"] for p in pre_ctx if p["flaps"] is not None]
    flap_change = bool(flaps) and max(flaps) - min(flaps) >= 5
    phase_change = next((ph for ph in phases[1:] if onset - 10 <= ph["t"] <= 0), None)
    calls = [{"t": round(_t(c["ts"]) - t0, 1), "who": c.get("who"), "code": c.get("code"), "message": c.get("message")}
             for c in (coaching or []) if -pre_s <= _t(c["ts"]) - t0 <= (t1 - t0) + post_s]
    out["context"] = {"phases": phases, "turning": turning, "flap_change": flap_change,
                      "phase_change": phase_change, "callouts": calls}
    out["clock"] = clock or {}

    out["findings"] = _findings(out, metric, onset)
    return out


def _f(code, kind, text, **evidence):
    return {"code": code, "kind": kind, "text": text, "evidence": evidence}


def _findings(a: dict, metric: str, onset: float) -> list[dict]:
    """Rule-based reading of the facts. kind: perception | action | handling |
    workload | context | positive | data."""
    out = []
    g, r, c, w = a["gaze"], a["response"], a["context"], a["workload"]
    inst = (g.get("primary_label") or "the instrument").lower()
    clock = a.get("clock") or {}
    has_eye = g["coverage"] >= 0.5

    if not has_eye:
        out.append(_f("NO_EYE_DATA", "data", f"Too little eye data around this breach ({g['coverage']:.0%} of the 30 s "
                      "before) to say where the pilot was looking.", coverage=g["coverage"]))
    elif not clock.get("checked_at"):
        out.append(_f("CLOCK_UNVERIFIED", "data", "Eye and flight clocks weren't measured for this session, so gaze "
                      "timings may be off by the clock difference between the two PCs."))
    elif not clock.get("corrected") and abs(clock.get("offset_s") or 0) > 0.1:
        out.append(_f("CLOCK_OFF", "data", f"The eye tracker's clock was {abs(clock['offset_s']):.2f} s off and wasn't "
                      "corrected: gaze timings are shifted by that much.", offset_s=clock["offset_s"]))
    if has_eye and not g["primary_zone_drawn"] and g.get("primary_role"):
        out.append(_f("NO_ZONE", "data", f"No AOI zone is named for the {inst}, so looks at it can't be told apart "
                      "from the rest of the panel. Draw and name one in AOI Zones.", role=g["primary_role"]))

    if has_eye and g["primary_zone_drawn"]:
        last = g.get("last_look_before_s")
        if last is None or last >= LOOK_GAP_S:
            out.append(_f("NOT_MONITORED", "perception",
                          f"Didn't look at the {inst} for {'over 30' if last is None else f'{last:.0f}'} s before the "
                          f"limit was exceeded.", last_look_before_s=last))
        fa = g.get("first_look_after_onset_s")
        if fa is not None and fa > 0:
            out.append(_f("LATE_DETECTION", "perception",
                          f"First looked at the {inst} {fa:.1f} s after the limit was exceeded.", first_look_s=fa))
    if has_eye:
        lg = g.get("longest_look")
        if lg and lg["dur_s"] >= FIXATION_S and lg["role"] not in METRIC_ROLES.get(metric, ()):
            out.append(_f("FIXATION", "perception", f"Eyes stayed on {lg['name']} for {lg['dur_s']:.1f} s without a "
                          f"break in the 30 s before.", **lg))

    fi = r.get("first_input")
    if fi is None:
        out.append(_f("NO_CORRECTION", "action", "No clear control input to correct it was found in the window.",))
    else:
        noticed = g.get("first_look_after_onset_s") if has_eye else None
        ref = noticed if noticed is not None else 0.0
        delay = fi["t"] - max(ref, onset)
        if fi["t"] > 0 and delay >= SLOW_ACTION_S:
            out.append(_f("SLOW_ACTION", "action",
                          f"First correcting input ({fi['label']}) came {fi['t']:.1f} s after the limit was exceeded"
                          + (f", {delay:.1f} s after first looking at the {inst}." if noticed is not None else "."),
                          input_s=fi["t"], delay_s=round(delay, 1)))
        call = next((cl for cl in c["callouts"] if cl["who"] != "trainee" and onset <= cl["t"] <= fi["t"]), None)
        if call:
            out.append(_f("PROMPTED", "context", f"Corrected {fi['t'] - call['t']:.1f} s after MAVIS said "
                          f"“{(call['message'] or '')[:80]}”.", callout_s=call["t"]))

    if r.get("overshoot") is not None:
        out.append(_f("OVERSHOOT", "handling", f"Over-corrected: went past the other limit "
                      f"({_fmt(r['overshoot'], metric)}) after recovering.", value=r["overshoot"]))
    if r.get("rebreached_s") is not None:
        out.append(_f("REBREACH", "handling", f"Broke the same rule again {r['rebreached_s']:.0f} s later.",
                      t=r["rebreached_s"]))
    if w.get("vs_baseline") is not None and w["vs_baseline"] >= HIGH_WORKLOAD:
        out.append(_f("HIGH_WORKLOAD", "workload", f"Pupil {w['vs_baseline']:.0%} larger than this pilot's usual in "
                      "the 30 s before (a sign of high workload; brightness also changes pupil size).",
                      vs_baseline=w["vs_baseline"]))
    busy = [x for x, on in (("a turn", c["turning"]), ("a flap change", c["flap_change"]),
                            (f"the change to {(c['phase_change'] or {}).get('phase', '').replace('_', ' ').lower()}",
                             bool(c["phase_change"]))) if on]
    if busy:
        out.append(_f("BUSY", "context", "It started building during " + " and ".join(busy) + ".", what=busy))
    if (r.get("recovered_s") or 99) <= QUICK_RECOVERY_S and r.get("overshoot") is None and r.get("rebreached_s") is None:
        out.append(_f("GOOD_RECOVERY", "positive", f"Back within limits in {r['recovered_s']:.1f} s without "
                      "over-correcting.", recovered_s=r["recovered_s"]))
    if a["onset"]["lead_s"] >= 3:
        out.append(_f("BUILD_UP", "context", f"The value was heading for the limit for {a['onset']['lead_s']:.0f} s "
                      "before it was crossed.", lead_s=a["onset"]["lead_s"]))
    return out
