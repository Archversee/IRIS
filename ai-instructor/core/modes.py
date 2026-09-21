"""Training-mode policies loaded from profiles/modes.yaml."""
from __future__ import annotations
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent.parent
MODES_PATH = HERE / "profiles" / "modes.yaml"

# Legacy menu ids → current ids
_ALIASES = {"casual": "visitor", "serious": "pilot"}

MODE_IDS = ("visitor", "trainee", "pilot", "exam")

# Modes whose scores appear on the public leaderboard (exam is practice-only)
LEADERBOARD_MODES = ("visitor", "trainee", "pilot")

_DEFAULTS = {
    "label": "Visitor",
    "expected_jargon": "layman",
    "force_repeat": False,
    "coaching": "turn_by_turn",
    "guidance": "direct",
    "chat": True,
    "score_caps": False,
    "auto_practice_summary": False,
}

_cache: dict | None = None


def _load() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    raw = yaml.safe_load(MODES_PATH.read_text(encoding="utf-8")) or {}
    _cache = {k: {**_DEFAULTS, **(v or {})} for k, v in raw.items()}
    return _cache


def normalize_mode(mode: str) -> str:
    m = (mode or "visitor").strip().lower()
    m = _ALIASES.get(m, m)
    return m if m in MODE_IDS else "visitor"


def policy_for(mode: str) -> dict:
    """Return a shallow copy of the policy for mode (after alias normalize)."""
    mid = normalize_mode(mode)
    modes = _load()
    p = dict(modes.get(mid, _DEFAULTS))
    p["id"] = mid
    return p


def expected_jargon(mode: str) -> str:
    j = policy_for(mode).get("expected_jargon", "layman")
    return "pilot" if j == "pilot" else "layman"


def allow_coach_event(policy: dict, code: str, severity: str) -> bool:
    """Filter CoachEvents by coaching level before they enter the speak queue.

    turn_by_turn  Visitor: every callout (PHASE_ENTER, TURN_*, nudges, …)
    reactive      Trainee/Pilot: prepared flyers; no scripted guidance.
                   Only take-off clearance, end debrief cue, safety, and the
                   lesson's own teaching points (LESSON_NOTE, e.g. night cues).
    silent        Exam: safety-only
    """
    level = policy.get("coaching", "turn_by_turn")
    if level == "silent":
        return severity == "safety"
    if level == "reactive":
        if severity == "safety":
            return True
        return code in ("PHASE_ENTER_TAKEOFF_ROLL", "PHASE_ENTER_DEBRIEF", "LESSON_NOTE")
    # turn_by_turn: everything (including TURN_*)
    return True
