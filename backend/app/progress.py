"""How a pilot is getting on across flights (post-flight only).

Pure: data in, numbers and plain-words insights out. routers/progress.py
gathers the data and caches the expensive parts (scan statistics per
session, the findings of each broken-rule moment from app/incidents.py).
"""
from __future__ import annotations

import bisect
import re
from datetime import datetime

from .aoi_roles import role_of
from .incidents import _t, gaze_target

SCAN_VERSION = "1"
GROUND = {None, "", "BRIEFING", "PRE_TAKEOFF", "TAKEOFF_ROLL", "FLARE_TOUCHDOWN", "ROLLOUT", "DEBRIEF"}
APPROACH = {"BASE", "FINAL"}
MAX_DT = 0.2            # an eye sample stands for at most this long (dropouts don't count as looking)
MAX_GAP = 60.0          # longer than this between two looks at an instrument = left that part of the flight

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

_DATEISH = re.compile(r"^\d{1,2} [A-Z][a-z]{2} \d{2}:\d{2}$")     # mavis_bridge's "20 Oct 02:05" fallback


def pilot_of(session: dict) -> str | None:
    """Who flew it: sessions.pilot_name, else the part after ' – ' in a MAVIS
    session name ('Your First Circuit – Alex'). None when unknown."""
    p = (session.get("pilot_name") or "").strip()
    if p:
        return p
    name = session.get("name") or ""
    if " – " in name:
        who = name.rsplit(" – ", 1)[1].strip()
        if who and not _DATEISH.match(who):
            return who
    return None


# ---------------------------------------------------------------- scan statistics
def scan_stats(eye: list[dict], flight: list[dict], zones: list[dict]) -> dict:
    """Where the pilot looked while airborne, and on base + final: share of
    time per instrument / outside, how often they checked airspeed and
    altitude, the longest unbroken look. Eye samples get the phase of the
    flight sample just before them."""
    fl = sorted(((_t(r["ts"]), r.get("phase")) for r in flight), key=lambda x: x[0])
    ft = [x[0] for x in fl]
    groups = {"airborne": lambda ph: ph not in GROUND, "approach": lambda ph: ph in APPROACH}
    acc = {g: {"time": 0.0, "by_role": {}, "looks": {"airspeed": [], "altimeter": []}, "longest": 0.0}
           for g in groups}
    flight_air = sum(min(b[0] - a[0], 1.0) for a, b in zip(fl, fl[1:]) if a[1] not in GROUND)

    prev_t, prev_role, run_start = None, None, None
    for e in sorted(eye, key=lambda x: _t(x["ts"])):
        t = _t(e["ts"])
        i = bisect.bisect_right(ft, t) - 1
        ph = fl[i][1] if i >= 0 else None
        name = gaze_target(e, zones)
        role = role_of(name) if name else None
        dt = min(t - prev_t, MAX_DT) if prev_t is not None else 0.0
        for g, inside in groups.items():
            if not inside(ph):
                continue
            a = acc[g]
            a["time"] += dt
            if role:
                a["by_role"][role] = a["by_role"].get(role, 0.0) + dt
            if role in a["looks"] and role != prev_role:
                a["looks"][role].append(t)
        if role != prev_role or (prev_t is not None and t - prev_t > 0.3):
            run_start = t
        if role and run_start is not None:
            for g, inside in groups.items():
                if inside(ph):
                    acc[g]["longest"] = max(acc[g]["longest"], t - run_start)
        prev_t, prev_role = t, role

    out = {"version": SCAN_VERSION}
    for g, a in acc.items():
        tot = a["time"]
        if tot < 5:                          # too little to say anything
            out[g] = None
            continue
        res = {"seconds": round(tot, 1),
               "share": {r: round(v / tot, 3) for r, v in sorted(a["by_role"].items(), key=lambda x: -x[1])},
               "longest_look_s": round(a["longest"], 1)}
        for inst, starts in a["looks"].items():
            gaps = [b - x for x, b in zip(starts, starts[1:]) if b - x <= MAX_GAP]
            res[f"{inst}_check_every_s"] = round(sum(gaps) / len(gaps), 1) if gaps else None
            res[f"{inst}_longest_gap_s"] = round(max(gaps), 1) if gaps else None
        out[g] = res
    out["eye_coverage"] = round(acc["airborne"]["time"] / flight_air, 2) if flight_air > 0 else None
    return out


# ---------------------------------------------------------------- insights
def _vals(history, key):
    return [(h, h["scan"]["approach"][key]) for h in history
            if (h.get("scan") or {}).get("approach") and h["scan"]["approach"].get(key) is not None]


def insights(history: list[dict]) -> list[dict]:
    """history: the pilot's flights, oldest first, each
       {id, label, rules: {title: verdict}, causes: {code: count}, breaches: n, scan: scan_stats() | None}
    -> [{kind: improving|persistent|regression|cause|scan|info, text, evidence}]"""
    out: list[dict] = []
    if not history:
        return out
    titles = sorted({t for h in history for t in h.get("rules", {})})
    for title in titles:
        seq = [h["rules"][title] for h in history if h["rules"].get(title) in ("pass", "fail")]
        if len(seq) < 3:
            continue
        n_fail_early = seq[:-2].count("fail")
        if seq[-1] == "fail" and seq[-2] == "fail" or seq[-3:].count("fail") >= 2:
            out.append({"kind": "persistent", "text": f"Still breaking “{title}”: {seq[-3:].count('fail')} of the "
                        f"last 3 flights.", "evidence": {"rule": title, "sequence": seq}})
        elif seq[-1] == "pass" and seq[-2] == "pass" and n_fail_early:
            out.append({"kind": "improving", "text": f"Improved on “{title}”: met in the last 2 flights after "
                        f"breaking it in {n_fail_early} of the {len(seq) - 2} before.", "evidence": {"rule": title, "sequence": seq}})
        elif seq[-1] == "fail" and seq[-3:-1] == ["pass", "pass"]:
            out.append({"kind": "regression", "text": f"“{title}” was met in the 2 flights before, but broken again "
                        "in the latest one.", "evidence": {"rule": title, "sequence": seq}})

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
                        f"{CAUSES[code]}, in {n} of {breaches} rule breaches.",
                        "evidence": {"code": code, "count": n, "breaches": breaches}})

    for key, what, unit in (("airspeed_check_every_s", "checking the airspeed on base and final", "s"),):
        v = _vals(history, key)
        if len(v) >= 3:
            half = len(v) // 2
            early = sum(x for _, x in v[:half]) / half
            late = sum(x for _, x in v[-half:]) / half
            if early and abs(late - early) / early >= 0.2:
                better = late < early
                out.append({"kind": "scan", "text": f"{'More' if better else 'Less'} often {what}: about every "
                            f"{late:.1f} {unit} lately, was {early:.1f} {unit}.",
                            "evidence": {"metric": key, "early": round(early, 1), "late": round(late, 1), "better": better}})
    outside = [(h, h["scan"]["airborne"]["share"].get("outside")) for h in history
               if (h.get("scan") or {}).get("airborne") and h["scan"]["airborne"]["share"].get("outside") is not None]
    if len(outside) >= 3:
        half = len(outside) // 2
        early = sum(x for _, x in outside[:half]) / half
        late = sum(x for _, x in outside[-half:]) / half
        if abs(late - early) >= 0.1:
            out.append({"kind": "scan", "text": f"Time spent looking outside while airborne: {late:.0%} lately, "
                        f"was {early:.0%}.", "evidence": {"metric": "outside_share", "early": round(early, 3),
                                                          "late": round(late, 3)}})
    if not out:
        out.append({"kind": "info", "text": "Not enough flights with rule checks yet to see a trend (needs 3+)."
                    if len(history) < 3 else "No clear trend in the rule checks yet.", "evidence": {}})
    return out


def flight_label(s: dict) -> str:
    ts = s.get("started_at") or s.get("created_at")
    when = ts.strftime("%d %b %H:%M") if isinstance(ts, datetime) else (str(ts)[:16] if ts else "")
    name = s.get("name") or ""
    lesson = name.rsplit(" – ", 1)[0] if " – " in name else (s.get("lesson_id") or name)
    return f"{when} · {lesson}".strip(" ·")
