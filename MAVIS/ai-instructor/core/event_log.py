"""Flight event log: start/end time of every graded event in a session.

An "event" is one step of the lesson's to-do list (familiarization: straight &
level, left turn, right turn, climb, descend, find the airfield) or one phase
of a circuit (take-off roll, climb-out, downwind, ... flare, rollout). The
boundaries are decided by the lesson engine itself: when the engine moves to
the next step the previous event ends at that instant and the next one starts
at the same instant, so events tile the flight with no gaps or overlaps.

The log only records. Scoring each event against the SOP rules, and the
analysis of why it scored as it did, happens downstream (IRIS): `score`,
`grade` and `detail` are left empty here for that purpose.

Each flight records which curriculum it came from (a lesson, or free flight), and
every event carries its flight number. Restart keeps the same session AND the same
flight: the attempt in progress ends as "restarted" and the next attempt's events
are try 2, 3... Fly again is a new session, which wipes the log (reset()).

Times are epoch seconds (the same clock as Telemetry.t, hence the same as
flight_data.ts in IRIS), so an event's [start_t, end_t] slices the flight
data directly.
"""
from __future__ import annotations

# Briefing and debrief are not flown, so they are not events.
NOT_EVENTS = ("BRIEFING", "DEBRIEF")


class EventLog:
    def __init__(self, labels: dict[str, str] | None = None, kind: str = "phase"):
        # labels: phase id -> display name. kind: "exercise" (familiarization
        # to-do step) or "phase" (circuit phase).
        self.labels = labels or {}
        self.kind = kind
        self.reset()

    def reset(self) -> None:
        """New session: forget every flight and event."""
        self._events: list[dict] = []
        self._open: dict | None = None
        self._flights: list[dict] = []
        self._flight: dict | None = None

    def configure(self, labels: dict[str, str], kind: str) -> None:
        self.labels, self.kind = labels, kind
        self.reset()

    # ---- flights ----
    def begin_flight(self, t: float, lesson_id: str, title: str, curriculum: str,
                     module: str = "", mode: str = "") -> int:
        """A flight starts (Start / Fly again). curriculum: "lesson" or
        "free_flight"; title is what the dashboard shows ("Flight Familiarization",
        "Free Flight"); module is the catalog group ("Basic Flight")."""
        self.end_flight(t, "abandoned")
        self._flight = {
            "flight": len(self._flights) + 1, "lesson_id": lesson_id, "title": title,
            "curriculum": curriculum, "module": module, "mode": mode,
            "start_t": t, "end_t": None, "outcome": None,
        }
        self._flights.append(self._flight)
        return self._flight["flight"]

    @property
    def in_flight(self) -> bool:
        return self._flight is not None

    def restart_attempt(self, t: float) -> None:
        """Restart mid-flight: the attempt in progress ends as "restarted"; the flight
        (and its session) carry on, and the next attempt's events count as try 2, 3..."""
        self.close(t, "restarted")

    def end_flight(self, t: float, outcome: str) -> None:
        """The flight ends (completed / abandoned / restarted); closes any open
        event with the same outcome. No-op when no flight is in progress."""
        if self._flight is None:
            return
        self.close(t, "completed" if outcome == "completed" else outcome)
        self._flight["end_t"] = t
        self._flight["outcome"] = outcome
        self._flight = None

    # ---- recording ----
    def observe(self, phase: str, t: float, circuit: int = 1,
                skipped: set[str] | frozenset = frozenset(), kind: str | None = None) -> bool:
        """Call every tick with the engine's current step. Returns True when
        the log changed (an event closed and/or opened)."""
        cur = self._open
        if cur is not None and cur["phase"] == phase and cur["circuit"] == circuit:
            return False
        changed = False
        if cur is not None:
            self._close(t, "skipped" if cur["phase"] in skipped else "completed")
            changed = True
        if phase not in NOT_EVENTS:
            flight = self._flight["flight"] if self._flight else 1
            attempt = 1 + sum(1 for e in self._events if e["flight"] == flight
                              and e["phase"] == phase and e["circuit"] == circuit)
            self._open = {
                "seq": len(self._events),
                "flight": flight, "event_id": phase,
                "label": self.labels.get(phase) or phase.replace("_", " ").title(),
                "kind": kind or self.kind, "phase": phase, "circuit": circuit,
                "attempt": attempt, "start_t": t, "end_t": None, "outcome": None,
                "score": None, "grade": None, "detail": None,
            }
            self._events.append(self._open)
            changed = True
        return changed

    def close(self, t: float, outcome: str = "abandoned") -> None:
        """End whatever event is open (lesson quit / aircraft frozen)."""
        if self._open is not None:
            self._close(t, outcome)

    def retried(self, t: float) -> None:
        """The trainee was teleported back to re-fly a step: the attempt in
        progress ends as 'retried' and the re-fly opens a new event (attempt
        n+1) on the next observe()."""
        self.close(t, "retried")

    def _close(self, t: float, outcome: str) -> None:
        self._open["end_t"] = t
        self._open["outcome"] = outcome
        self._open = None

    # ---- output ----
    def to_list(self) -> list[dict]:
        return [dict(e) for e in self._events]

    def flights(self) -> list[dict]:
        return [dict(f) for f in self._flights]
