"""Judging eye-tracking ("gaze") rules from the eye tracker's samples.

A gaze rule (sop/schema.py AOI_ROLES / GAZE_MEASURES) says where the pilot
should look, and how:

    max_gap    look at <role> at least every N s     (longest stretch WITHOUT a look)
    max_dwell  no single look at <role> longer than N s
    min_share  at least N % of the time on <role>
    max_share  at most N % of the time on <role>

over the phases it lists (every flying phase if none).

Judged once the flight is over, from what IRIS recorded: eye_tracking_data
(Smart Eye via LiveAdapter.py, on this PC's clock) and the AOI zones drawn on
the instrument panel (IRIS > Tools > AOI Zones). Each eye sample becomes the
ROLES it was on:

    aoi 'OTW'                               -> outside
    aoi 'Instruments', inside a drawn zone  -> that zone's role (from its name:
                                               'Airspeed' / 'ASI' -> airspeed) + instruments
    aoi 'Instruments', in no zone           -> instruments

The role names match IRIS's backend/app/aoi_roles.py (same patterns; keep
the two in step). Pure functions apart from load_eye(): samples in, results
out, in the same shape as sop/evaluate.py's, so they slot into the same
flight_evaluations / rule_results rows and Review shows them like any rule.

Honest about missing data: if the eye tracker saw the pilot for less than
MIN_COVERAGE of the rule's time the result is insufficient_data, and a stretch
with no samples never counts as "not looking".
"""
from __future__ import annotations

import re
from datetime import datetime

from .schema import AOI_ROLES

MIN_COVERAGE = 0.5        # share of the rule's time the eye tracker must have covered
SAMPLE_GAP_S = 1.0        # samples further apart than this: the time between is "no data"
LOOK_MERGE_S = 0.3        # a blink / glitch shorter than this doesn't end a look
NOT_FLYING = {"BRIEFING", "DEBRIEF"}

# role -> words / abbreviations in an AOI zone's name (as backend/app/aoi_roles.py)
_PATTERNS: list[tuple[str, str]] = [
    ("vsi", r"vsi|vvi|vertical\s*speed|v/?s\s*ind|rate\s*of\s*climb|roc|climb\s*rate"),
    ("airspeed", r"airspeed|air\s*speed|asi|ias|kias|speed"),
    ("altimeter", r"altimeter|altitude|alt|height"),
    ("attitude", r"attitude|adi|ai|artificial\s*horizon|horizon"),
    ("turn", r"turn\s*coordinator|turn\s*(and|&)\s*slip|tc|slip|turn"),
    ("heading", r"heading|hdg|hsi|dg|directional\s*gyro|compass"),
    ("engine", r"engine|tach|tachometer|rpm|manifold|oil|fuel|egt|cht|power"),
    ("outside", r"otw|outside|out\s*the\s*window|windscreen|windshield|window|horizon\s*view"),
    ("instruments", r"instruments?|panel|pfd|mfd|dash"),
]
_COMPILED = [(role, re.compile(rf"(?<![a-z0-9])(?:{pat})(?![a-z0-9])", re.I)) for role, pat in _PATTERNS]


def role_of(name: str | None) -> str | None:
    if not name:
        return None
    for role, rx in _COMPILED:
        if rx.search(name.replace("_", " ").replace("-", " ")):
            return role
    return None


def _ts(v) -> float:
    return v.timestamp() if isinstance(v, datetime) else float(v)


_PLAIN = {"outside": "outside view", "instruments": "instrument panel"}


def _label(role: str) -> str:
    return _PLAIN.get(role) or AOI_ROLES.get(role, (role,))[0].lower()


def _nice(phases) -> str:
    return ", ".join(p.replace("_", " ").lower() for p in phases)


def gaze_text(r: dict, mult: float = 1.0) -> str:
    """The requirement in a few words, e.g. 'airspeed indicator at least every 10 s'."""
    role, m, v = r.get("aoi"), r.get("gaze_measure"), r.get("value")
    if v is None or role is None:
        return ""
    what = _label(role)
    if m == "max_gap":
        return f"{what} at least every {float(v) * mult:g} s"
    if m == "max_dwell":
        return f"no look at the {what} longer than {float(v) * mult:g} s"
    if m == "min_share":
        return f"{what} at least {float(v) * 100:.0f}% of the time"
    if m == "max_share":
        return f"{what} at most {float(v) * 100:.0f}% of the time"
    return ""


# ---------------------------------------------------------------- samples -> roles
def sample_roles(e: dict, zones: list[dict]) -> set[str] | None:
    """The roles one eye sample was on; None = blink / not on anything known."""
    if e.get("blink") or not e.get("aoi"):
        return None
    if e["aoi"] == "OTW":
        return {"outside"}
    if e["aoi"] == "Instruments":
        x, y = e.get("gaze_point_x"), e.get("gaze_point_y")
        if x is not None and y is not None:
            for z in zones:
                if min(z["x1"], z["x2"]) <= x <= max(z["x1"], z["x2"]) and min(z["y1"], z["y2"]) <= y <= max(z["y1"], z["y2"]):
                    role = role_of(z["name"])
                    return {"instruments"} | ({role} if role else set())
        return {"instruments"}
    role = role_of(e["aoi"])
    return {role} if role else None


def timeline(eye: list[dict], zones: list[dict]) -> list[tuple[float, float, set[str] | None]]:
    """Eye samples -> [(t0, t1, roles)]: each sample covers up to the next one
    (at most SAMPLE_GAP_S); roles None = tracked but on nothing (blink)."""
    pts = sorted(((_ts(e["ts"]), sample_roles(e, zones)) for e in eye), key=lambda p: p[0])
    out = []
    for i, (t, roles) in enumerate(pts):
        nxt = pts[i + 1][0] if i + 1 < len(pts) else t + 0.05
        out.append((t, t + min(max(nxt - t, 0.0), SAMPLE_GAP_S), roles))
    return out


def phase_spans(samples: list[tuple[float, str | None]]) -> list[tuple[float, float, str]]:
    """[(t, phase)] in time order -> [(t0, t1, phase)] (consecutive same phase merged)."""
    spans: list[list] = []
    cur = None
    for t, ph in samples:
        if spans and cur is not None:
            spans[-1][1] = t                   # the phase lasts until this sample
        if ph != cur:
            cur = ph
            if ph:                             # None = no phase from here on (closes the last one)
                spans.append([t, t, ph])
    return [tuple(s) for s in spans if s[1] > s[0]]


# ---------------------------------------------------------------- judging
def _clip(tl, t0, t1):
    for a, b, roles in tl:
        a2, b2 = max(a, t0), min(b, t1)
        if b2 > a2:
            yield a2, b2, roles


def judge(rule: dict, spans: list[tuple[float, float, str]], tl: list, zones: list[dict],
          mult: float = 1.0) -> dict:
    """One gaze rule -> a result like sop/evaluate.py's."""
    role, measure = rule.get("aoi"), rule.get("gaze_measure")
    value = float(rule["value"]) if rule.get("value") is not None else None
    phases = [p for p in (rule.get("phases") or []) if p not in NOT_FLYING]
    scope = [s for s in spans if (s[2] in phases if phases else s[2] not in NOT_FLYING)]
    out = {"rule_id": rule.get("id"), "title": rule.get("title"), "kind": "gaze",
           "severity": rule.get("severity") or "caution", "metric": None, "phases": rule.get("phases") or [],
           "layer": rule.get("layer"), "requirement": gaze_text(rule, mult), "metrics": {}, "violations": [],
           "checks": [], "source_quote": rule.get("source_quote"), "source_page": rule.get("source_page")}

    def done(verdict, summary):
        return {**out, "verdict": verdict, "summary": summary}

    if role is None or measure is None or value is None:
        return done("insufficient_data", "The rule is missing where to look or the number.")
    if not spans:
        return done("not_applicable", "This flight has no lesson phases, so the rule can't be placed in time.")
    if not scope:
        flown = sorted({s[2] for s in spans if s[2] not in NOT_FLYING})
        return done("not_applicable", f"{_nice(phases).capitalize()} {'was' if len(phases) == 1 else 'were'} "
                                      f"never flown (this flight: {_nice(flown) or 'none'}).")
    if not tl:
        return done("insufficient_data", "No eye-tracker data was recorded for this flight.")
    if role not in ("outside", "instruments") and not any(role_of(z["name"]) == role for z in zones):
        return done("insufficient_data", f"No AOI zone is marked as the {_label(role)} "
                                         "(name a zone after it on the AOI Zones page).")

    total = sum(b - a for a, b, _ in scope)
    tracked = on = 0.0
    looks: list[list] = []                       # [t0, t1, phase] while on the role
    gaps: list[dict] = []                        # stretches without a look, on tracked time only
    for a, b, ph in scope:
        gap_t0, gap_len, last_end = a, 0.0, a
        for s0, s1, roles in _clip(tl, a, b):
            if s0 - last_end > SAMPLE_GAP_S:     # no data in between: a gap can't be judged across it
                gap_t0, gap_len = s0, 0.0
            last_end = s1
            tracked += s1 - s0
            if roles and role in roles:
                on += s1 - s0
                if looks and looks[-1][2] == ph and s0 - looks[-1][1] <= LOOK_MERGE_S:
                    looks[-1][1] = s1
                else:
                    looks.append([s0, s1, ph])
                if gap_len > 0:
                    gaps.append({"t0": gap_t0, "t1": s0, "dur": gap_len, "phase": ph})
                gap_t0, gap_len = s1, 0.0
            else:
                gap_len += s1 - s0
        if gap_len > 0:                          # still not looked when the phase ended
            gaps.append({"t0": gap_t0, "t1": last_end, "dur": gap_len, "phase": ph})

    coverage = tracked / total if total > 0 else 0.0
    where = _nice(phases) if phases else "the flight"
    out["metrics"] = {"time_in_scope_s": round(total, 1), "eye_coverage": round(coverage, 3),
                      "share": round(on / tracked, 3) if tracked else None}
    if coverage < MIN_COVERAGE:
        return done("insufficient_data", f"The eye tracker covered only {coverage * 100:.0f}% of {where}.")
    share = on / tracked
    what = _label(role)

    if measure in ("min_share", "max_share"):
        ok = share >= value if measure == "min_share" else share <= value
        need = f"needs {'at least' if measure == 'min_share' else 'at most'} {value * 100:.0f}%"
        return done("pass" if ok else "fail", f"Looked at the {what} {share * 100:.0f}% of {where} ({need}).")

    limit = value * mult
    if measure == "max_gap":
        worst = max(gaps, key=lambda g: g["dur"], default=None)
        bad = [g for g in gaps if g["dur"] > limit]
        out["violations"] = [{"t0": g["t0"], "t1": g["t1"], "dur_s": round(g["dur"], 1), "phase": g["phase"],
                              "peak": round(g["dur"], 1), "peak_text": f"{g['dur']:.0f} s without a look"} for g in bad]
        out["metrics"]["longest_gap_s"] = round(worst["dur"], 1) if worst else 0.0
        if bad:
            return done("fail", f"Went {worst['dur']:.0f} s without looking at the {what} "
                                f"({len(bad)} time{'s' if len(bad) != 1 else ''} over {limit:g} s).")
        return done("pass", f"Looked at the {what} at least every {limit:g} s"
                            f" (longest gap {worst['dur']:.0f} s)." if worst else f"Kept looking at the {what}.")
    if measure == "max_dwell":
        longest = max(looks, key=lambda x: x[1] - x[0], default=None)
        bad = [x for x in looks if x[1] - x[0] > limit]
        out["violations"] = [{"t0": x[0], "t1": x[1], "dur_s": round(x[1] - x[0], 1), "phase": x[2],
                              "peak": round(x[1] - x[0], 1), "peak_text": f"{x[1] - x[0]:.0f} s look"} for x in bad]
        out["metrics"]["longest_look_s"] = round(longest[1] - longest[0], 1) if longest else 0.0
        if bad:
            return done("fail", f"Stared at the {what} for {longest[1] - longest[0]:.0f} s "
                                f"({len(bad)} look{'s' if len(bad) != 1 else ''} over {limit:g} s).")
        return done("pass", f"No look at the {what} over {limit:g} s.")
    return done("insufficient_data", f"Unknown gaze check '{measure}'.")


def judge_all(rules: list[dict], spans: list, eye: list[dict], zones: list[dict], mult: float = 1.0) -> dict:
    """{rule_id: result} for the gaze rules among `rules`."""
    gaze = [r for r in rules if r.get("kind") == "gaze"]
    if not gaze:
        return {}
    tl = timeline(eye, zones)
    return {r.get("id"): judge(r, spans, tl, zones, mult) for r in gaze}


async def load_eye(conn, session_id, t0: float | None = None, t1: float | None = None) -> tuple[list[dict], list[dict]]:
    """(eye samples, AOI zones) for a session, optionally between two epoch times."""
    from datetime import timezone
    args, where = [session_id], "session_id=$1"
    if t0 is not None and t1 is not None:
        args += [datetime.fromtimestamp(t0 - 1, timezone.utc), datetime.fromtimestamp(t1 + 1, timezone.utc)]
        where += " and ts between $2 and $3"
    try:
        eye = await conn.fetch(f"select ts, aoi, gaze_point_x, gaze_point_y, blink from eye_tracking_data "
                               f"where {where} order by ts", *args)
    except Exception:                    # no eye table (MAVIS without IRIS)
        return [], []
    try:
        zones = await conn.fetch("select name, x1, y1, x2, y2 from aoi_zones")
    except Exception:
        zones = []
    return [dict(e) for e in eye], [dict(z) for z in zones]
