"""Suggested numbers for rules whose source gives none.

An LLM-drafted rule often has everything except the one number an instructor
has to supply: "Best rate-of-climb 74 KIAS" (how far off is still fine?),
"check the airspeed periodically" (how often?). Such a rule is set aside until
a person fills the number in. For the common cases the FAA publishes one, and
it is offered here -- PRE-FILLED on the set-aside rule, clearly marked as coming
from the FAA and not from the uploaded document. The reviewer accepts it (adds
the rule back), changes it, or leaves the rule aside. Nothing suggested is ever
used in a lesson without that step.

Sources (checked October 2026):
  * FAA-S-ACS-6C, Private Pilot - Airplane Airman Certification Standards:
    the tolerances an applicant must hold on the practical test.
      PA.III.B  Traffic Patterns            pattern altitude +/-100 ft, airspeed +/-10 kt
      PA.IV.A   Normal Takeoff and Climb    VY +10/-5 kt
      PA.IV.B   Normal Approach and Landing approach speed +10/-5 kt
      PA.IV.N   Go-Around/Rejected Landing  VY +10/-5 kt
      PA.VIII   Basic Instrument Maneuvers  altitude +/-200 ft, heading +/-20 deg, airspeed +/-10 kt
      PA.IX.A   Emergency Descent           airspeed +0/-10 kt
      PA.IX.B   Emergency Approach and Landing (Simulated)   best glide airspeed +/-10 kt
  * Aeronautical Information Manual 8-1-6 c.3 (Scanning for Other Aircraft):
    "no more than 4 to 5 seconds on the instrument panel for every 16 seconds outside".

These are CHECKRIDE standards for a private pilot. A school may train to
tighter or looser ones; the school's own handbook, uploaded as a training
handbook, takes the place of these suggestions wherever it gives a number.
"""
from __future__ import annotations

import re

from .schema import clean_lesson_types

ACS = "FAA Private Pilot ACS (FAA-S-ACS-6C)"
AIM = "FAA Aeronautical Information Manual 8-1-6"

_CLIMB = {"TAKEOFF_ROLL", "CLIMB_OUT"}
_LANDING = {"FINAL", "FLARE_TOUCHDOWN"}
_DESCENT_RX = re.compile(r"emergency\s+descent|rapid\s+descent", re.I)
_GLIDE_RX = re.compile(r"glide|engine\s+fail|without\s+engine\s+power|no\s+power|forced\s+landing", re.I)


def _tol(hi: float, lo: float, unit: str, task: str, exact: bool = True) -> dict:
    shown = f"±{hi:g} {unit}" if hi == lo else f"+{hi:g} / −{lo:g} {unit}"
    return {"fields": {"tol_hi": float(hi), "tol_lo": float(lo)}, "what": "Tolerance", "shown": shown,
            "source": f"{ACS}, {task}", "exact": exact}


def suggest(rule: dict) -> dict | None:
    """A suggested number for a rule that lacks one, or None.

    -> {"fields": {...values to fill in...}, "what": "Tolerance" | "Threshold",
        "shown": "+10 / −5 kt", "source": "...", "exact": bool}
    exact=False: the FAA standard is for a neighbouring case (e.g. best glide
    speed, offered for another emergency speed) -- said so in the text."""
    kind, metric = rule.get("kind"), rule.get("metric")
    lt = clean_lesson_types(rule.get("lesson_types"))
    phases = set(rule.get("phases") or [])
    said = f"{rule.get('title') or ''} {(rule.get('source_quote') or '')[:200]}"

    if kind == "band" and rule.get("tol_hi") is None and rule.get("tol_lo") is None:
        if metric == "ias":
            if "emergency" in lt:
                if _DESCENT_RX.search(said):
                    return _tol(0, 10, "kt", "PA.IX.A Emergency Descent")
                return _tol(10, 10, "kt", "PA.IX.B Emergency Approach and Landing (best glide airspeed)",
                            exact=bool(_GLIDE_RX.search(said)))
            if lt == ["instrument"]:
                return _tol(10, 10, "kt", "PA.VIII Basic Instrument Maneuvers")
            if rule.get("ref_speed") in ("vy", "vx") or (phases and phases <= _CLIMB):
                return _tol(10, 5, "kt", "PA.IV.A Normal Takeoff and Climb (VY)")
            if rule.get("ref_speed") == "approach" or (phases and phases <= _LANDING):
                return _tol(10, 5, "kt", "PA.IV.B Normal Approach and Landing")
            return _tol(10, 10, "kt", "PA.III.B Traffic Patterns", exact=bool(phases))
        if metric in ("alt_agl", "alt_msl"):
            if "emergency" in lt:
                return None
            if lt == ["instrument"]:
                return _tol(200, 200, "ft", "PA.VIII Basic Instrument Maneuvers")
            return _tol(100, 100, "ft", "PA.III.B Traffic Patterns (pattern altitude)")
        if metric == "hdg" and lt == ["instrument"]:
            return _tol(20, 20, "°", "PA.VIII Basic Instrument Maneuvers")
        return None

    if kind == "gaze" and rule.get("value") is None and "normal" in lt:
        aoi, measure = rule.get("aoi"), rule.get("gaze_measure")
        why = f"{AIM} c.3: no more than 4 to 5 seconds on the instrument panel for every 16 seconds outside"
        if (aoi, measure) == ("outside", "max_gap"):       # never away from the outside view for longer than that
            return {"fields": {"value": 5.0}, "what": "Threshold", "shown": "5 s", "source": why, "exact": True}
        if (aoi, measure) == ("instruments", "max_dwell"):  # a single look inside no longer than that
            return {"fields": {"value": 5.0}, "what": "Threshold", "shown": "5 s", "source": why, "exact": True}
    return None


def describe(s: dict) -> str:
    """One line for the reviewer: what was filled in and where it comes from."""
    near = "" if s.get("exact", True) else " (the nearest FAA standard - there is none for exactly this)"
    return f"{s['what']} {s['shown']} is a suggestion from {s['source']}{near}, not from this document"


NOTE_PREFIX = "FAA suggestion: "


def note_with(note: str | None, s: dict) -> str:
    """The rule's note with the suggestion's origin added (kept with the rule
    after it is added back, and in exports)."""
    line = f"{NOTE_PREFIX}{s['what'].lower()} {s['shown']} from {s['source']}."
    kept = "\n".join(ln for ln in (note or "").splitlines() if not ln.startswith(NOTE_PREFIX)).strip()
    return (kept + "\n" + line).strip()[:2000]


def apply(rule: dict, s: dict | None = None) -> dict | None:
    """Fill the suggested number into the rule (in place). -> the suggestion used, or None."""
    s = s or suggest(rule)
    if not s:
        return None
    rule.update(s["fields"])
    rule["note"] = note_with(rule.get("note"), s)
    rule["suggested"] = {"fields": dict(s["fields"]), "what": s["what"], "shown": s["shown"], "source": s["source"],
                         "exact": s.get("exact", True)}
    return s
