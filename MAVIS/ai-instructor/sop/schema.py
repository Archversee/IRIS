"""Vocabulary shared by every stage of the SOP -> rules pipeline.

Everything the LLM is allowed to say is enumerated here, so the JSON schema
Ollama is constrained with, the validator, the API and the wizard all agree.
Rule kinds follow docs/acs-guided-instruction.md §3.2 (band / event /
capture / unobservable) plus `limit`, because SOPs are mostly one-sided
limits ("do not exceed 85 kt with full flaps") rather than target±tol.

All stored numbers are in MAVIS Telemetry units (adapters/sim_base.py).
"""
from __future__ import annotations

from pathlib import Path

import yaml

APP_ROOT = Path(__file__).resolve().parent.parent

KINDS = ("band", "limit", "event", "capture", "unobservable")
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
    "none": ("", 1.0),
}
# dimensions that share the "deg" unit
_DEG_DIMS = {"angle", "heading"}


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


def llm_json_schema(phases: list[str]) -> dict:
    """JSON schema handed to Ollama's `format` -- decoding is constrained to it,
    so the model cannot emit an unknown metric, phase or kind."""
    num = {"type": ["number", "null"]}
    phase_or_null = {"type": ["string", "null"], "enum": phases + [None]}
    return {
        "type": "object",
        "properties": {
            "rules": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": list(KINDS)},
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
                        "source_quote": {"type": "string"},
                        "page": {"type": "integer"},
                    },
                    "required": ["kind", "title", "phases", "metric", "unit", "severity",
                                 "source_quote", "page"],
                },
            }
        },
        "required": ["rules"],
    }
