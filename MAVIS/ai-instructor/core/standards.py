"""Judging by level: how strict the scorecard is for each mode.

Every lesson's scoring block sets its own tolerances (`scoring.scales`: the
error that scores ~37/100). A level multiplies all of them: bigger is more
forgiving. Trainee is the lesson's own standard; Visitor is gentler; Pilot
(and Exam) is the check-ride standard. Every scorecard is also worked out at
the check-ride standard, so a Visitor or Trainee sees how the same flight
would grade there.
"""
from __future__ import annotations

LEVELS = {
    "visitor": 1.5,
    "trainee": 1.0,
    "pilot": 0.7,
    "exam": 0.7,
}
CHECKRIDE = 0.7
LABELS = {"visitor": "Visitor", "trainee": "Trainee", "pilot": "Check-ride", "exam": "Check-ride"}


def factor(mode: str | None) -> float:
    return LEVELS.get((mode or "").lower(), 1.0)


def label(mode: str | None) -> str:
    return LABELS.get((mode or "").lower(), "Trainee")
