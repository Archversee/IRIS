"""Leaderboard store (design spec §14.6). Session board is in-memory (wiped on
restart); all-time board persists to a small local JSON file. Fully local, no
network. Single-process app, so a plain file write is fine.
"""
from __future__ import annotations
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)
# Public boards: visitor / trainee / pilot. Exam runs are practice-only and never posted.
MODES = ("visitor", "trainee", "pilot")
_LEGACY_MODE = {"casual": "visitor", "serious": "pilot"}
SCOPES = ("session", "all")


class Leaderboard:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.session: list[dict] = []
        self.alltime: list[dict] = []
        self._load()

    def _load(self) -> None:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self.alltime = data
        except Exception as e:
            log.warning("leaderboard load failed (%s) - starting empty", e)
            self.alltime = []

    def add(self, entry: dict) -> None:
        """Post a completed run to both the session and all-time boards."""
        self.session.append(entry)
        self.alltime.append(entry)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.alltime, indent=0), encoding="utf-8")
        except Exception as e:
            log.warning("leaderboard save failed: %s", e)

    def _norm_mode(self, mode: str) -> str:
        return _LEGACY_MODE.get(mode, mode)

    def board(self, mode: str, scope: str, limit: int = 50) -> list[dict]:
        src = self.session if scope == "session" else self.alltime
        want = self._norm_mode(mode)
        rows = [e for e in src if self._norm_mode(e.get("mode", "")) == want]
        # highest total first; fewer retries breaks ties; earlier run wins next
        rows.sort(key=lambda e: (-e.get("total", 0), e.get("retries", 0), e.get("ts", 0)))
        return rows[:limit]

    def snapshot(self) -> dict:
        """Nested {mode: {scope: [rows]}} for pushing to the UI."""
        return {m: {s: self.board(m, s) for s in SCOPES} for m in MODES}

    def seed_demo_if_empty(self) -> None:
        """Populate showcase rows when no all-time scores exist yet.

        The last row is a real first-time flight (WSAP, 2026-09-16, scored with
        tools/score_log.py); the others are illustrative."""
        if self.alltime:
            return
        import time
        ts = time.time() - 86400 * 3
        demo = [
            {"name": "Jun Liang Chan", "given": "Jun Liang", "family": "Chan",
             "mode": "visitor", "lesson": "lesson01_pattern",
             "ts": ts, "scores": {"track": 88, "speed": 82, "altitude": 85, "landing": 79, "smoothness": 90},
             "retries": 2, "total": 84, "grade": "B", "capped": False},
            {"name": "Alex Tan", "given": "Alex", "family": "Tan",
             "mode": "visitor", "lesson": "lesson01_pattern",
             "ts": ts + 3600, "scores": {"track": 92, "speed": 88, "altitude": 90, "landing": 86, "smoothness": 88},
             "retries": 1, "total": 89, "grade": "A", "capped": False},
            {"name": "Sam Koh", "given": "Sam", "family": "Koh",
             "mode": "trainee", "lesson": "lesson01_pattern",
             "ts": ts + 7200, "scores": {"track": 78, "speed": 80, "altitude": 76, "landing": 72, "smoothness": 85},
             "retries": 1, "total": 76, "grade": "B", "capped": True},
            {"name": "Riya Menon", "given": "Riya", "family": "Menon",
             "mode": "pilot", "lesson": "lesson01_pattern",
             "ts": ts + 10800, "scores": {"track": 85, "speed": 84, "altitude": 88, "landing": 80, "smoothness": 82},
             "retries": 0, "total": 84, "grade": "B", "capped": False},
            {"name": "Chris Pang", "given": "Chris", "family": "Pang",
             "mode": "visitor", "lesson": "lesson01_pattern",
             "ts": ts + 14400, "scores": {"track": 70, "speed": 68, "altitude": 72, "landing": 65, "smoothness": 78},
             "retries": 3, "total": 70, "grade": "C", "capped": False},
            {"name": "Marc", "given": "Marc", "family": "",
             "mode": "visitor", "lesson": "lesson01_pattern",
             "ts": 1789543877.694451,
             "scores": {"track": 0, "speed": 0, "altitude": 0, "landing": 28, "smoothness": 100},
             "retries": 0, "total": 18, "grade": "F", "capped": False},
        ]
        self.alltime.extend(demo)
        self.session.extend(demo[:3])
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.alltime, indent=0), encoding="utf-8")
        except Exception as e:
            log.warning("leaderboard demo seed save failed: %s", e)
