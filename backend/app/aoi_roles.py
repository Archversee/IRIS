"""Which instrument an AOI zone is, from the name it was given in the AOI
editor ("Airspeed", "ASI", "IAS" -> airspeed). Zone names stay free text;
this is how analysis knows that a speed rule goes with the airspeed zone.

Role ids match MAVIS's SOP gaze rules (sop/schema.py AOI_ROLES), so the
same names serve both.
"""
from __future__ import annotations

import re

ROLE_LABELS = {
    "outside": "Outside view",
    "instruments": "Instrument panel",
    "attitude": "Attitude indicator",
    "airspeed": "Airspeed indicator",
    "altimeter": "Altimeter",
    "vsi": "Vertical speed indicator",
    "heading": "Heading indicator",
    "turn": "Turn coordinator",
    "engine": "Engine instruments",
}

# role -> words / abbreviations that mean it (whole words, case-insensitive),
# most specific roles first
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
    """'Airspeed' -> 'airspeed'; None when the name doesn't say which instrument."""
    if not name:
        return None
    for role, rx in _COMPILED:
        if rx.search(name.replace("_", " ").replace("-", " ")):
            return role
    return None


# Which instruments show each measured value -- "was the pilot looking at
# the instrument that would have shown this going wrong?". The first role
# is the main one; attitude / outside are also relevant for pitch & bank.
METRIC_ROLES: dict[str, tuple[str, ...]] = {
    "ias": ("airspeed",),
    "alt_msl": ("altimeter",),
    "alt_agl": ("altimeter", "outside"),
    "vs": ("vsi", "altimeter"),
    "bank": ("attitude", "turn", "outside"),
    "pitch": ("attitude", "outside"),
    "hdg": ("heading",),
    "rpm": ("engine",),
    "throttle": ("engine",),
    "flaps_deg": ("outside",),
}
