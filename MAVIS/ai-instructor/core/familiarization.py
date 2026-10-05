"""Flight familiarization: an airborne lesson with no take-off or landing.

The aircraft is placed in the air (lesson `airborne_start`), the trainee flies
a short set of exercises -- straight & level, a gentle turn each way, a climb
and a descent -- then finds Paya Lebar and heads for it. The lesson ends when
the aircraft is within `finish_radius_nm` of the runway; no landing needed.

FamiliarizationEngine / FamiliarizationScorer mirror the LessonEngine / Scorer
interfaces main.py drives (tick, start_flying, load_phase, sample, finalize...)
so the same ticker, checkpoints and scorecard flow serve both lesson types.
"""
from __future__ import annotations
import logging
import math
import time

from adapters.sim_base import Telemetry
from adapters.input_hid import ControlSample
from .events import CoachEvent
from .monitors import Monitors
from .phase_detector import PhaseDetector, hdg_diff
from .scoring import DEFAULTS, ScoreCard, _curve

log = logging.getLogger(__name__)

M_PER_NM = 1852.0


def is_familiarization(lesson: dict) -> bool:
    return lesson.get("type") == "familiarization"


def field_center(lesson: dict) -> tuple[float, float]:
    """Runway midpoint lat/lon -- the 'airfield' the trainee flies back to."""
    rw = lesson["runway"]
    lat0, lon0 = rw["threshold"]["lat"], rw["threshold"]["lon"]
    h = math.radians(rw["heading_true"])
    half = rw["length_m"] / 2.0
    return (lat0 + half * math.cos(h) / 111320.0,
            lon0 + half * math.sin(h) / (111320.0 * math.cos(math.radians(lat0))))


def bearing_dist(lat: float, lon: float, lat2: float, lon2: float) -> tuple[float, float]:
    """True bearing (deg) and distance (m) from (lat, lon) to (lat2, lon2)."""
    dn = (lat2 - lat) * 111320.0
    de = (lon2 - lon) * 111320.0 * math.cos(math.radians(lat))
    return math.degrees(math.atan2(de, dn)) % 360.0, math.hypot(de, dn)


def clock_position(rel_deg: float) -> int:
    """Relative bearing -> clock code (12 = dead ahead, 3 = right wing)."""
    c = round((rel_deg % 360.0) / 30.0) % 12
    return 12 if c == 0 else c


class _Steps:
    """Stands in for PhaseDetector where main.py reads detector.* directly."""
    ORDER = ["BRIEFING", "STRAIGHT_LEVEL", "TURN_LEFT", "TURN_RIGHT",
             "CLIMB", "DESCEND", "FIND_AIRFIELD", "DEBRIEF"]

    def __init__(self):
        self.phase = "BRIEFING"
        self.circuit = 1
        self.total_circuits = 1
        self._still_since = None

    def advance(self, to: str) -> str:
        self.phase = to
        return to


class FamiliarizationEngine:
    def __init__(self, lesson: dict, aircraft: dict, sim_adapter: str = "mock"):
        self.lesson = lesson
        self.detector = _Steps()
        self._geo = PhaseDetector(lesson)        # runway-relative geometry for map/recorder
        self.monitors = Monitors(aircraft, lesson, sim_adapter=sim_adapter)  # reference profiles only
        self.vy = float(aircraft["v_speeds"]["vy"])
        ex = lesson.get("exercises") or {}
        self.hold_s = float(ex.get("straight_level_hold_s", 20))
        self.turn_deg = float(ex.get("turn_deg", 90))
        self.bank = float(ex.get("turn_bank_deg", 20))
        self.climb_ft = float(ex.get("climb_ft", 500))
        self.descend_ft = float(ex.get("descend_ft", 500))
        self.step_max_s = float(ex.get("step_timeout_s", 120))
        self.hint_s = float(ex.get("hint_every_s", 35))
        self.finish_m = float(lesson.get("finish_radius_nm", 2.0)) * M_PER_NM
        self.field = field_center(lesson)
        self.field_name = lesson["airport"].get("name", "the airfield")
        self.notes = lesson.get("step_notes") or {}
        self.briefing_done = False
        self.held = False                        # frozen while a step is explained
        self.night_done = True                   # no night switch in this lesson
        self.geom = None
        self._last_fire: dict[str, float] = {}
        self._reset_step()

    # ---- interface main.py drives ----
    @property
    def phase(self) -> str: return self.detector.phase

    @property
    def circuit(self) -> int: return 1

    def start_flying(self):
        self.briefing_done = True
        self.detector.advance("STRAIGHT_LEVEL")

    def skip_phase(self):
        order = self.detector.ORDER
        i = order.index(self.phase)
        if i < len(order) - 1:
            self.detector.advance(order[i + 1])

    def hold(self):
        """Aircraft frozen for an explanation: judge nothing until release()."""
        self.held = True

    def release(self):
        """'You have control': the step's clock starts now, from here."""
        self.held = False
        self._prev_hdg = self._prev_t = None
        if self._entry:
            self._entry["t"] = time.time()
            self._last_hint = time.time()

    def restart(self):
        self.detector.phase = "BRIEFING"
        self.briefing_done = False
        self.held = False
        self._last_fire.clear()
        self._reset_step()

    def load_phase(self, phase: str, circuit: int):
        """Checkpoint retry: re-enter the step fresh (re-announced on next tick)."""
        self.detector.advance(phase)
        self._reset_step()

    def wants_night_switch(self) -> bool:
        return False

    def mark_night_done(self):
        pass

    def targets(self) -> dict:
        alt = self._target_alt()
        return {"speed": self.vy if self.phase == "CLIMB" else None,
                "alt": round(alt) if alt is not None else None, "flaps": None}

    # ---- step logic ----
    def _reset_step(self):
        self._announced = None
        self._entry: dict = {}
        self._held = 0.0
        self._turned = 0.0
        self._prev_hdg = None
        self._prev_t = None
        self._last_hint = 0.0
        self._ahead_called = False

    def _target_alt(self):
        a = self._entry.get("alt")
        if a is None:
            return None
        if self.phase == "CLIMB":
            return a + self.climb_ft
        if self.phase == "DESCEND":
            return a - self.descend_ft
        if self.phase in ("STRAIGHT_LEVEL", "TURN_LEFT", "TURN_RIGHT"):
            return a
        return None

    def _cooled(self, ev: CoachEvent) -> bool:
        last = self._last_fire.get(ev.code, 0.0)
        if ev.t - last >= ev.cooldown_s:
            self._last_fire[ev.code] = ev.t
            return True
        return False

    def _field(self, tel: Telemetry) -> dict:
        brg, dist = bearing_dist(tel.lat, tel.lon, *self.field)
        return {"brg": round(brg / 10.0) * 10 % 360 or 360,
                "dist_nm": round(dist / M_PER_NM, 1),
                "clock": clock_position(brg - tel.hdg),
                "rel": hdg_diff(brg, tel.hdg), "dist_m": dist,
                "field": self.field_name}

    def _enter(self, tel: Telemetry, now: float) -> list[CoachEvent]:
        """First tick of a step: snapshot entry state, announce it."""
        self._announced = self.phase
        self._entry = {"alt": tel.alt_msl, "hdg": tel.hdg, "t": now}
        self._held = self._turned = 0.0
        self._last_hint = now
        p = self.phase
        f = self._field(tel)
        data = {"phase": p, "circuit": 1, "total_circuits": 1,
                "bank": round(self.bank), "turn_deg": round(self.turn_deg),
                "climb_ft": round(self.climb_ft), "descend_ft": round(self.descend_ft),
                "alt": round(tel.alt_msl / 100.0) * 100, "vy": round(self.vy),
                **{k: f[k] for k in ("brg", "dist_nm", "clock", "field")}}
        tgt = self._target_alt()
        if tgt is not None:
            data["tgt_alt"] = round(tgt / 100.0) * 100
        # what the mock autopilot should fly for this step
        mock = {"STRAIGHT_LEVEL": {"hdg_delta": 0},
                "TURN_LEFT": {"hdg_delta": -self.turn_deg},
                "TURN_RIGHT": {"hdg_delta": self.turn_deg},
                "CLIMB": {"alt_delta_ft": self.climb_ft, "spd": self.vy},
                "DESCEND": {"alt_delta_ft": -self.descend_ft, "spd": 95},
                "FIND_AIRFIELD": {"home": True, "spd": 95}}.get(p)
        if mock is not None:
            data["mock"] = mock
        out = [CoachEvent(f"PHASE_ENTER_{p}", "info", data, cooldown_s=0)]
        note = self.notes.get(p)
        if note:
            out.append(CoachEvent("LESSON_NOTE", "info", {**data, **note}, cooldown_s=0))
        log.info("familiarization step -> %s (alt %.0f, hdg %.0f, field %s nm at %s)",
                 p, tel.alt_msl, tel.hdg, f["dist_nm"], f["brg"])
        return out

    def _step_done(self, tel: Telemetry, dt: float, now: float) -> bool:
        p, e = self.phase, self._entry
        if p == "FIND_AIRFIELD":
            return self._field(tel)["dist_m"] <= self.finish_m
        timed_out = now - e["t"] > self.step_max_s
        if p == "STRAIGHT_LEVEL":
            if abs(tel.bank) < 6 and abs(tel.alt_msl - e["alt"]) < 150:
                self._held += dt
            return self._held >= self.hold_s or timed_out
        if p in ("TURN_LEFT", "TURN_RIGHT"):
            want = -1.0 if p == "TURN_LEFT" else 1.0
            return (self._turned * want >= self.turn_deg - 20 and abs(tel.bank) < 8) or timed_out
        if p == "CLIMB":
            return tel.alt_msl >= e["alt"] + self.climb_ft - 100 or timed_out
        if p == "DESCEND":
            return tel.alt_msl <= e["alt"] - self.descend_ft + 100 or timed_out
        return False

    def _monitors(self, tel: Telemetry, now: float) -> list[CoachEvent]:
        out: list[CoachEvent] = []
        if tel.on_ground:
            return out
        if tel.alt_agl < 700:
            out.append(CoachEvent("FAM_LOW", "safety", {"agl": round(tel.alt_agl)}, cooldown_s=15))
        if tel.ias < 58:
            out.append(CoachEvent("FAM_SLOW", "correction", {"ias": round(tel.ias)}, cooldown_s=15))
        elif tel.ias > 140:
            out.append(CoachEvent("FAM_FAST", "correction", {"ias": round(tel.ias)}, cooldown_s=15))
        if abs(tel.bank) > 40:
            out.append(CoachEvent("FAM_STEEP", "correction", {"bank": round(abs(tel.bank))}, cooldown_s=15))
        if self.phase in ("TURN_LEFT", "TURN_RIGHT") and now - self._entry["t"] > 20:
            want = -1.0 if self.phase == "TURN_LEFT" else 1.0
            if self._turned * want < 15 and abs(tel.bank) < 5:
                out.append(CoachEvent("FAM_TURN_REMIND", "nudge",
                                      {"dir": "left" if want < 0 else "right",
                                       "bank": round(self.bank)}, cooldown_s=25))
        if self.phase == "FIND_AIRFIELD":
            f = self._field(tel)
            if not self._ahead_called and abs(f["rel"]) < 20 and f["dist_m"] < 7 * M_PER_NM:
                self._ahead_called = True
                self._last_hint = now
                out.append(CoachEvent("FAM_FIELD_AHEAD", "info", f, cooldown_s=0))
            elif now - self._last_hint >= self.hint_s and abs(f["rel"]) >= 20:
                self._last_hint = now
                out.append(CoachEvent("FAM_FIELD_HINT", "nudge", f, cooldown_s=0))
        return out

    def tick(self, tel: Telemetry, ctrl: ControlSample) -> list[CoachEvent]:
        self.geom = self._geo.geometry(tel)
        out: list[CoachEvent] = []
        if not self.briefing_done or self.held or self.phase in ("BRIEFING", "DEBRIEF"):
            return out
        now = time.time()
        dt = 0.0 if self._prev_t is None else min(1.0, now - self._prev_t)
        if self._prev_hdg is not None:
            self._turned += hdg_diff(tel.hdg, self._prev_hdg)
        self._prev_hdg, self._prev_t = tel.hdg, now
        if self._announced != self.phase:
            return self._enter(tel, now)
        if self._step_done(tel, dt, now):
            order = self.detector.ORDER
            self.detector.advance(order[order.index(self.phase) + 1])
            if self.phase == "DEBRIEF":
                return [CoachEvent("PHASE_ENTER_DEBRIEF", "info", {"phase": "DEBRIEF"}, cooldown_s=0)]
            return self._enter(tel, now)
        for ev in self._monitors(tel, now):
            if ev.cooldown_s == 0 or self._cooled(ev):
                out.append(ev)
        return out


class FamiliarizationScorer:
    """Practice score for the familiarization flight -- never posted to the
    leaderboard. Metrics (0-100): altitude hold (straight & level and turns),
    bank control (turns), climb speed, smoothness. 'Incomplete' until the
    trainee reaches the airfield."""
    WEIGHTS = {"altitude": 30, "bank": 25, "climb_speed": 20, "smoothness": 25}

    def __init__(self, lesson: dict, aircraft: dict):
        ex = lesson.get("exercises") or {}
        self.bank = float(ex.get("turn_bank_deg", 20))
        self.vy = float(aircraft["v_speeds"]["vy"])
        cfg = lesson.get("scoring", {}) or {}
        self.s = {**DEFAULTS["scales"], "bank": 8.0, **cfg.get("scales", {})}
        self.grade_bands = cfg.get("grade_bands", DEFAULTS["grade_bands"])
        self.reset()

    def reset(self) -> None:
        self._acc = {k: [0.0, 0] for k in self.WEIGHTS}
        self._prev_ctrl = None
        self._phase = None
        self._ref_alt = None
        self.reached = False
        self.retries = 0

    def note_retry(self) -> None:
        self.retries += 1
        self._phase = None

    def on_landing(self, tel: dict, geom: dict | None) -> None:
        pass

    def _add(self, key: str, err: float) -> None:
        a = self._acc[key]
        a[0] += err
        a[1] += 1

    def sample(self, tel: dict, geom: dict | None, ctrl: dict | None, phase: str) -> None:
        if phase == "DEBRIEF":
            self.reached = True
        if tel.get("on_ground", True):
            return
        if phase != self._phase:
            self._phase, self._ref_alt = phase, tel.get("alt_msl", 0.0)
        if phase in ("STRAIGHT_LEVEL", "TURN_LEFT", "TURN_RIGHT"):
            self._add("altitude", abs(tel.get("alt_msl", 0.0) - self._ref_alt))
        if phase in ("TURN_LEFT", "TURN_RIGHT") and abs(tel.get("bank", 0.0)) > 5:
            self._add("bank", abs(abs(tel.get("bank", 0.0)) - self.bank))
        if phase == "CLIMB":
            self._add("climb_speed", abs(tel.get("ias", 0.0) - self.vy))
        roll = ctrl["roll"] if ctrl and ctrl.get("available") else tel.get("aileron", 0.0)
        pitch = ctrl["pitch"] if ctrl and ctrl.get("available") else -tel.get("elevator", 0.0)
        if self._prev_ctrl is not None:
            self._add("smoothness", abs(roll - self._prev_ctrl[0]) + abs(pitch - self._prev_ctrl[1]))
        self._prev_ctrl = (roll, pitch)

    def finalize(self, name: str, mode: str, lesson_name: str, practice: bool = False,
                 score_caps: bool | None = None) -> ScoreCard:
        scale = {"altitude": self.s["altitude"], "bank": self.s["bank"],
                 "climb_speed": self.s["speed"], "smoothness": self.s["smoothness"]}
        scores = {}
        for k, (tot, n) in self._acc.items():
            scores[k] = round(_curve(tot / n, scale[k])) if n else None
        flown = {k: v for k, v in scores.items() if v is not None}
        wsum = sum(self.WEIGHTS[k] for k in flown) or 1
        total = round(sum(v * self.WEIGHTS[k] for k, v in flown.items()) / wsum) if flown else 0
        grade = "F"
        for thr, g in self.grade_bands:
            if total >= thr:
                grade = g
                break
        return ScoreCard(name or "", mode, lesson_name, time.time(), scores,
                         self.retries, total, grade, False, True,
                         incomplete=not self.reached)
