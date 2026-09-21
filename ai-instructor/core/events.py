"""Coaching events: the structured 'what to say' produced by the lesson
engine and monitors; the Coach turns them into gentle speech."""
from __future__ import annotations
import time
from dataclasses import dataclass, field

SEVERITY = ("info", "nudge", "correction", "safety")


@dataclass
class CoachEvent:
    code: str                 # e.g. ALT_HIGH, PHASE_ENTER_DOWNWIND
    severity: str = "info"
    data: dict = field(default_factory=dict)
    t: float = field(default_factory=time.time)
    cooldown_s: float = 20.0  # per-code re-fire suppression

    @property
    def priority(self) -> int:
        return SEVERITY.index(self.severity)
