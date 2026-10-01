"""A correctly flown circuit, as ranges per phase -- used to catch rules that
would flag a trainee who flew exactly as the POH says.

Built from the lesson YAML (phase targets, pattern height) and the aircraft
profile (V-speeds, flap settings, abeam RPM). Engine/throttle ranges that
neither file states use typical light-trainer values (`_GENERIC`), and a
profile can override any phase/metric under `nominal:`:

    nominal:
      CLIMB_OUT: { rpm: [2400, 2550] }

A rule that this profile breaks is only *set aside* for the reviewer with an
explanation (never deleted): the profile is approximate, the reviewer decides.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from .schema import APP_ROOT, METRICS

FLIGHT_PHASES = ["PRE_TAKEOFF", "TAKEOFF_ROLL", "CLIMB_OUT", "CROSSWIND", "DOWNWIND",
                 "BASE", "FINAL", "FLARE_TOUCHDOWN", "ROLLOUT"]

# Engine / throttle / vertical speed / bank ranges of a normal circuit in a
# light single-engine trainer. Only used where the lesson + profile say nothing.
_GENERIC = {
    "PRE_TAKEOFF":     {"rpm": (700, 1800), "throttle": (0.0, 0.5), "vs": (0, 0), "bank": (0, 0)},
    "TAKEOFF_ROLL":    {"rpm": (2300, 2450), "throttle": (1.0, 1.0), "vs": (0, 0), "bank": (0, 0)},
    "CLIMB_OUT":       {"rpm": (2350, 2550), "throttle": (1.0, 1.0), "vs": (300, 900), "bank": (-15, 15)},
    "CROSSWIND":       {"rpm": (2350, 2550), "throttle": (1.0, 1.0), "vs": (0, 800), "bank": (-25, 25)},
    "DOWNWIND":        {"rpm": (1500, 2300), "throttle": (0.3, 0.8), "vs": (-200, 200), "bank": (-25, 25)},
    "BASE":            {"rpm": (1300, 1800), "throttle": (0.1, 0.5), "vs": (-800, 0), "bank": (-25, 25)},
    "FINAL":           {"rpm": (1000, 1800), "throttle": (0.05, 0.5), "vs": (-800, 0), "bank": (-15, 15)},
    "FLARE_TOUCHDOWN": {"rpm": (700, 1100), "throttle": (0.0, 0.1), "vs": (-300, 0), "bank": (-5, 5)},
    "ROLLOUT":         {"rpm": (700, 1100), "throttle": (0.0, 0.1), "vs": (0, 0), "bank": (0, 0)},
}

_PHASE_LABEL = {p: p.replace("_", " ").lower().replace("flare touchdown", "flare/touchdown").capitalize()
                for p in FLIGHT_PHASES}


def _load(path: Path) -> dict:
    try:
        return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _resolve(v, vs: dict):
    """Lesson targets may reference aircraft keys: "{vy}"."""
    if isinstance(v, str):
        m = re.fullmatch(r"\{(\w+)\}", v.strip())
        if m:
            return vs.get(m.group(1))
        try:
            return float(v)
        except ValueError:
            return None
    return v


def nominal_circuit(lesson: dict, aircraft: dict) -> dict[str, dict[str, tuple[float, float]]]:
    vs = dict(aircraft.get("v_speeds") or {})
    fl = aircraft.get("flaps") or {}
    tg = lesson.get("targets") or {}
    elev = float((lesson.get("airport") or {}).get("elevation_ft") or 0)
    pat = lesson.get("pattern") or {}
    agl = (aircraft.get("pattern") or {}).get("altitude_agl_ft") \
        or ((pat.get("altitude_msl_ft") or 1000 + elev) - elev)
    xw = float(pat.get("crosswind_turn_agl_ft") or agl * 0.6)

    def spd(phase, fallback):
        v = _resolve((tg.get(phase) or {}).get("speed"), vs)
        return float(v) if v is not None else fallback

    vr = float(vs.get("vr") or 55)
    vs0 = float(vs.get("vs0") or 40)
    vy = spd("CLIMB_OUT", float(vs.get("vy") or 74))
    dw = spd("DOWNWIND", float(vs.get("pattern_downwind") or 85))
    base = spd("BASE", float(vs.get("base") or dw - 10))
    fin = spd("FINAL", float(vs.get("final") or base - 10))
    f_dw, f_base, f_fin = float(fl.get("downwind") or 0), float(fl.get("base") or 0), float(fl.get("final") or 0)
    abeam = (aircraft.get("power") or {}).get("abeam_rpm")

    P = {
        "PRE_TAKEOFF":     {"ias": (0, 15), "alt_agl": (0, 0), "flaps_deg": (0, 10), "on_ground": (1, 1)},
        "TAKEOFF_ROLL":    {"ias": (0, vr), "alt_agl": (0, 0), "flaps_deg": (0, 10), "on_ground": (1, 1)},
        "CLIMB_OUT":       {"ias": (vy, vy), "alt_agl": (0, xw), "flaps_deg": (0, 0), "on_ground": (0, 0)},
        "CROSSWIND":       {"ias": (vy, vy), "alt_agl": (xw, agl), "flaps_deg": (0, 0), "on_ground": (0, 0)},
        "DOWNWIND":        {"ias": (min(base, dw), dw), "alt_agl": (agl, agl), "flaps_deg": (0, f_dw), "on_ground": (0, 0)},
        "BASE":            {"ias": (base, base), "alt_agl": (agl * 0.5, agl), "flaps_deg": (f_dw, f_base), "on_ground": (0, 0)},
        "FINAL":           {"ias": (fin, fin), "alt_agl": (30, agl * 0.5), "flaps_deg": (f_base, f_fin), "on_ground": (0, 0)},
        "FLARE_TOUCHDOWN": {"ias": (vs0, fin), "alt_agl": (0, 30), "flaps_deg": (f_fin, f_fin), "on_ground": (0, 1)},
        "ROLLOUT":         {"ias": (0, vs0 + 10), "alt_agl": (0, 0), "flaps_deg": (f_fin, f_fin), "on_ground": (1, 1)},
    }
    for ph, extra in _GENERIC.items():
        for k, rng in extra.items():
            P[ph].setdefault(k, rng)
    if abeam:
        lo, hi = P["DOWNWIND"]["rpm"]
        P["DOWNWIND"]["rpm"] = (min(lo, float(abeam)), hi)
    for ph, over in (aircraft.get("nominal") or {}).items():
        for k, rng in (over or {}).items():
            if ph in P and isinstance(rng, (list, tuple)) and len(rng) == 2:
                P[ph][k] = (float(rng[0]), float(rng[1]))
    for ph in P:
        a, b = P[ph]["alt_agl"]
        P[ph]["alt_msl"] = (a + elev, b + elev)
    P["_capture"] = {"liftoff": {"ias": (vr, vr)}, "touchdown": {"ias": (vs0, min(fin, vs0 + 20))}}
    return P


def load_nominal(aircraft_id: str | None, lesson_path: str | None = None) -> dict | None:
    lesson = _load(APP_ROOT / (lesson_path or "lessons/lesson01_pattern.yaml"))
    ac_file = APP_ROOT / "profiles" / "aircraft" / f"{aircraft_id}.yaml"
    if not lesson or not aircraft_id or not ac_file.is_file():
        return None
    return nominal_circuit(lesson, _load(ac_file))


# ---------------------------------------------------------------- checking
def _cond_possible(cond: dict, state: dict) -> bool:
    rng = state.get(cond.get("metric"))
    if rng is None:
        return True                     # unknown -> assume it can apply
    lo, hi = rng
    v, op = float(cond.get("value", 0)), cond.get("op")
    return {">=": hi >= v, ">": hi > v, "<=": lo <= v, "<": lo < v, "==": lo <= v <= hi}.get(op, True)


def _fmt(metric: str, v: float) -> str:
    u = METRICS.get(metric, ("", ""))[1]
    if u == "frac":
        return f"{round(v * 100)}%"
    if u == "bool":
        return "on ground" if v else "airborne"
    unit = {"kt": " kt", "ft": " ft", "ft/min": " ft/min", "deg": "°", "rpm": " RPM"}.get(u, "")
    return f"{v:g}{unit}"


def _rng(metric, lo, hi):
    return _fmt(metric, lo) if lo == hi else f"{_fmt(metric, lo)}–{_fmt(metric, hi)}"


def conflicts(rule: dict, nominal: dict | None) -> list[str]:
    """Plain-English reasons a correctly flown circuit breaks this rule ([] = fine)."""
    if not nominal or rule.get("kind") in (None, "unobservable") or not rule.get("metric"):
        return []
    m = rule["metric"]
    f = lambda k: None if rule.get(k) is None else float(rule[k])  # noqa: E731
    out: list[str] = []
    if rule["kind"] == "capture":
        at = nominal.get("_capture", {}).get(rule.get("capture_at") or "")
        rng = (at or {}).get(m)
        if rng:
            lo, hi = rng
            mn, mx = f("min_value"), f("max_value")
            if (mn is not None and lo < mn) or (mx is not None and hi > mx):
                out.append(f"at {rule['capture_at']} a correct flight has {_rng(m, lo, hi)}")
        return out
    phases = [p for p in (rule.get("phases") or FLIGHT_PHASES) if p in nominal]
    if rule["kind"] == "event":
        g = rule.get("gate_phase")
        if g in nominal and m in nominal[g]:
            lo, hi = nominal[g][m]
            v, op = f("value"), rule.get("op")
            ok = {">=": hi >= v, ">": hi > v, "<=": lo <= v, "<": lo < v, "==": lo <= v <= hi}.get(op, True)
            if not ok:
                out.append(f"in {_PHASE_LABEL[g]} a correct flight has {_rng(m, lo, hi)}")
        return out
    for ph in phases:
        st = nominal[ph]
        if m not in st or not all(_cond_possible(c, st) for c in rule.get("conditions") or []):
            continue
        lo, hi = st[m]
        if rule["kind"] == "limit":
            mn, mx = f("min_value"), f("max_value")
            if (mx is not None and hi > mx) or (mn is not None and lo < mn):
                out.append(f"in {_PHASE_LABEL[ph]} a correct flight has {_rng(m, lo, hi)}")
        elif rule["kind"] == "band":
            t, th, tl = f("target"), f("tol_hi"), f("tol_lo")
            if t is None or (th is None and tl is None):
                continue                           # no tolerance yet -- blocked elsewhere
            if hi > t + (th or 0) or lo < t - (tl or 0):
                out.append(f"in {_PHASE_LABEL[ph]} a correct flight has {_rng(m, lo, hi)}")
    return out


def conflict_message(reasons: list[str], aircraft: str | None = None) -> str | None:
    if not reasons:
        return None
    more = f" (and {len(reasons) - 2} more phase{'s' if len(reasons) > 3 else ''})" if len(reasons) > 2 else ""
    who = f"A correctly flown circuit in the {aircraft}" if aircraft else "A correctly flown circuit"
    return f"{who} would break this rule: " + "; ".join(reasons[:2]) + more + \
        ". It is probably meant for a different situation (emergency, autopilot, ground check...) or needs narrower phases/conditions."


def nominals_all() -> list[tuple[str, dict]]:
    """(aircraft name, correct circuit) for every aircraft profile -- handbook
    rules apply to all of them, so they are checked against each one."""
    from .schema import aircraft_profiles
    out = []
    for a in aircraft_profiles():
        n = load_nominal(a["id"])
        if n is not None:
            out.append((a["name"], n))
    return out


def circuit_conflict(rule: dict, nominal) -> str | None:
    """nominal: one circuit dict (aircraft rules) or a list of (name, circuit)
    (handbook rules: the first aircraft whose correct circuit breaks the rule)."""
    if nominal is None:
        return None
    if isinstance(nominal, dict):
        return conflict_message(conflicts(rule, nominal))
    for name, n in nominal:
        msg = conflict_message(conflicts(rule, n), name)
        if msg:
            return msg
    return None
