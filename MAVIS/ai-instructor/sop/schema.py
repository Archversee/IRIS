"""Vocabulary shared by every stage of the SOP -> rules pipeline.

Everything the LLM is allowed to say is enumerated here, so the JSON schema
Ollama is constrained with, the validator, the API and the wizard all agree.
Rule kinds follow docs/acs-guided-instruction.md §3.2 (band / event /
capture / unobservable) plus `limit`, because SOPs are mostly one-sided
limits ("do not exceed 85 kt with full flaps") rather than target±tol, and
`gaze` (where the pilot looks, from the eye tracker) for training handbooks.

All stored numbers are in MAVIS Telemetry units (adapters/sim_base.py).
"""
from __future__ import annotations

from pathlib import Path

import yaml

APP_ROOT = Path(__file__).resolve().parent.parent

KINDS = ("band", "limit", "event", "capture", "gaze", "unobservable")
OPS = (">=", "<=", "==", ">", "<")
CAPTURE_AT = ("liftoff", "touchdown", "phase_entry", "phase_exit")
SEVERITIES = ("advisory", "caution", "warning")
REVIEW_STATUSES = ("draft", "approved", "rejected", "auto_rejected")

# metric -> (dimension, stored unit, plausible [lo, hi], layman label)
METRICS: dict[str, tuple[str, str, tuple[float, float], str]] = {
    "ias":       ("speed",     "kt",     (0, 400),       "Indicated airspeed"),
    "alt_msl":   ("altitude",  "ft",     (-1500, 60000), "Altitude (MSL)"),
    "alt_agl":   ("altitude",  "ft",     (0, 60000),     "Height above ground"),
    "vs":        ("vspeed",    "ft/min", (-10000, 10000), "Vertical speed"),
    "hdg":       ("heading",   "deg",    (0, 360),       "Heading (true)"),
    "bank":      ("angle",     "deg",    (-90, 90),      "Bank angle"),
    "pitch":     ("angle",     "deg",    (-90, 90),      "Pitch attitude"),
    "flaps_deg": ("angle",     "deg",    (0, 60),        "Flap angle"),
    "rpm":       ("rpm",       "rpm",    (0, 10000),     "Engine RPM"),
    "throttle":  ("fraction",  "frac",   (0, 1),         "Throttle position"),
    "on_ground": ("bool",      "bool",   (0, 1),         "On ground (1) / airborne (0)"),
}

# units the LLM may report, grouped by dimension; factor converts to stored unit
UNITS: dict[str, tuple[str, float]] = {
    "kt": ("speed", 1.0), "km/h": ("speed", 0.539957), "mph": ("speed", 0.868976),
    "ft": ("altitude", 1.0), "m": ("altitude", 3.28084),
    "fpm": ("vspeed", 1.0), "m/s": ("vspeed", 196.850),
    "deg": ("angle", 1.0),
    "rpm": ("rpm", 1.0),
    "percent": ("fraction", 0.01), "fraction": ("fraction", 1.0),
    "s": ("time", 1.0),
    "none": ("", 1.0),
}

# ---------------------------------------------------------------- gaze rules
# kind "gaze": where the pilot looks, measured by the eye tracker. A rule
# points at an AOI *role*, not at a zone's free-text name: each AOI zone drawn
# in IRIS gets one of these roles, so a rule works whatever the zone is called.
AOI_ROLES: dict[str, tuple[str, str]] = {
    # id            (label shown in the UI,        meaning given to the LLM)
    "outside":     ("Outside view",               "looking out of the windscreen/windows: lookout, horizon, runway, traffic"),
    "instruments": ("Any instrument (panel)",     "the instrument panel as a whole: any time spent looking inside"),
    "attitude":    ("Attitude indicator",         "attitude indicator / artificial horizon"),
    "airspeed":    ("Airspeed indicator",         "airspeed indicator"),
    "altimeter":   ("Altimeter",                  "altimeter"),
    "vsi":         ("Vertical speed indicator",   "vertical speed indicator"),
    "heading":     ("Heading indicator",          "heading indicator / directional gyro / compass"),
    "turn":        ("Turn coordinator",           "turn coordinator / turn-and-slip indicator"),
    "engine":      ("Engine instruments",         "tachometer and other engine gauges"),
}
# measure -> (label, value dimension, meaning given to the LLM)
GAZE_MEASURES: dict[str, tuple[str, str, str]] = {
    "max_gap":   ("Look at it at least every",   "time",     "omission: longest time WITHOUT looking at the area, seconds"),
    "max_dwell": ("No single look longer than",  "time",     "fixation: longest single uninterrupted look at the area, seconds"),
    "min_share": ("At least this share of time", "fraction", "share of the phase spent looking at the area, at least N percent"),
    "max_share": ("At most this share of time",  "fraction", "emphasis: share of the phase spent looking at the area, at most N percent"),
}
GAZE_RANGE = {"time": (0.1, 600.0), "fraction": (0.0, 1.0)}


def gaze_unit_ok(unit: str, measure: str) -> bool:
    return UNITS.get(unit, ("?", 0))[0] == GAZE_MEASURES[measure][1]


# ---------------------------------------------------------------- source documents
# poh:      an aircraft's POH / AFM / type SOP -> rules for THAT aircraft
# handbook: a training handbook (FAA or the school's own) -> rules for EVERY aircraft
DOC_TYPES = {"poh": "Aircraft POH / SOP", "handbook": "Training handbook"}
# handbook id -> (label, short name, (document-number regex, title regex) found in the PDF text)
BOOKS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "afh":    ("Airplane Flying Handbook (FAA-H-8083-3C)", "FAA Airplane Flying Handbook",
               (r"8083-3[a-c]?\b", r"airplane flying handbook")),
    "ifh":    ("Instrument Flying Handbook (FAA-H-8083-15B)", "FAA Instrument Flying Handbook",
               (r"8083-15[a-b]?\b", r"instrument flying handbook")),
    "phak":   ("Pilot's Handbook of Aeronautical Knowledge (FAA-H-8083-25C)", "FAA Pilot's Handbook (PHAK)",
               (r"8083-25[a-c]?\b", r"pilot.s handbook of aeronautical knowledge")),
    "school": ("Flight school handbook / SOP", "School handbook", ()),
    "other":  ("Other training handbook", "Training handbook", ()),
}
# FAA books that hold (almost) no circuit rules -- recognised so the upload can say so
# id -> ((document-number regex, title regex), what to tell the reviewer)
NOT_RULE_BOOKS: dict[str, tuple[tuple[str, str], str]] = {
    "iph":     ((r"8083-16[a-b]?\b", r"instrument procedures handbook"),
                "the Instrument Procedures Handbook (IFR departures, charts and approaches) - it has almost no rules for a "
                "visual circuit. The scan-path book is the Instrument FLYING Handbook (FAA-H-8083-15B)."),
    "weather": ((r"8083-28[a-b]?\b", r"aviation weather handbook"),
                "the Aviation Weather Handbook - it explains weather but contains no flying rules."),
}
# dimensions that share the "deg" unit
_DEG_DIMS = {"angle", "heading"}

# ---------------------------------------------------------------- rule tags
# What a rule is ABOUT. 1-3 per rule. The LLM proposes them; validate.py
# always adds the tag implied by the rule's metric, so filtering is reliable.
TAGS: dict[str, tuple[str, str]] = {
    # id                (label shown in the UI,  meaning given to the LLM)
    "flight_telemetry": ("Flight telemetry", "how the aircraft is flying: airspeed, altitude, climb/descent rate, attitude, heading"),
    "pilot_controls":   ("Pilot controls",   "what the pilot sets or moves: throttle, flaps, yoke/stick, rudder, brakes, trim"),
    "engine":           ("Engine",           "engine and power: RPM, power setting, mixture, engine start/run-up"),
    "aircraft_limit":   ("Aircraft limit",   "a hard limitation of the aircraft (never-exceed, max flap speed, max RPM)"),
    "procedure":        ("Procedure",        "a checklist item or step in a normal procedure"),
    "configuration":    ("Configuration",    "how the aircraft must be configured for a phase (flaps set, lights on, mixture rich)"),
    "communication":    ("Communication",    "radio calls, ATC, transponder codes"),
    "situational":      ("Lookout & awareness", "lookout, traffic, terrain, orientation, weather decisions"),
    "scan":             ("Scan path",        "where the pilot looks: instrument cross-check, fixation/omission/emphasis, time outside vs inside"),
    "emergency":        ("Emergency",        "emergency or abnormal situation (engine failure, fire, forced landing)"),
}
# tag implied by each measurable metric (added automatically)
METRIC_TAG = {
    "ias": "flight_telemetry", "alt_msl": "flight_telemetry", "alt_agl": "flight_telemetry",
    "vs": "flight_telemetry", "hdg": "flight_telemetry", "bank": "flight_telemetry",
    "pitch": "flight_telemetry", "on_ground": "flight_telemetry",
    "flaps_deg": "pilot_controls", "throttle": "pilot_controls",
    "rpm": "engine",
}

# Quantities an SOP often states that MAVIS telemetry CANNOT measure. A number
# followed by one of these units must never become a rule on another metric
# (e.g. "Oil pressure 20 PSI" must not turn into "RPM >= 20").
FOREIGN_UNITS: dict[str, str] = {
    r"psi": "pressure", r"in\.?\s?hg": "manifold/vacuum pressure", r"°\s?[fc]\b|deg(?:rees)?\s?[fc]\b": "temperature",
    r"gph|gal(?:lon)?s?\b|liters?\b|litres?\b": "fuel quantity/flow", r"lbs?\b|pounds?\b|kg\b": "weight",
    r"amps?\b|volts?\b": "electrical", r"mhz|khz": "radio frequency", r"hours?\b|hrs\b|minutes?\b": "time",
    r"nm\b|nautical miles?|statute miles?|\bsm\b": "distance", r"%\s?mac|inches aft": "weight & balance",
}


def unit_ok(unit: str, metric: str) -> bool:
    dim = METRICS[metric][0]
    if dim == "bool":
        return unit == "none"
    udim = UNITS.get(unit, ("?", 0))[0]
    return udim == dim or (unit == "deg" and dim in _DEG_DIMS)


def to_stored(value: float | None, unit: str) -> float | None:
    if value is None:
        return None
    return float(value) * UNITS.get(unit, ("", 1.0))[1]


_FALLBACK_PHASES = ["BRIEFING", "PRE_TAKEOFF", "TAKEOFF_ROLL", "CLIMB_OUT", "CROSSWIND",
                    "DOWNWIND", "BASE", "FINAL", "FLARE_TOUCHDOWN", "ROLLOUT", "DEBRIEF"]


def known_phases() -> list[str]:
    """Union of the phases declared by every YAML lesson, in first-seen order
    (falls back to the phase detector's own order)."""
    seen: list[str] = []
    for p in sorted((APP_ROOT / "lessons").glob("*.yaml")):
        try:
            for ph in (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("phases", []):
                if ph not in seen:
                    seen.append(str(ph))
        except Exception:
            continue
    return seen or list(_FALLBACK_PHASES)


def aircraft_profiles() -> list[dict]:
    out = []
    for p in sorted((APP_ROOT / "profiles" / "aircraft").glob("*.yaml")):
        try:
            d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:
            d = {}
        out.append({"id": p.stem, "name": d.get("name", p.stem), "profile": d})
    return out


def llm_json_schema(phases: list[str], gaze: bool = False) -> dict:
    """JSON schema handed to Ollama's `format` -- decoding is constrained to it,
    so the model cannot emit an unknown metric, phase or kind. gaze=True (the
    handbook prompt) adds the "gaze" kind and its aoi / gaze_measure fields."""
    num = {"type": ["number", "null"]}
    phase_or_null = {"type": ["string", "null"], "enum": phases + [None]}
    kinds = list(KINDS) if gaze else [k for k in KINDS if k != "gaze"]
    gaze_props = {
        "aoi": {"type": ["string", "null"], "enum": list(AOI_ROLES) + [None]},
        "gaze_measure": {"type": ["string", "null"], "enum": list(GAZE_MEASURES) + [None]},
    } if gaze else {}
    return {
        "type": "object",
        "properties": {
            "rules": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": kinds},
                        **gaze_props,
                        "title": {"type": "string"},
                        "phases": {"type": "array", "items": {"type": "string", "enum": phases}},
                        "metric": {"type": ["string", "null"], "enum": list(METRICS) + [None]},
                        "unit": {"type": "string", "enum": list(UNITS)},
                        "target": num, "tol_plus": num, "tol_minus": num,
                        "min": num, "max": num,
                        "op": {"type": ["string", "null"], "enum": list(OPS) + [None]},
                        "value": num,
                        "gate_phase": phase_or_null,
                        "capture_at": {"type": ["string", "null"], "enum": list(CAPTURE_AT) + [None]},
                        "when": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "metric": {"type": "string", "enum": list(METRICS)},
                                    "op": {"type": "string", "enum": list(OPS)},
                                    "value": {"type": "number"},
                                    "unit": {"type": "string", "enum": list(UNITS)},
                                },
                                "required": ["metric", "op", "value", "unit"],
                            },
                        },
                        "severity": {"type": "string", "enum": list(SEVERITIES)},
                        "tags": {"type": "array", "items": {"type": "string", "enum": list(TAGS)},
                                 "minItems": 1, "maxItems": 3},
                        "source_quote": {"type": "string"},
                        "page": {"type": "integer"},
                    },
                    "required": ["kind", "title", "phases", "metric", "unit", "severity", "tags",
                                 "source_quote", "page"],
                },
            }
        },
        "required": ["rules"],
    }
