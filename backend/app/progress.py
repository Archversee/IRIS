"""How trainees are getting on across flights (the Progress page). After the
flight only; nothing here runs live.

Pure: data in, numbers and plain-words notes out. routers/progress.py
gathers the data and caches the per-flight part (session_stats()).

Per flight (session_stats):
  phases    per flight phase: where the pilot looked (share per instrument,
            how often each was checked, how spread out the attention was) and
            how they flew (how steady speed / height / climb rate were, how
            often the stick and pedals reversed)
  approach  final approach speed and sink rate, mean and spread
  landing   sink rate, speed, pitch and bank at touchdown
  callouts  each MAVIS correction callout: time to the first control input,
            and whether the value it was about moved the right way
Across flights (learning_curve, coaching_summary, insights, rule_impact).
"""
from __future__ import annotations

import bisect
import math
import re
import statistics
from datetime import datetime

from .aoi_roles import role_of
from .incidents import _num, _t, gaze_target

STATS_VERSION = "1"
GROUND = {None, "", "BRIEFING", "PRE_TAKEOFF", "TAKEOFF_ROLL", "FLARE_TOUCHDOWN", "ROLLOUT", "DEBRIEF"}
PHASE_ORDER = ["CLIMB_OUT", "CROSSWIND", "DOWNWIND", "BASE", "FINAL"]
LEVEL = {"DOWNWIND"}            # phases flown level: height should stay put
SETTLE_S = 5.0                  # ignore this long after a phase starts (the transition itself)
MAX_DT = 0.2                    # an eye sample stands for at most this long (dropouts don't count as looking)
MAX_GAP = 60.0                  # longer than this between two looks at an instrument = not counted as a gap
INSTRUMENTS = ("airspeed", "altimeter", "attitude", "vsi", "heading", "turn", "engine")
REVERSAL = {"elevator_position": 0.04, "aileron_position": 0.04, "rudder_position": 0.04}   # -1..1 travel
REACT_S = 10.0                  # a control input within this long of a callout = a response
HOLD_S = 3.0                    # ... seen in the average over this long (a constantly busy stick averages out)
HELP_S = 15.0                   # the value has this long to move the right way
CONTROL_STEP = {"throttle_pct": 5.0, "elevator_position": 0.05, "aileron_position": 0.05,
                "rudder_position": 0.05, "flaps_deg": 4.0}
# the controls that can fix each value (a callout about it is answered with one of these)
FIXED_BY = {"airspeed_kt": ("throttle_pct", "elevator_position"), "altitude_ft": ("elevator_position", "throttle_pct"),
            "vertical_speed_fpm": ("elevator_position", "throttle_pct"), "bank_deg": ("aileron_position", "rudder_position"),
            "pitch_deg": ("elevator_position",), "flaps_deg": ("flaps_deg",), "rpm": ("throttle_pct",)}
TOUCHDOWN_S = 1.5               # sink rate at touchdown = steepest in this long before
FORGET_DAYS = 7.0               # a gap this long between lessons counts as "a break"

# finding code -> how it reads as a cause (app/incidents.py _findings)
CAUSES = {
    "NOT_MONITORED": "not checking the instrument that shows it",
    "LATE_DETECTION": "noticing late",
    "FIXATION": "attention fixed on one thing",
    "SLOW_ACTION": "correcting slowly",
    "NO_CORRECTION": "no clear correction",
    "OVERSHOOT": "over-correcting",
    "REBREACH": "breaking it again soon after",
    "HIGH_WORKLOAD": "high workload",
}
CAUSE_KIND = {"NOT_MONITORED": "perception", "LATE_DETECTION": "perception", "FIXATION": "perception",
              "SLOW_ACTION": "action", "NO_CORRECTION": "action", "OVERSHOOT": "handling", "REBREACH": "handling",
              "HIGH_WORKLOAD": "workload"}

# MAVIS callout code -> (flight_data column, which way it should go: +1 up, -1 down, "abs-" smaller magnitude,
# how much counts as a real move). Codes not here (turn calls, track, info) only get a reaction time.
_CALLOUT_GOAL = [
    (r"^(SPEED_FAST|FAM_FAST)", ("airspeed_kt", -1, 3.0)),
    (r"^SINK_HIGH_FAST$", ("airspeed_kt", -1, 3.0)),
    (r"^(SPEED_SLOW|FAM_SLOW)", ("airspeed_kt", +1, 3.0)),
    (r"^(ALT_LOW|FAM_LOW)", ("altitude_ft", +1, 40.0)),
    (r"^ALT_HIGH", ("altitude_ft", -1, 40.0)),
    (r"^(BANK_STEEP|FAM_STEEP)$", ("bank_deg", "abs-", 5.0)),
    (r"^SINK_HIGH$", ("vertical_speed_fpm", +1, 200.0)),
    (r"^(ROTATE_NOW|ROTATE_LATE)$", ("pitch_deg", +1, 3.0)),
    (r"^(FLAPS_REMINDER|ABEAM_FLAPS)$", ("flaps_deg", +1, 5.0)),
]
_SOP_COLUMN = {"ias": ("airspeed_kt", 3.0), "alt_msl": ("altitude_ft", 40.0), "alt_agl": ("altitude_ft", 40.0),
               "vs": ("vertical_speed_fpm", 200.0), "bank": ("bank_deg", 5.0), "pitch": ("pitch_deg", 3.0),
               "flaps_deg": ("flaps_deg", 5.0), "rpm": ("rpm", 100.0)}
CALLOUT_LABEL = {
    "SPEED_FAST": "Too fast", "SPEED_FAST_LEVEL": "Too fast (level)", "SPEED_SLOW": "Too slow",
    "SPEED_SLOW_LEVEL": "Too slow (level)", "ALT_LOW": "Too low", "ALT_LOW_FAST": "Low and fast", "ALT_HIGH": "Too high",
    "ALT_HIGH_SLOW": "High and slow", "BANK_STEEP": "Bank too steep", "SINK_HIGH": "Sinking fast",
    "SINK_HIGH_FAST": "Sinking fast, nose low", "ROTATE_NOW": "Rotate now", "ROTATE_LATE": "Rotate (late)",
    "FLAPS_REMINDER": "Flaps reminder", "ABEAM_FLAPS": "Flaps abeam", "SOP_RULE": "SOP rule warning",
    "FAM_LOW": "Too low", "FAM_SLOW": "Too slow", "FAM_FAST": "Too fast", "FAM_FAST_DIVE": "Too fast, diving",
    "FAM_STEEP": "Bank too steep", "OFF_TRACK": "Off track", "ROLL_OUT_LEG": "Roll out on the leg",
    "SMOOTH_HANDS": "Smooth hands", "TURN_CROSSWIND": "Turn crosswind", "TURN_DOWNWIND": "Turn downwind",
    "TURN_BASE": "Turn base", "TURN_FINAL": "Turn final", "PRE_TURN": "Turn coming up", "PRE_TURN_ALT": "Turn coming up",
}


def callout_label(code: str) -> str:
    return CALLOUT_LABEL.get(code) or code.replace("_", " ").capitalize()


# ---------------------------------------------------------------- helpers
def _sd(xs: list[float]) -> float | None:
    return round(statistics.pstdev(xs), 2) if len(xs) >= 5 else None


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 2) if xs else None


def _median(xs: list[float]) -> float | None:
    return round(statistics.median(xs), 2) if xs else None


def _reversals(vals: list[float], step: float) -> int:
    """Times the control changed direction by more than `step` (hysteresis, so noise doesn't count)."""
    if not vals:
        return 0
    n, d, ext = 0, 0, vals[0]        # d: current direction (+1 / -1, 0 = not moved yet), ext: furthest point
    for v in vals[1:]:
        if d == 0:
            if abs(v - ext) > step:
                d, ext = (1 if v > ext else -1), v
        elif d * (v - ext) > 0:
            ext = v
        elif abs(v - ext) > step:
            n, d, ext = n + 1, -d, v
    return n


def _entropy(share: dict[str, float]) -> float | None:
    """How spread out attention was: 0 = all on one thing, 1 = evenly over everything looked at."""
    ps = [p for p in share.values() if p > 0]
    if len(ps) < 2:
        return 0.0 if ps else None
    tot = sum(ps)
    h = -sum(p / tot * math.log(p / tot) for p in ps)
    return round(h / math.log(len(ps)), 3)


# ---------------------------------------------------------------- one flight
def session_stats(flight: list[dict], eye: list[dict], zones: list[dict], callouts: list[dict]) -> dict:
    """flight: flight_data rows (ts, phase, airspeed_kt, altitude_ft, vertical_speed_fpm, bank_deg, pitch_deg,
    throttle_pct, elevator_position, aileron_position, rudder_position, flaps_deg, on_ground)
    eye: eye rows; zones: AOI zones; callouts: coaching_events rows (ts, code, severity, phase, message, data)"""
    fl = sorted(flight, key=lambda r: _t(r["ts"]))
    for r in fl:
        r["_t"] = _t(r["ts"])
    ft = [r["_t"] for r in fl]

    # phase segments: [(phase, t_start, t_end)]
    segs: list[list] = []
    for r in fl:
        ph = r.get("phase")
        if segs and segs[-1][0] == ph and r["_t"] - segs[-1][2] < 5:
            segs[-1][2] = r["_t"]
        else:
            segs.append([ph, r["_t"], r["_t"]])
    phase_time: dict[str, float] = {}
    for ph, a, b in segs:
        if ph not in GROUND:
            phase_time[ph] = phase_time.get(ph, 0.0) + (b - a)

    def phase_at(t: float):
        i = bisect.bisect_right(ft, t) - 1
        return fl[i].get("phase") if i >= 0 else None

    def settled(t: float) -> bool:
        for ph, a, b in segs:
            if a <= t <= b + 0.3:
                return t - a >= SETTLE_S
        return False

    phases: dict[str, dict] = {}

    # -- handling per phase
    for ph in phase_time:
        rows = [r for r in fl if r.get("phase") == ph and settled(r["_t"])]
        if len(rows) < 10:
            continue
        col = lambda c: [v for v in (_num(r.get(c)) for r in rows) if v is not None]   # noqa: E731
        minutes = max(phase_time[ph] / 60.0, 1e-6)
        h = {"ias_sd": _sd(col("airspeed_kt")), "vs_sd": _sd(col("vertical_speed_fpm")),
             "alt_sd": _sd(col("altitude_ft")) if ph in LEVEL else None,
             "ias_mean": _mean(col("airspeed_kt"))}
        rev = {}
        for c, step in REVERSAL.items():
            vals = col(c)
            if len(vals) >= 10:
                rev[c.split("_")[0]] = round(_reversals(vals, step) / minutes, 1)
        h["reversals_per_min"] = rev
        phases.setdefault(ph, {"seconds": round(phase_time[ph], 1)})["handling"] = h

    # -- scan per phase (and airborne overall)
    acc: dict[str, dict] = {}

    def a_for(key):
        return acc.setdefault(key, {"time": 0.0, "by_role": {}, "looks": {}, "longest": 0.0})

    prev_t = prev_role = run_start = None
    eye_air = 0.0
    for e in sorted(eye, key=lambda x: _t(x["ts"])):
        t = _t(e["ts"])
        ph = phase_at(t)
        name = gaze_target(e, zones)
        role = role_of(name) if name else None
        dt = min(t - prev_t, MAX_DT) if prev_t is not None else 0.0
        if ph not in GROUND:
            eye_air += dt
            for key in (ph, "_airborne"):
                a = a_for(key)
                a["time"] += dt
                if role:
                    a["by_role"][role] = a["by_role"].get(role, 0.0) + dt
                if role in INSTRUMENTS and role != prev_role:
                    a["looks"].setdefault(role, []).append(t)
        if role != prev_role or (prev_t is not None and t - prev_t > 0.3):
            run_start = t
        if role and role != "outside" and run_start is not None and ph not in GROUND:   # long looks outside are fine
            for key in (ph, "_airborne"):
                a_for(key)["longest"] = max(a_for(key)["longest"], t - run_start)
        prev_t, prev_role = t, role

    def scan_of(a) -> dict | None:
        tot = a["time"]
        if tot < 5:
            return None
        share = {r: round(v / tot, 3) for r, v in sorted(a["by_role"].items(), key=lambda x: -x[1])}
        every, gap = {}, {}
        for inst, starts in a["looks"].items():
            gaps = [b - x for x, b in zip(starts, starts[1:]) if b - x <= MAX_GAP]
            if gaps:
                every[inst] = round(sum(gaps) / len(gaps), 1)
                gap[inst] = round(max(gaps), 1)
        return {"seconds": round(tot, 1), "share": share, "check_every_s": every, "longest_gap_s": gap,
                "spread": _entropy(share), "longest_look_s": round(a["longest"], 1)}

    for ph, a in acc.items():
        if ph == "_airborne":
            continue
        s = scan_of(a)
        if s:
            phases.setdefault(ph, {"seconds": round(phase_time.get(ph, 0.0), 1)})["scan"] = s
    flight_air = sum(phase_time.values())

    # -- approach and landing
    final = [r for r in fl if r.get("phase") == "FINAL" and settled(r["_t"])]
    fias = [v for v in (_num(r.get("airspeed_kt")) for r in final) if v is not None]
    fvs = [v for v in (_num(r.get("vertical_speed_fpm")) for r in final) if v is not None]
    approach = {"ias_mean": _mean(fias), "ias_sd": _sd(fias), "vs_mean": _mean(fvs), "vs_sd": _sd(fvs)} if final else None
    landing = None
    was_air = False
    for prev, r in zip(fl, fl[1:]):
        if prev.get("on_ground") is False:
            was_air = True
        if was_air and prev.get("on_ground") is False and r.get("on_ground") is True:
            # sink rate: the steepest in the last moments before the wheels touched (the
            # touchdown sample itself often already reads ~0)
            near = [v for v in (_num(x.get("vertical_speed_fpm")) for x in fl
                                if r["_t"] - TOUCHDOWN_S <= x["_t"] < r["_t"]) if v is not None]
            landing = {"vs_fpm": min(near) if near else None, "ias": _num(prev.get("airspeed_kt")),
                       "pitch": _num(prev.get("pitch_deg")), "bank": _num(prev.get("bank_deg"))}
            landing = {k: (round(v, 1) if v is not None else None) for k, v in landing.items()}
            break

    return {"version": STATS_VERSION, "phases": phases, "airborne_scan": scan_of(acc["_airborne"]) if "_airborne" in acc else None,
            "eye_coverage": round(eye_air / flight_air, 2) if flight_air > 0 and eye else None,
            "approach": approach, "landing": landing, "callouts": callout_responses(fl, callouts)}


def _goal(c: dict):
    code = c.get("code") or ""
    if code == "SOP_RULE":
        d = c.get("data") or {}
        col = _SOP_COLUMN.get(d.get("metric"))
        v, lo, hi = _num(d.get("value")), _num(d.get("lo")), _num(d.get("hi"))
        if not col or v is None:
            return None
        if hi is not None and v > hi:
            return col[0], -1, col[1]
        if lo is not None and v < lo:
            return col[0], +1, col[1]
        return None
    for pat, goal in _CALLOUT_GOAL:
        if re.match(pat, code):
            return goal
    return None


def callout_responses(fl: list[dict], callouts: list[dict]) -> list[dict]:
    """For each MAVIS correction callout: seconds to the first control input,
    and whether the value it was about moved the right way within HELP_S.
    fl: sorted flight rows with '_t'."""
    ft = [r["_t"] for r in fl]
    out = []
    for c in callouts:
        code = c.get("code")
        if not code or (c.get("who") not in (None, "instructor")) or c.get("severity") == "info":
            continue
        t = _t(c["ts"])
        i = bisect.bisect_left(ft, t)
        if i >= len(fl) or ft[i] - t > 2:
            continue
        base = fl[i]
        goal = _goal(c)
        controls = FIXED_BY.get(goal[0], tuple(CONTROL_STEP)) if goal else tuple(CONTROL_STEP)
        react = None
        # a real input: the control's average over HOLD_S moved `step` away from its average in the
        # HOLD_S before the callout (so jitter and a constantly busy stick don't count)
        def level(rows, col):
            vs = [v for v in (_num(r.get(col)) for r in rows) if v is not None]
            return sum(vs) / len(vs) if len(vs) >= 2 else None
        ref = {col: level(fl[bisect.bisect_left(ft, t - HOLD_S):i + 1], col) for col in controls}
        for j in range(i, len(fl)):
            if fl[j]["_t"] - t > REACT_S:
                break
            win = fl[j:bisect.bisect_right(ft, fl[j]["_t"] + HOLD_S)]
            for col in controls:
                lv, step = level(win, col), CONTROL_STEP[col]
                if ref.get(col) is not None and lv is not None and abs(lv - ref[col]) >= step:
                    moved = next((r for r in win if _num(r.get(col)) is not None
                                  and abs(_num(r[col]) - ref[col]) >= step), win[0])
                    react = round(moved["_t"] - t, 1)
                    break
            if react is not None:
                break
        helped = None
        if goal:
            col, way, thr = goal
            v0 = _num(base.get(col))
            if v0 is not None:
                best = 0.0
                for r in fl[i:]:
                    if r["_t"] - t > HELP_S:
                        break
                    v = _num(r.get(col))
                    if v is None:
                        continue
                    gain = (abs(v0) - abs(v)) if way == "abs-" else way * (v - v0)
                    best = max(best, gain)
                helped = best >= thr
        out.append({"code": code, "t": round(t, 1), "phase": c.get("phase") or base.get("phase"),
                    "reaction_s": react, "helped": helped})
    return out


# ---------------------------------------------------------------- across flights
def pilot_of(session: dict) -> str | None:
    """Who flew it: sessions.pilot_name, else the part after ' – ' in a MAVIS
    session name ('Your First Circuit – Alex'). None when unknown."""
    p = (session.get("pilot_name") or "").strip()
    if p:
        return p
    name = session.get("name") or ""
    if " – " in name:
        who = name.rsplit(" – ", 1)[1].strip()
        if who and not re.match(r"^\d{1,2} [A-Z][a-z]{2} \d{2}:\d{2}$", who):     # mavis_bridge's date fallback
            return who
    return None


def flight_label(s: dict) -> str:
    ts = s.get("started_at") or s.get("created_at")
    when = ts.strftime("%d %b %H:%M") if isinstance(ts, datetime) else (str(ts)[:16] if ts else "")
    name = s.get("name") or ""
    lesson = name.rsplit(" – ", 1)[0] if " – " in name else (s.get("lesson_id") or name)
    return f"{when} · {lesson}".strip(" ·")


def learning_curve(history: list[dict]) -> list[dict]:
    """history: checked flights oldest first, each {at (iso), rules: {title: {verdict, breaches}}}
    -> per rule: [{title, series: [breaches or None per flight], broken, applied, repeats, relapses}]
       repeats  = longest run of consecutive flights it was broken in
       relapses = times it was met and then broken again on the next flight it applied,
                  split by whether there was a break of FORGET_DAYS+ in between"""
    titles = sorted({t for h in history for t in h["rules"]})
    out = []
    for title in titles:
        series, applied, broken, run, best_run = [], 0, 0, 0, 0
        relapse = {"after_break": 0, "after_break_of": 0, "soon": 0, "soon_of": 0}
        last = None                       # (verdict, at) of the last flight it applied to
        for h in history:
            r = h["rules"].get(title)
            v = r and r["verdict"]
            if v not in ("pass", "fail"):
                series.append(None)
                continue
            applied += 1
            series.append(r.get("breaches", 1 if v == "fail" else 0))
            if v == "fail":
                broken += 1
                run += 1
                best_run = max(best_run, run)
            else:
                run = 0
            if last and last[0] == "pass":
                gap = _days(last[1], h.get("at"))
                key = "after_break" if gap is not None and gap >= FORGET_DAYS else "soon"
                relapse[key + "_of"] += 1
                relapse[key] += v == "fail"
            last = (v, h.get("at"))
        out.append({"title": title, "series": series, "applied": applied, "broken": broken,
                    "repeats": best_run, "relapses": relapse})
    out.sort(key=lambda r: (-r["broken"], r["title"]))
    return out


def _days(a, b) -> float | None:
    try:
        return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 86400
    except (TypeError, ValueError):
        return None


def coaching_summary(callouts: list[dict]) -> list[dict]:
    """callouts: callout_responses() over any number of flights
    -> per code: {code, label, count, responded, helped, judged, median_reaction_s}"""
    by: dict[str, list] = {}
    for c in callouts:
        by.setdefault(c["code"], []).append(c)
    out = []
    for code, cs in by.items():
        judged = [c for c in cs if c["helped"] is not None]
        reacts = [c["reaction_s"] for c in cs if c["reaction_s"] is not None]
        out.append({"code": code, "label": callout_label(code), "count": len(cs),
                    "responded": len(reacts), "helped": sum(1 for c in judged if c["helped"]),
                    "judged": len(judged), "median_reaction_s": _median(reacts),
                    "ignored": sum(1 for c in cs if c["reaction_s"] is None and not c["helped"])})
    out.sort(key=lambda r: -r["count"])
    return out


def phase_scan_table(stats: list[dict]) -> dict[str, dict]:
    """Average the per-phase scan numbers over several flights' session_stats()."""
    out: dict[str, dict] = {}
    for ph in PHASE_ORDER + sorted({p for s in stats for p in (s.get("phases") or {})} - set(PHASE_ORDER)):
        scans = [s["phases"][ph]["scan"] for s in stats if (s.get("phases") or {}).get(ph, {}).get("scan")]
        hands = [s["phases"][ph]["handling"] for s in stats if (s.get("phases") or {}).get(ph, {}).get("handling")]
        if not scans and not hands:
            continue
        row = {"flights": max(len(scans), len(hands))}
        if scans:
            row["outside"] = _mean([x["share"].get("outside", 0.0) for x in scans])
            row["spread"] = _mean([x["spread"] for x in scans if x.get("spread") is not None])
            for inst in ("airspeed", "altimeter", "attitude"):
                vals = [x["check_every_s"][inst] for x in scans if inst in x.get("check_every_s", {})]
                row[f"{inst}_every_s"] = _mean(vals)
        if hands:
            for k in ("ias_sd", "alt_sd", "vs_sd"):
                vals = [x[k] for x in hands if x.get(k) is not None]
                row[k] = _mean(vals)
            row["reversals_per_min"] = _mean([sum(x["reversals_per_min"].values()) for x in hands
                                              if x.get("reversals_per_min")])
        out[ph] = row
    return out


def insights(history: list[dict], stats: list[dict], expert: dict[str, dict] | None) -> list[dict]:
    """history: checked flights oldest first ({rules: {title: {verdict, breaches}}, causes, breaches, at});
    stats: session_stats() of the same pilot's flights oldest first (None where missing);
    expert: phase_scan_table() of the expert flights, or None
    -> [{kind: improving|persistent|regression|forgetting|cause|scan|handling|coaching|info, text}]"""
    out: list[dict] = []
    for r in learning_curve(history):
        seq = [s for s in r["series"] if s is not None]
        if len(seq) < 3:
            continue
        fails = [s > 0 for s in seq]
        title = r["title"]
        if fails[-1] and fails[-2] or fails[-3:].count(True) >= 2:
            out.append({"kind": "persistent", "text": f"Still breaking “{title}”: {fails[-3:].count(True)} of the "
                        "last 3 flights."})
        elif not fails[-1] and not fails[-2] and any(fails[:-2]):
            out.append({"kind": "improving", "text": f"Improved on “{title}”: met in the last 2 flights after "
                        f"breaking it in {fails[:-2].count(True)} of the {len(fails) - 2} before."})
        elif fails[-1] and fails[-3:-1] == [False, False]:
            out.append({"kind": "regression", "text": f"“{title}” was met in the 2 flights before, but broken "
                        "again in the latest one."})
    rel = [r["relapses"] for r in learning_curve(history)]
    ab, abo = sum(x["after_break"] for x in rel), sum(x["after_break_of"] for x in rel)
    so, soo = sum(x["soon"] for x in rel), sum(x["soon_of"] for x in rel)
    if abo >= 3 and ab / abo >= 0.3 and (soo == 0 or ab / abo > so / soo + 0.15):
        out.append({"kind": "forgetting", "text": f"Forgets between lessons: after a break of {FORGET_DAYS:.0f}+ days, "
                    f"rules met last time were broken again {ab} of {abo} times (vs {so} of {soo} with shorter gaps)."})

    recent = history[-5:]
    counts: dict[str, int] = {}
    for h in recent:
        for code, n in (h.get("causes") or {}).items():
            if code in CAUSES:
                counts[code] = counts.get(code, 0) + n
    breaches = sum(h.get("breaches", 0) for h in recent)
    if counts and breaches:
        code, n = max(counts.items(), key=lambda x: x[1])
        if n >= 2 and n / breaches >= 0.3:
            out.append({"kind": "cause", "text": f"The most common cause in the last {len(recent)} flights: "
                        f"{CAUSES[code]}, in {n} of {breaches} rule breaches."})

    got = [s for s in stats if s]
    if len(got) >= 2:
        worst: dict[str, tuple[float, dict]] = {}          # one note per comparison: the phase where it's worst

        def note(key, diff, kind, text):
            if key not in worst or diff > worst[key][0]:
                worst[key] = (diff, {"kind": kind, "text": text})
        for ph, row in phase_scan_table(got[-3:]).items():
            ex = (expert or {}).get(ph)
            if not ex:
                continue
            a, b = row.get("airspeed_every_s"), ex.get("airspeed_every_s")
            if a is not None and b is not None and a > b * 1.5 and a - b >= 3:
                note("asi", a - b, "scan", f"{_ph(ph)}: checks the airspeed about every {a:.0f} s; "
                     f"the expert flights about every {b:.0f} s.")
            a, b = row.get("outside"), ex.get("outside")
            if a is not None and b is not None and b - a >= 0.15:
                note("outside", b - a, "scan", f"{_ph(ph)}: {a:.0%} of the time looking outside; the expert "
                     f"flights {b:.0%}.")
            a, b = row.get("ias_sd"), ex.get("ias_sd")
            if a is not None and b is not None and a > b * 1.6 and a - b >= 2:
                note("ias", a - b, "handling", f"{_ph(ph)}: speed wanders ±{a:.1f} kt; the expert flights "
                     f"±{b:.1f} kt.")
            a, b = row.get("reversals_per_min"), ex.get("reversals_per_min")
            if a is not None and b is not None and a > b * 1.6 and a - b >= 10:
                note("rev", a - b, "handling", f"{_ph(ph)}: busy on the controls, {a:.0f} stick / pedal reversals "
                     f"a minute; the expert flights {b:.0f}.")
        out += [v[1] for v in worst.values()]
    landings = [s["landing"]["vs_fpm"] for s in got if s.get("landing") and s["landing"].get("vs_fpm") is not None]
    if len(landings) >= 3:
        sd = statistics.pstdev(landings)
        if sd >= 150:
            out.append({"kind": "handling", "text": f"Landings vary a lot: touchdown sink rate spread ±{sd:.0f} ft/min "
                        "over the last flights."})
    calls = [c for s in got[-5:] for c in s.get("callouts") or []]
    judged = [c for c in calls if c["helped"] is not None]
    if len(judged) >= 4:
        ign = sum(1 for c in calls if c["reaction_s"] is None and not c["helped"])
        helped = sum(1 for c in judged if c["helped"])
        out.append({"kind": "coaching", "text": f"MAVIS's correction callouts: fixed within {HELP_S:.0f} s "
                    f"{helped} of {len(judged)} times; no response to {ign} of {len(calls)}."})
    if not out:
        out.append({"kind": "info", "text": "Not enough flights with rule checks yet to see a trend (needs 3+)."
                    if len(history) < 3 else "No clear trend yet."})
    return out


def _ph(ph: str) -> str:
    return ph.replace("_", " ").title()


SEVERITY_WEIGHT = {"warning": 3, "caution": 2, "advisory": 1}


def rule_impact(flights: list[dict], knock_on_s: float = 60.0) -> list[dict]:
    """Which handbook rules matter most in practice, over many pilots' flights.
    flights: [{pilot, rules: {title: {verdict, severity, breach_t: [t0...]}}}]
    -> [{title, severity, flights, broken, fail_rate, pilots, knock_on, score}] most important first.
    knock_on = share of its breaches followed by a different rule breaking within knock_on_s."""
    agg: dict[str, dict] = {}
    for f in flights:
        all_t = [(t, title) for title, r in f["rules"].items() for t in r.get("breach_t") or []]
        for title, r in f["rules"].items():
            a = agg.setdefault(title, {"title": title, "severity": r.get("severity"), "flights": 0, "broken": 0,
                                       "pilots": set(), "breaches": 0, "knocked": 0})
            if r["verdict"] in ("pass", "fail"):
                a["flights"] += 1
            if r["verdict"] == "fail":
                a["broken"] += 1
                a["pilots"].add(f["pilot"].lower())
            for t in r.get("breach_t") or []:
                a["breaches"] += 1
                a["knocked"] += any(o != title and 0 < t2 - t <= knock_on_s for t2, o in all_t)
    out = []
    for a in agg.values():
        if not a["flights"]:
            continue
        rate = a["broken"] / a["flights"]
        knock = a["knocked"] / a["breaches"] if a["breaches"] else 0.0
        score = rate * SEVERITY_WEIGHT.get(a["severity"], 2) * (1 + knock)
        out.append({"title": a["title"], "severity": a["severity"], "flights": a["flights"], "broken": a["broken"],
                    "fail_rate": round(rate, 3), "pilots": len(a["pilots"]), "knock_on": round(knock, 2),
                    "score": round(score, 3)})
    out.sort(key=lambda r: -r["score"])
    return out
