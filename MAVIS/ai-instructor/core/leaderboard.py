"""Leaderboard store (design spec §14.6), in memory only.

The permanent all-time board lives in IRIS's Postgres (leaderboard_entries):
every posted run is published in state["leaderboard_posts"] and IRIS's
bridge (backend/app/mavis_bridge.py) saves it there. Nothing is written to
disk here. The boards this keeps -- runs posted since MAVIS started -- feed
MAVIS's own pop-up board (state["leaderboard"]).
"""
from __future__ import annotations

# Public boards: visitor / trainee / pilot. Exam runs are practice-only and never posted.
MODES = ("visitor", "trainee", "pilot")
_LEGACY_MODE = {"casual": "visitor", "serious": "pilot"}
SCOPES = ("session", "all")


class Leaderboard:
    def __init__(self):
        self.entries: list[dict] = []

    def add(self, entry: dict) -> None:
        """Post a completed run."""
        self.entries.append(entry)

    def posts(self) -> list[dict]:
        """Every run posted since MAVIS started, oldest first -- what IRIS's
        bridge saves to Postgres (it skips ones it already has)."""
        return list(self.entries)

    def _norm_mode(self, mode: str) -> str:
        return _LEGACY_MODE.get(mode, mode)

    def board(self, mode: str, limit: int = 50) -> list[dict]:
        want = self._norm_mode(mode)
        rows = [e for e in self.entries if self._norm_mode(e.get("mode", "")) == want]
        # highest total first; fewer retries breaks ties; earlier run wins next
        rows.sort(key=lambda e: (-e.get("total", 0), e.get("retries", 0), e.get("ts", 0)))
        return rows[:limit]

    def snapshot(self) -> dict:
        """Nested {mode: {scope: [rows]}} for pushing to the UI. Both scopes are
        this process's runs; the all-time board is IRIS's Leaderboard page."""
        return {m: {s: self.board(m) for s in SCOPES} for m in MODES}
