"""Lesson state machine: owns phase progression, phase-entry callouts,
the announced day->night switch, and per-code event cooldowns."""
from __future__ import annotations
import logging
import time

log = logging.getLogger(__name__)

from adapters.sim_base import Telemetry
from adapters.input_hid import ControlSample
from .events import CoachEvent
from .monitors import Monitors
from .phase_detector import PhaseDetector


class LessonEngine:
    def __init__(self, lesson: dict, aircraft: dict, sim_adapter: str = "mock"):
        self.lesson = lesson
        self.detector = PhaseDetector(lesson, total_circuits=int(lesson.get("total_circuits", 1)))
        self.monitors = Monitors(aircraft, lesson, sim_adapter=sim_adapter)
        self.night = lesson.get("night_event", {})
        self.night_done = False
        # per-phase teaching points ({phase: {layman, pilot}}), spoken after the phase call
        self.phase_notes = lesson.get("phase_notes") or {}
        self.briefing_done = False
        self._last_fire: dict[str, float] = {}
        self.geom = None
        # where flying starts: the runway (PRE_TAKEOFF), or e.g. FINAL for the
        # landing lesson, which starts airborne on final approach
        self.start_phase = lesson.get("start_phase", "PRE_TAKEOFF")
        self._announce_start = False

    @property
    def phase(self) -> str: return self.detector.phase

    @property
    def circuit(self) -> int: return self.detector.circuit

    def start_flying(self):
        """Called once the briefing has been spoken (or presenter skips)."""
        self.briefing_done = True
        self.detector.advance(self.start_phase)
        # a lesson starting mid-circuit announces that leg (and its teaching
        # point) on the first tick, as if it had just been entered
        self._announce_start = self.start_phase != "PRE_TAKEOFF"

    def skip_phase(self):
        order = self.detector.ORDER
        i = order.index(self.detector.phase)
        if i < len(order) - 1:
            self.detector.advance(order[i + 1])

    def restart(self):
        self.detector.phase = "BRIEFING"
        self.detector.circuit = 1
        self.night_done = False
        self.briefing_done = False
        self._announce_start = False
        self._last_fire.clear()
        self.monitors.reset_turn_cues()

    def _cooled(self, ev: CoachEvent) -> bool:
        last = self._last_fire.get(ev.code, 0.0)
        if ev.t - last >= ev.cooldown_s:
            self._last_fire[ev.code] = ev.t
            return True
        return False

    def load_phase(self, phase: str, circuit: int):
        """Jump the lesson tracker to a checkpointed phase (teleport/retry)."""
        self.detector.advance(phase)
        self.detector.circuit = circuit
        self.detector._still_since = None
        self.monitors._rotate_called = False
        self.monitors._phase_prev = None       # fresh settle-in grace period
        self.monitors.reset_turn_cues()

    def wants_night_switch(self) -> bool:
        """Mid-flight day->night reveal. A lesson flown at night from the start
        (night_event.at == "start") sets its clock before the briefing instead."""
        if not self.night or self.night.get("at") == "start":
            return False
        return (not self.night_done
                and self.detector.circuit >= self.night.get("circuit", 2)
                and self.detector.phase == self.night.get("phase", "DOWNWIND"))

    def mark_night_done(self):
        self.night_done = True

    def tick(self, tel: Telemetry, ctrl: ControlSample) -> list[CoachEvent]:
        g = self.geom = self.detector.geometry(tel)
        out: list[CoachEvent] = []
        if not self.briefing_done:
            return out
        new_phase = self.detector.tick(tel, g)
        if not new_phase and self._announce_start:
            new_phase = self.detector.phase
        self._announce_start = False
        if new_phase:
            log.info("phase -> %s (circuit %s, agl %.0f, ias %.0f, cross %.0f)",
                     new_phase, self.detector.circuit, tel.alt_agl, tel.ias, g.cross)
            catch = self.monitors.catchup_turn(new_phase)
            if catch:
                out.append(catch)
            data = {"phase": new_phase, "circuit": self.detector.circuit,
                    "total_circuits": self.detector.total_circuits,
                    **{k: v for k, v in
                       self.monitors.targets_for(new_phase).items() if v}}
            out.append(CoachEvent(f"PHASE_ENTER_{new_phase}", "info", data, cooldown_s=0))
            note = self.phase_notes.get(new_phase)
            if note:
                # same severity as the phase call, queued after it, so it is
                # spoken as the follow-on teaching point for this leg
                out.append(CoachEvent("LESSON_NOTE", "info", {**data, **note}, cooldown_s=0))
        for ev in self.monitors.tick(self.detector.phase, tel, g, ctrl):
            if self._cooled(ev):
                out.append(ev)
        return out

    def targets(self) -> dict:
        return self.monitors.targets_for(self.detector.phase)
