"""Lesson state machine: owns phase progression, phase-entry callouts,
the announced day->night switch, and per-code event cooldowns."""
from __future__ import annotations
import logging
import time

log = logging.getLogger(__name__)

from adapters.sim_base import Telemetry
from adapters.input_hid import ControlSample
from .events import CoachEvent
from .monitors import Monitors, final_speed_band
from .meters import standard_all
from coach import phrases
from .phase_detector import PhaseDetector
from .variations import make_variation


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
        # to-do list for the pilot view (Take-off / Landing): lesson `todo_steps`
        self.todo_steps = lesson.get("todo_steps") or []
        self.todo_done = lesson.get("todo_done") or "Done. Well flown."
        self.end_agl = float(lesson.get("end_at_agl_ft") or 0)
        self.elev = float(lesson["airport"]["elevation_ft"])
        self._tel: Telemetry | None = None
        self._ever: set[str] = set()             # phases reached this run
        # Circuit Variations (core/variations.py): go-around / short / soft field
        self.variation = make_variation(lesson, aircraft)
        self._arm_variation()

    def _arm_variation(self) -> None:
        """Fresh run of a variation; a go-around gets a new random call height."""
        if self.variation:
            self.variation.reset()
            if self.detector.go_around:
                self.detector.go_around_agl = self.variation.call_agl
                log.info("go-around will be called at %.0f ft", self.variation.call_agl)

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
        self._ever.clear()
        self._last_fire.clear()
        self.monitors.reset_turn_cues()
        self._arm_variation()

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
        # a re-flown leg keeps what the variation has measured so far (the
        # soft-field take-off, ...); only the go-around gets a new call height
        if self.variation and self.detector.go_around:
            import random
            lo, hi = (self.variation.cfg.get("call_agl_ft") or [150, 300])
            self.variation.call_agl = random.uniform(float(lo), float(hi))
            self.detector.go_around_agl = self.variation.call_agl

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
        self._tel = tel
        if self.briefing_done:
            self._ever.add(self.detector.phase)
        out: list[CoachEvent] = []
        if not self.briefing_done:
            return out
        new_phase = self.detector.tick(tel, g)
        starting = not new_phase and self._announce_start
        if starting:
            new_phase = self.detector.phase
        self._announce_start = False
        if new_phase:
            log.info("phase -> %s (circuit %s, agl %.0f, ias %.0f, cross %.0f)",
                     new_phase, self.detector.circuit, tel.alt_agl, tel.ias, g.cross)
            # no catch-up turn call for the leg a lesson starts on (the landing
            # lesson starts straight in on final -- there was no turn)
            catch = None if starting else self.monitors.catchup_turn(new_phase)
            if starting:
                self.monitors._turn_spoken.add(f"TURN_{new_phase}")
            if catch:
                out.append(catch)
            data = {"phase": new_phase, "circuit": self.detector.circuit,
                    "total_circuits": self.detector.total_circuits,
                    **{k: v for k, v in
                       self.monitors.targets_for(new_phase).items() if v}}
            variant = self._leg_variant(new_phase, tel)
            if variant:
                data["variant"] = variant
            out.append(CoachEvent(f"PHASE_ENTER_{new_phase}", "info", data, cooldown_s=0))
            note = self.phase_notes.get(new_phase)
            if note:
                note = {**note, "say_meters": self._step_meters(new_phase),
                        # the leg call just before it: its targets aren't repeated
                        "said_before": phrases.render(f"PHASE_ENTER_{new_phase}", data)}
                # same severity as the phase call, queued after it, so it is
                # spoken as the follow-on teaching point for this leg
                out.append(CoachEvent("LESSON_NOTE", "info", {**data, **note}, cooldown_s=0))
        for ev in self.monitors.tick(self.detector.phase, tel, g, ctrl):
            if self._cooled(ev):
                out.append(ev)
        if self.variation:
            d = tel.as_dict()
            self.variation.observe(d, g.along, g.cross, self.detector.phase)
            for ev in self.variation.cues(d, g.along, g.cross, self.detector.phase):
                if self._cooled(ev):
                    out.append(ev)
        return out

    def _leg_variant(self, phase: str, tel: Telemetry) -> str | None:
        """Which wording of a leg's call fits what the aircraft is already
        doing, so MAVIS never says "keep climbing" when we're at circuit
        height, or "full flaps" when they're already down (coach.phrases
        PHASE_ENTER_<leg>__<variant>)."""
        if phase in ("CROSSWIND", "DOWNWIND"):
            dv = tel.alt_msl - self.monitors.pattern_alt
            if dv > 100:
                return "high"
            if dv >= -100:
                return "level"
            return "climb"
        if phase == "BASE" and tel.flaps_deg >= 15:
            return "flapsset"
        if phase == "FINAL" and tel.flaps_deg >= 25:
            return "flapsset"
        return None

    def targets(self) -> dict:
        return self.monitors.targets_for(self.detector.phase)

    def bands(self) -> dict:
        """Allowed ranges for the Live Monitor's dials (Monitors.bands_for)."""
        return self.monitors.bands_for(self.detector.phase, self._tel)

    # ---- to-do list (lessons with todo_steps) ----
    def todo(self) -> dict | None:
        """Same shape as FamiliarizationEngine.todo(): every step with its
        status, the active one with its checklist lines and live readouts."""
        if not self.todo_steps:
            return None
        phase = self.detector.phase
        cur = next((i for i, st in enumerate(self.todo_steps) if phase in st.get("phases", [])),
                   len(self.todo_steps) if phase == "DEBRIEF" else -1)
        steps = []
        for i, st in enumerate(self.todo_steps):
            row = {"id": f"step{i}", "title": st.get("title", ""),
                   "status": "done" if i < cur else ("active" if i == cur else "todo")}
            if i == cur:
                row["lines"] = list(st.get("todo") or [])
                row["meters"] = standard_all(self._meter(k) for k in st.get("meters") or [])
            steps.append(row)
        return {"steps": steps, "complete": phase == "DEBRIEF", "done_text": self.todo_done}

    def _step_meters(self, phase: str) -> list[dict]:
        """The to-do readouts of the step this phase belongs to (read out after
        the leg's teaching point -- coach.phrases.spoken_targets)."""
        st = next((s for s in self.todo_steps if phase in s.get("phases", [])), None)
        return standard_all(self._meter(k) for k in st.get("meters") or []) if st else []

    def _meter(self, kind: str) -> dict | None:
        """One live "now / target" row. No target speed on the take-off: it
        shows the speed, the trainee isn't asked to fly a number."""
        tel, g = self._tel, self.geom
        if tel is None or g is None:
            return None
        agl = max(0.0, tel.alt_msl - self.elev)
        side = "left" if g.cross > 0 else "right"     # cross + = pattern side (left traffic)
        if kind == "speed":
            return {"label": "Speed", "now": f"{tel.ias:.0f} kt", "target": ""}
        if kind == "centreline":
            # how far off still counts as fine (m): the lesson's centreline_ok_m
            # in the air (default 30), a third of that on the ground (min 10)
            air = float(self.lesson.get("centreline_ok_m", 30))
            lim = max(10.0, air / 3) if tel.on_ground else air
            now = "on the line" if abs(g.cross) < 10 else f"{abs(g.cross):.0f} m {side}"
            return {"label": "Centreline", "now": now, "target": "middle", "ok": abs(g.cross) < lim}
        if kind == "height_to":
            tgt = self.end_agl or 1000.0
            return {"label": "Height", "now": f"{agl:,.0f} ft", "target": f"{tgt:,.0f} ft",
                    "pct": min(1.0, agl / tgt)}
        if kind == "speed_band":
            fb = final_speed_band(self.lesson, self.monitors._v, agl) if self.detector.phase == "FINAL" else None
            if fb:
                lo, hi, aim = fb       # aim slides lower over the last stretch to the runway
                return {"label": "Speed", "now": f"{tel.ias:.0f} kt",
                        "target": f"aim {aim:.0f} kt ({lo:.0f}\u2013{hi:.0f})", "ok": lo <= tel.ias <= hi}
            spd = self.monitors.tgt_speed.get(self.detector.phase)
            if not spd:
                return None
            return {"label": "Speed", "now": f"{tel.ias:.0f} kt", "target": f"{spd - 5:.0f}–{spd + 5:.0f} kt",
                    "ok": abs(tel.ias - spd) <= 5}
        if kind == "glidepath":
            ideal = max(0.0, -g.along) * 0.1719            # 3-degree path, ft above the runway
            diff = agl - ideal
            now = "on the path" if abs(diff) < 40 else f"{abs(diff):.0f} ft {'high' if diff > 0 else 'low'}"
            return {"label": "Glidepath", "now": now, "target": "on the path", "ok": abs(diff) < 60}
        if kind == "flaps":
            want = self.monitors.tgt_flaps.get("FINAL", 30)
            return {"label": "Flaps", "now": f"{tel.flaps_deg:.0f}°", "target": "full",
                    "ok": tel.flaps_deg >= want - 6}
        if kind == "height":
            return {"label": "Height", "now": f"{agl:,.0f} ft", "target": ""}
        if kind == "height_agl":
            return {"label": "Height", "now": f"{agl:,.0f} ft", "target": "settle on"}
        if kind == "sink":
            return {"label": "Sink", "now": f"{max(0.0, -tel.vs):.0f} fpm", "target": "under 500 fpm",
                    "ok": -tel.vs < 500}
        if kind == "stop":
            return {"label": "Speed", "now": f"{tel.ias:.0f} kt", "target": "stopped",
                    "pct": min(1.0, max(0.0, 1.0 - tel.ias / 60.0))}
        return None
