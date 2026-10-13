"""Airborne exercise lessons (type: familiarization): no take-off or landing.

The aircraft is placed in the air (lesson `airborne_start`), the trainee flies
a short set of exercises -- by default straight & level, a gentle turn each
way, a climb and a descent; a lesson can list its own `steps` from STEP_TITLES
(Effects of Controls, Straight & Level) -- then finds Paya Lebar and heads for
it. The lesson ends once the trainee points the aircraft at the field for a
few seconds (or gets within `finish.radius_nm` of it); no landing needed.

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


def start_position(lesson: dict) -> tuple[float, float]:
    """airborne_start lat/lon: explicit, or bearing/distance from the field."""
    a = lesson["airborne_start"]
    if "lat" in a and "lon" in a:
        return a["lat"], a["lon"]
    if "final_m" in a:
        # on the extended centreline, this far before the threshold (landing lesson)
        rw = lesson["runway"]
        lat0, lon0 = rw["threshold"]["lat"], rw["threshold"]["lon"]
        h = math.radians(rw["heading_true"])
        along = -float(a["final_m"])
        left = float(lesson["pattern"].get("centerline_offset_m", 0.0))
        n = along * math.cos(h) + left * math.sin(h)
        e = along * math.sin(h) - left * math.cos(h)
        return (lat0 + n / 111320.0,
                lon0 + e / (111320.0 * math.cos(math.radians(lat0))))
    lat0, lon0 = field_center(lesson)
    b = math.radians(a["bearing_from_field_deg"])
    d = float(a["distance_nm"]) * M_PER_NM
    return (lat0 + d * math.cos(b) / 111320.0,
            lon0 + d * math.sin(b) / (111320.0 * math.cos(math.radians(lat0))))


def runway_start(lesson: dict) -> tuple[float, float, float]:
    """Lined-up spot at the start of the runway (lat, lon, true heading) the
    aircraft is put back on once the exercise is scored. Applies the lesson's
    centerline_offset_m calibration so it lands on the real centreline."""
    rw = lesson["runway"]
    lat0, lon0 = rw["threshold"]["lat"], rw["threshold"]["lon"]
    h = math.radians(rw["heading_true"])
    along = float((lesson.get("end_reposition") or {}).get("along_m", 60))
    left = float(lesson["pattern"].get("centerline_offset_m", 0.0))
    n = along * math.cos(h) + left * math.sin(h)
    e = along * math.sin(h) - left * math.cos(h)
    return (lat0 + n / 111320.0,
            lon0 + e / (111320.0 * math.cos(math.radians(lat0))),
            float(rw["heading_true"]))


def bearing_dist(lat: float, lon: float, lat2: float, lon2: float) -> tuple[float, float]:
    """True bearing (deg) and distance (m) from (lat, lon) to (lat2, lon2)."""
    dn = (lat2 - lat) * 111320.0
    de = (lon2 - lon) * 111320.0 * math.cos(math.radians(lat))
    return math.degrees(math.atan2(de, dn)) % 360.0, math.hypot(de, dn)


def _r(x: float, step: float) -> int:
    return int(round(x / step) * step)


def _hdg(x: float) -> int:
    """Rounded heading in 1..360 (a pilot says 360, not 0)."""
    return _r(x % 360.0, 10) % 360 or 360


STEP_TITLES = {"STRAIGHT_LEVEL": "Straight & level", "TURN_LEFT": "Gentle left turn",
               "TURN_RIGHT": "Gentle right turn", "CLIMB": "Climb", "DESCEND": "Descend",
               "PITCH_UP": "Nose up", "PITCH_DOWN": "Nose down",
               "ROLL_LEFT": "Roll left, roll level", "ROLL_RIGHT": "Roll right, roll level",
               "POWER": "Power up, power back",
               "YAW_LEFT": "Left rudder", "YAW_RIGHT": "Right rudder",
               "TRIM": "Trim it out", "SLOW_CRUISE": "Slow down", "FAST_CRUISE": "Speed up",
               "CLIMB_TO": "Climb and level off", "DESCEND_TO": "Descend and level off",
               "GLIDE": "Glide", "SLOW_FLIGHT": "Slow flight", "STALL": "Stall and recover",
               "MEDIUM_TURN_LEFT": "Medium turn left", "MEDIUM_TURN_RIGHT": "Medium turn right",
               "STEEP_TURN_LEFT": "Steep turn left", "STEEP_TURN_RIGHT": "Steep turn right",
               "FIND_AIRFIELD": "Find Paya Lebar & point at it"}

DEFAULT_STEPS = ["STRAIGHT_LEVEL", "TURN_LEFT", "TURN_RIGHT", "CLIMB", "DESCEND", "FIND_AIRFIELD"]
ROLL_STEPS = ("ROLL_LEFT", "ROLL_RIGHT")
LEVEL_OFF_STEPS = ("CLIMB_TO", "DESCEND_TO")            # to a set height, then level off there
MEDIUM_TURNS = ("MEDIUM_TURN_LEFT", "MEDIUM_TURN_RIGHT")
STEEP_TURNS = ("STEEP_TURN_LEFT", "STEEP_TURN_RIGHT")
BANKED_TURNS = (*MEDIUM_TURNS, *STEEP_TURNS)
# Height is part of pass/fail only where holding it IS the skill (slow
# flight, medium and steep turns) -- within a generous band.
HOLD_HEIGHT_STEPS = ("SLOW_FLIGHT", *BANKED_TURNS)
LEVEL_STEPS = ("STRAIGHT_LEVEL", "TURN_LEFT", "TURN_RIGHT", *ROLL_STEPS, "YAW_LEFT", "YAW_RIGHT",
               "TRIM", "SLOW_CRUISE", "FAST_CRUISE", *HOLD_HEIGHT_STEPS)  # height held at the entry altitude


def lesson_steps(lesson: dict) -> list[str]:
    """The lesson's exercises in order. Finding the field, when a lesson
    lists it (Flight Familiarization), is always the last one."""
    steps = [s for s in (lesson.get("steps") or DEFAULT_STEPS) if s in STEP_TITLES]
    if "FIND_AIRFIELD" in steps:
        steps.remove("FIND_AIRFIELD")
        steps.append("FIND_AIRFIELD")
    return steps


def level_target(step: str, alt: float, level_ft: dict) -> float:
    """Climb/descend-and-level-off target from the step's start height: a
    round hundred feet (level_ft = the lesson's climb_to_ft / descend_to_ft)."""
    sign = 1.0 if step == "CLIMB_TO" else -1.0
    return round((alt + sign * level_ft[step]) / 100.0) * 100.0


def turn_dir(step: str) -> float:
    """-1 for a step that turns/rolls/yaws left, +1 for right."""
    return -1.0 if step.endswith("_LEFT") else 1.0


def clock_position(rel_deg: float) -> int:
    """Relative bearing -> clock code (12 = dead ahead, 3 = right wing)."""
    c = round((rel_deg % 360.0) / 30.0) % 12
    return 12 if c == 0 else c


class _Steps:
    """Stands in for PhaseDetector where main.py reads detector.* directly."""
    def __init__(self, steps: list[str]):
        self.ORDER = ["BRIEFING", *steps, "DEBRIEF"]
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
        self.detector = _Steps(lesson_steps(lesson))
        self.mock = sim_adapter == "mock"
        self._geo = PhaseDetector(lesson)        # runway-relative geometry for map/recorder
        self.monitors = Monitors(aircraft, lesson, sim_adapter=sim_adapter)  # reference profiles only
        self.vy = float(aircraft["v_speeds"]["vy"])
        ex = lesson.get("exercises") or {}
        self.hold_s = float(ex.get("straight_level_hold_s", 10))
        self.turn_deg = float(ex.get("turn_deg", 90))
        self.bank = float(ex.get("turn_bank_deg", 20))
        self.climb_ft = float(ex.get("climb_ft", 500))
        self.descend_ft = float(ex.get("descend_ft", 500))
        self.step_max_s = float(ex.get("step_timeout_s", 120))
        self.hint_s = float(ex.get("hint_every_s", 35))
        self.alt_band = float(ex.get("altitude_band_ft", 200))
        self.pitch_ft = float(ex.get("pitch_change_ft", 200))
        self.yaw_deg = float(ex.get("yaw_deg", 10))
        self.roll_hold_s = float(ex.get("roll_hold_s", 2))
        self.power_throttle = float(ex.get("power_throttle_change", 0.2))   # 0..1 lever travel
        self.trim_s = float(ex.get("trim_hold_s", 10))
        self.speed_s = float(ex.get("speed_hold_s", 10))
        self.speeds = {"SLOW_CRUISE": float(ex.get("slow_kt", 80)),
                       "FAST_CRUISE": float(ex.get("fast_kt", 105))}
        # climbs & descents
        self.level_ft = {"CLIMB_TO": float(ex.get("climb_to_ft", 500)),
                         "DESCEND_TO": float(ex.get("descend_to_ft", 500))}
        self.level_band = float(ex.get("level_band_ft", 100))     # "level at the target" means within this
        self.level_hold_s = float(ex.get("level_hold_s", 5))
        self.glide_kt = float(ex.get("glide_kt", 68))
        self.glide_ft = float(ex.get("glide_ft", 300))
        # slow flight & stalls
        vs1 = float(aircraft["v_speeds"].get("vs1", 48))
        self.sf_kt = float(ex.get("slow_flight_kt", 55))
        self.sf_hold_s = float(ex.get("slow_flight_hold_s", 10))
        self.stall_kt = float(ex.get("stall_kt", vs1 + 2))
        self.recover_kt = float(ex.get("recover_kt", 65))
        # medium & steep turns: (bank, degrees of turn)
        self.turn_spec = {"MEDIUM": (float(ex.get("medium_bank_deg", 30)), float(ex.get("medium_turn_deg", 180))),
                          "STEEP": (float(ex.get("steep_bank_deg", 45)), float(ex.get("steep_turn_deg", 360)))}
        self.height_band = float(ex.get("height_band_ft", 200))  # where height counts: within this
        fin = lesson.get("finish") or {}
        self.finish_m = float(fin.get("radius_nm", 2.0)) * M_PER_NM
        self.point_deg = float(fin.get("pointing_within_deg", 15))
        self.point_s = float(fin.get("pointing_hold_s", 5))
        self.point_max_m = float(fin.get("pointing_max_nm", 10)) * M_PER_NM
        self.field = field_center(lesson)
        self.field_name = lesson["airport"].get("name", "the airfield")
        self.notes = lesson.get("step_notes") or {}
        self.briefing_done = False
        self.held = False                        # frozen while a step is explained
        self.night_done = True                   # no night switch in this lesson
        self.geom = None
        self.skipped: set[str] = set()          # steps that timed out unfinished
        self._last_fire: dict[str, float] = {}
        self._reset_step()

    # ---- interface main.py drives ----
    @property
    def phase(self) -> str: return self.detector.phase

    @property
    def circuit(self) -> int: return 1

    def start_flying(self):
        self.briefing_done = True
        self.detector.advance(self.detector.ORDER[1])     # the lesson's first exercise

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
        self.skipped.clear()
        self._last_fire.clear()
        self._reset_step()

    def load_phase(self, phase: str, circuit: int):
        """Checkpoint retry: re-enter the step fresh (re-announced on next tick)."""
        self.detector.advance(phase)
        order = self.detector.ORDER
        self.skipped -= set(order[order.index(phase):])
        self._reset_step()

    def wants_night_switch(self) -> bool:
        return False

    def mark_night_done(self):
        pass

    def targets(self) -> dict:
        alt = self._target_alt()
        spd = {"CLIMB": self.vy, "CLIMB_TO": self.vy, "GLIDE": self.glide_kt,
               "SLOW_FLIGHT": self.sf_kt}.get(self.phase, self.speeds.get(self.phase))
        return {"speed": spd,
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
        self._pointing = 0.0
        self._rudder = 0.0                       # largest rudder deflection this step
        self._stage = 0                          # two-part steps: 0 = first part, 1 = second
        self._progress = 0.0                     # banked turns: degrees turned that count
        self._prev_turned = 0.0
        self._events: list[CoachEvent] = []      # said mid-step (the stall callout)
        self._data: dict = {}
        self._meters: list[dict] = []

    def _target_alt(self):
        a = self._entry.get("alt")
        if a is None:
            return None
        if self.phase == "CLIMB":
            return a + self.climb_ft
        if self.phase == "DESCEND":
            return a - self.descend_ft
        if self.phase == "PITCH_UP":
            return a + self.pitch_ft
        if self.phase == "PITCH_DOWN":
            return a - self.pitch_ft
        if self.phase in LEVEL_OFF_STEPS:
            return self._entry.get("tgt")
        if self.phase == "GLIDE":
            return a - self.glide_ft
        if self.phase in LEVEL_STEPS:
            return a
        return None

    def level_target(self, step: str, alt: float) -> float:
        return level_target(step, alt, self.level_ft)

    def turn_target(self, step: str) -> tuple[float, float]:
        """(bank, degrees of turn) for a medium or steep turn."""
        return self.turn_spec["MEDIUM" if step in MEDIUM_TURNS else "STEEP"]

    def _cooled(self, ev: CoachEvent) -> bool:
        last = self._last_fire.get(ev.code, 0.0)
        if ev.t - last >= ev.cooldown_s:
            self._last_fire[ev.code] = ev.t
            return True
        return False

    def _field(self, tel: Telemetry) -> dict:
        brg, dist = bearing_dist(tel.lat, tel.lon, *self.field)
        return {"brg_lo": _hdg(brg - 15), "brg_hi": _hdg(brg + 15),
                "dist_nm": max(1, round(dist / M_PER_NM)),
                "clock": clock_position(brg - tel.hdg),
                "rel": hdg_diff(brg, tel.hdg), "dist_m": dist,
                "field": self.field_name}

    def _enter(self, tel: Telemetry, now: float) -> list[CoachEvent]:
        """First tick of a step: snapshot entry state, announce it."""
        self._announced = self.phase
        self._entry = {"alt": tel.alt_msl, "hdg": tel.hdg, "t": now, "throttle": tel.throttle,
                       "ias": tel.ias}
        if self.phase in LEVEL_OFF_STEPS:
            self._entry["tgt"] = self.level_target(self.phase, tel.alt_msl)
        self._held = self._turned = self._progress = self._prev_turned = 0.0
        self._stage = 0
        self._events = []
        self._last_hint = now
        p = self.phase
        f = self._field(tel)
        data = self.step_data(p, tel)
        self._data = data
        self._meters = self._readout(tel)        # targets visible while it's explained
        slow, fast = self.speeds["SLOW_CRUISE"], self.speeds["FAST_CRUISE"]
        # what the mock autopilot should fly for this step
        mock = {"STRAIGHT_LEVEL": {"hdg_delta": 0},
                "TURN_LEFT": {"hdg_delta": -self.turn_deg},
                "TURN_RIGHT": {"hdg_delta": self.turn_deg},
                "CLIMB": {"alt_delta_ft": self.climb_ft, "spd": self.vy},
                "DESCEND": {"alt_delta_ft": -self.descend_ft, "spd": 95},
                "PITCH_UP": {"alt_delta_ft": self.pitch_ft, "spd": 80},
                "PITCH_DOWN": {"alt_delta_ft": -self.pitch_ft, "spd": 105},
                "ROLL_LEFT": {"hdg_delta": -45, "spd": 95},
                "ROLL_RIGHT": {"hdg_delta": 45, "spd": 95},
                "POWER": {"alt_delta_ft": 150, "spd": 95},
                "YAW_LEFT": {"hdg_delta": -(self.yaw_deg + 5), "spd": 95},
                "YAW_RIGHT": {"hdg_delta": self.yaw_deg + 5, "spd": 95},
                "TRIM": {"hdg_delta": 0, "spd": 95},
                "SLOW_CRUISE": {"hdg_delta": 0, "spd": slow},
                "FAST_CRUISE": {"hdg_delta": 0, "spd": fast},
                "CLIMB_TO": {"alt_delta_ft": data["tgt_alt"] - tel.alt_msl, "spd": self.vy},
                "DESCEND_TO": {"alt_delta_ft": data["tgt_alt"] - tel.alt_msl, "spd": 95},
                "GLIDE": {"alt_delta_ft": -(self.glide_ft + 80), "spd": self.glide_kt},
                "SLOW_FLIGHT": {"hdg_delta": 0, "spd": self.sf_kt},
                "STALL": {"hdg_delta": 0, "spd": self.stall_kt - 5},
                "FIND_AIRFIELD": {"home": True, "spd": 95}}.get(p)
        if p in BANKED_TURNS:
            bank, deg = self.turn_target(p)
            mock = {"turn_deg": turn_dir(p) * (deg + 5), "bank": bank, "spd": 95}
        if mock is not None:
            data["mock"] = mock
        out = [CoachEvent(f"PHASE_ENTER_{p}", "info", data, cooldown_s=0)]
        note = self.notes.get(p)
        if note:
            out.append(CoachEvent("LESSON_NOTE", "info", {**data, **note}, cooldown_s=0))
        log.info("familiarization step -> %s (alt %.0f, hdg %.0f, field ~%s nm, bearing from %s)",
                 p, tel.alt_msl, tel.hdg, f["dist_nm"], f["brg_lo"])
        return out

    def preview(self, phase: str, tel: Telemetry) -> dict:
        """A step's LESSON_NOTE data as if it began now -- what the up-front
        demonstration narrates -- without touching the lesson's progress."""
        return {**self.step_data(phase, tel), **(self.notes.get(phase) or {})}

    def step_data(self, p: str, tel: Telemetry) -> dict:
        """Numbers a step's lines and to-do use. Every number spoken or listed
        is a range or an "about" -- the trainee flies to a band, not a needle."""
        f = self._field(tel)
        a = tel.alt_msl
        band = {"CLIMB": (a + self.climb_ft - 100, a + self.climb_ft + 100),
                "DESCEND": (a - self.descend_ft - 100, a - self.descend_ft + 100),
                "PITCH_UP": (a + self.pitch_ft - 50, a + self.pitch_ft + 100),
                "PITCH_DOWN": (a - self.pitch_ft - 100, a - self.pitch_ft + 50)}.get(
                    p, (a - self.alt_band, a + self.alt_band))
        slow, fast = self.speeds["SLOW_CRUISE"], self.speeds["FAST_CRUISE"]
        mb, mdeg = self.turn_spec["MEDIUM"]
        sb, sdeg = self.turn_spec["STEEP"]
        return {"phase": p, "circuit": 1, "total_circuits": 1,
                "tgt_alt": round(self.level_target(p, a)) if p in LEVEL_OFF_STEPS else round(a),
                "level_band": round(self.level_band), "level_hold_s": round(self.level_hold_s),
                "glide_lo": _r(self.glide_kt - 5, 1), "glide_hi": _r(self.glide_kt + 5, 1),
                "glide_ft": round(self.glide_ft),
                "sf_lo": _r(self.sf_kt - 5, 5), "sf_hi": _r(self.sf_kt + 5, 5),
                "sf_hold_s": round(self.sf_hold_s), "band_ft": round(self.height_band),
                "stall_kt": round(self.stall_kt), "recover_kt": round(self.recover_kt),
                "med_bank_lo": _r(mb - 5, 5), "med_bank_hi": _r(mb + 5, 5), "med_turn_deg": round(mdeg),
                "steep_bank_lo": _r(sb - 5, 5), "steep_bank_hi": _r(sb + 5, 5),
                "steep_turn_deg": round(sdeg),
                "bank_lo": _r(self.bank - 5, 5), "bank_hi": _r(self.bank + 5, 5),
                "climb_lo": _r(self.climb_ft - 100, 100), "climb_hi": _r(self.climb_ft + 100, 100),
                "descend_lo": _r(self.descend_ft - 100, 100),
                "descend_hi": _r(self.descend_ft + 100, 100),
                "alt_lo": _r(band[0], 100), "alt_hi": _r(band[1], 100),
                "spd_lo": _r(self.vy - 5, 5), "spd_hi": _r(self.vy + 5, 5),
                "hold_s": round(self.hold_s),
                "pitch_lo": _r(self.pitch_ft - 50, 50), "pitch_hi": _r(self.pitch_ft + 100, 50),
                "yaw_deg": round(self.yaw_deg), "trim_s": round(self.trim_s),
                "speed_s": round(self.speed_s),
                "slow_lo": _r(slow - 5, 5), "slow_hi": _r(slow + 5, 5),
                "fast_lo": _r(fast - 5, 5), "fast_hi": _r(fast + 5, 5),
                **{k: f[k] for k in ("brg_lo", "brg_hi", "dist_nm", "clock", "field")}}

    def _achieved(self, tel: Telemetry, dt: float) -> bool:
        """Has the trainee done what the current step asks?"""
        p, e = self.phase, self._entry
        if p == "FIND_AIRFIELD":
            f = self._field(tel)
            if f["dist_m"] <= self.point_max_m and abs(f["rel"]) <= self.point_deg:
                self._pointing += dt
            else:
                self._pointing = 0.0
            return f["dist_m"] <= self.finish_m or self._pointing >= self.point_s
        if p == "STRAIGHT_LEVEL":
            if abs(tel.bank) < 6:                # wings level only; height is scored, not required
                self._held += dt
            return self._held >= self.hold_s
        if p in ("TURN_LEFT", "TURN_RIGHT"):
            want = -1.0 if p == "TURN_LEFT" else 1.0
            return self._turned * want >= self.turn_deg - 20 and abs(tel.bank) < 8
        if p == "CLIMB":
            return tel.alt_msl >= e["alt"] + self.climb_ft - 100
        if p == "DESCEND":
            return tel.alt_msl <= e["alt"] - self.descend_ft + 100
        if p == "PITCH_UP":
            return tel.alt_msl >= e["alt"] + self.pitch_ft - 50
        if p == "PITCH_DOWN":
            return tel.alt_msl <= e["alt"] - self.pitch_ft + 50
        if p in ROLL_STEPS:
            # bank in the right direction, hold it a moment, then wings level
            want = -1.0 if p == "ROLL_LEFT" else 1.0
            if self._stage == 0:
                if tel.bank * want >= self.bank - 7:
                    self._held += dt
                    if self._held >= self.roll_hold_s:
                        self._stage = 1
                return False
            return abs(tel.bank) < 5
        if p == "POWER":
            # throttle forward (watch the nose rise and swing), then back again.
            # The mock aircraft has no throttle to move: there the climb counts.
            if self.mock:
                return tel.alt_msl >= e["alt"] + 100
            t0 = e.get("throttle", 0.0)
            if self._stage == 0:
                if tel.throttle >= t0 + self.power_throttle:
                    self._stage = 1
                return False
            return abs(tel.throttle - t0) <= 0.08
        if p in ("YAW_LEFT", "YAW_RIGHT"):
            # nose swung the right way with the pedals, not the stick (the mock
            # aircraft has no rudder, so there the swing alone counts)
            want = -1.0 if p == "YAW_LEFT" else 1.0
            self._rudder = max(self._rudder, abs(tel.rudder))
            return self._turned * want >= self.yaw_deg and (self.mock or self._rudder >= 0.2)
        if p == "TRIM":
            # hands (nearly) off the stick and it stays steady -- no height target
            if abs(tel.vs) < 300 and (self.mock or abs(tel.elevator) < 0.1):
                self._held += dt
            else:
                self._held = 0.0
            return self._held >= self.trim_s
        if p in self.speeds:
            if abs(tel.ias - self.speeds[p]) <= 5:     # the speed only
                self._held += dt
            else:
                self._held = 0.0
            return self._held >= self.speed_s
        if p in LEVEL_OFF_STEPS:
            # get to the set height, then stay level on it a few seconds
            tgt = e["tgt"]
            if self._stage == 0:
                if abs(tel.alt_msl - tgt) < self.level_band:
                    self._stage = 1
                return False
            if abs(tel.alt_msl - tgt) < self.level_band and abs(tel.vs) < 200:
                self._held += dt
            return self._held >= self.level_hold_s
        if p == "GLIDE":
            return tel.alt_msl <= e["alt"] - self.glide_ft
        if p == "SLOW_FLIGHT":
            if abs(tel.ias - self.sf_kt) <= 5 and abs(tel.alt_msl - e["alt"]) < self.height_band:
                self._held += dt
            return self._held >= self.sf_hold_s
        if p == "STALL":
            if self._stage == 0:
                # stalled: down at the stall speed, or nose up and sinking anyway
                if tel.ias <= self.stall_kt or (tel.pitch > 10 and tel.vs < -300):
                    self._stage = 1
                    self._events.append(CoachEvent(
                        "FAM_STALL", "safety",
                        {"phase": p, "mock": {"spd": self.recover_kt + 10, "alt_delta_ft": 0}},
                        cooldown_s=0))
                return False
            return tel.ias >= self.recover_kt and tel.vs >= -200
        if p in BANKED_TURNS:
            # the turn only counts while banked enough and holding height
            bank, deg = self.turn_target(p)
            want = turn_dir(p)
            step_turn = self._turned - self._prev_turned
            self._prev_turned = self._turned
            if self._stage == 0:
                if (tel.bank * want >= bank - 8
                        and abs(tel.alt_msl - e["alt"]) < self.height_band):
                    self._progress += max(0.0, step_turn * want)
                if self._progress >= deg - 20:
                    self._stage = 1
                return False
            return abs(tel.bank) < 8
        return False

    def _step_done(self, tel: Telemetry, dt: float, now: float) -> bool:
        """Done, or out of time (finding the field never times out: it's the
        lesson's goal). A timed-out step moves on but is marked skipped."""
        if self._achieved(tel, dt):
            return True
        if self.phase != "FIND_AIRFIELD" and now - self._entry["t"] > self.step_max_s:
            self.skipped.add(self.phase)
            log.info("familiarization step %s timed out -- moving on", self.phase)
            return True
        return False

    def _monitors(self, tel: Telemetry, now: float) -> list[CoachEvent]:
        out: list[CoachEvent] = []
        if tel.on_ground:
            return out
        if tel.alt_agl < 700:
            out.append(CoachEvent("FAM_LOW", "safety", {"agl": round(tel.alt_agl)}, cooldown_s=15))
        if tel.ias < 58 and self.phase not in ("SLOW_FLIGHT", "STALL"):   # slow on purpose there
            out.append(CoachEvent("FAM_SLOW", "correction", {"ias": round(tel.ias)}, cooldown_s=15))
        elif tel.ias > 140:
            out.append(CoachEvent("FAM_FAST", "correction", {"ias": round(tel.ias)}, cooldown_s=15))
        if abs(tel.bank) > (58 if self.phase in STEEP_TURNS else 40):
            out.append(CoachEvent("FAM_STEEP", "correction", {"bank": round(abs(tel.bank))}, cooldown_s=15))
        if self.phase in ("YAW_LEFT", "YAW_RIGHT") and now - self._entry["t"] > 20:
            want = -1.0 if self.phase == "YAW_LEFT" else 1.0
            if self._turned * want < self.yaw_deg / 2:
                out.append(CoachEvent("FAM_YAW_REMIND", "nudge",
                                      {"dir": "left" if want < 0 else "right"}, cooldown_s=25))
        if (self.phase in ("TURN_LEFT", "TURN_RIGHT", *ROLL_STEPS, *BANKED_TURNS)
                and now - self._entry["t"] > 20):
            want = turn_dir(self.phase)
            if self._turned * want < 15 and abs(tel.bank) < 5 and self._stage == 0:
                key = ("med_" if self.phase in MEDIUM_TURNS else
                       "steep_" if self.phase in STEEP_TURNS else "")
                out.append(CoachEvent("FAM_TURN_REMIND", "nudge",
                                      {"dir": "left" if want < 0 else "right",
                                       "bank_lo": self._data.get(key + "bank_lo"),
                                       "bank_hi": self._data.get(key + "bank_hi")}, cooldown_s=25))
        if self.phase == "FIND_AIRFIELD":
            f = self._field(tel)
            if now - self._last_hint >= self.hint_s and abs(f["rel"]) > self.point_deg:
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
        self._meters = self._readout(tel)
        if self._step_done(tel, dt, now):
            self._events = []
            order = self.detector.ORDER
            self.detector.advance(order[order.index(self.phase) + 1])
            if self.phase == "DEBRIEF":
                self._meters = []
                return [CoachEvent("PHASE_ENTER_DEBRIEF", "info", {"phase": "DEBRIEF"}, cooldown_s=0)]
            return self._enter(tel, now)
        out.extend(self._events)                 # e.g. the stall callout
        self._events = []
        for ev in self._monitors(tel, now):
            if ev.cooldown_s == 0 or self._cooled(ev):
                out.append(ev)
        return out


    def _readout(self, tel: Telemetry) -> list[dict]:
        """Live "now / target" rows for the to-do list: how far through the
        step the trainee is and how big an angle/height/speed to aim for.
        Each row: label, now, target, and pct (0..1, progress bar) or ok
        (in the band right now)."""
        p, e, d = self.phase, self._entry, self._data
        # only what the step asks for -- no height row on steps that don't
        # change height (it's scored on straight & level, never required)
        alt = tel.alt_msl
        pitch = tel.pitch
        if p == "STRAIGHT_LEVEL":
            return [{"label": "Steady", "now": f"{self._held:.0f} s", "target": f"{self.hold_s:.0f} s",
                     "pct": min(1.0, self._held / self.hold_s)},
                    {"label": "Bank", "now": f"{abs(tel.bank):.0f}\u00b0", "target": "under 5\u00b0",
                     "ok": abs(tel.bank) < 6}]
        if p in ("TURN_LEFT", "TURN_RIGHT"):
            want = -1.0 if p == "TURN_LEFT" else 1.0
            turned = max(0.0, self._turned * want)
            need = self.turn_deg - 20
            return [{"label": "Turned", "now": f"{_r(turned, 5)}\u00b0",
                     "target": f"{need:.0f}\u2013{self.turn_deg:.0f}\u00b0", "pct": min(1.0, turned / need)},
                    {"label": "Bank", "now": f"{abs(tel.bank):.0f}\u00b0",
                     "target": f"{d['bank_lo']}\u2013{d['bank_hi']}\u00b0",
                     "ok": d["bank_lo"] - 2 <= abs(tel.bank) <= d["bank_hi"] + 2}]
        if p == "CLIMB":
            up = max(0.0, alt - e["alt"])
            return [{"label": "Climbed", "now": f"{_r(up, 10)} ft",
                     "target": f"{d['climb_lo']}\u2013{d['climb_hi']} ft",
                     "pct": min(1.0, up / max(1.0, self.climb_ft - 100))},
                    {"label": "Nose", "now": f"{pitch:+.0f}\u00b0", "target": "5\u201310\u00b0 up",
                     "ok": 4 <= pitch <= 11},
                    {"label": "Speed", "now": f"{tel.ias:.0f} kt",
                     "target": f"{d['spd_lo']}\u2013{d['spd_hi']} kt",
                     "ok": d["spd_lo"] - 3 <= tel.ias <= d["spd_hi"] + 3}]
        if p == "DESCEND":
            down = max(0.0, e["alt"] - alt)
            return [{"label": "Descended", "now": f"{_r(down, 10)} ft",
                     "target": f"{d['descend_lo']}\u2013{d['descend_hi']} ft",
                     "pct": min(1.0, down / max(1.0, self.descend_ft - 100))},
                    {"label": "Nose", "now": f"{pitch:+.0f}\u00b0", "target": "2\u20135\u00b0 down",
                     "ok": -6 <= pitch <= -1}]
        if p in ("PITCH_UP", "PITCH_DOWN"):
            up = p == "PITCH_UP"
            moved = max(0.0, (alt - e["alt"]) if up else (e["alt"] - alt))
            return [{"label": "Climbed" if up else "Descended", "now": f"{_r(moved, 10)} ft",
                     "target": f"{d['pitch_lo']}\u2013{d['pitch_hi']} ft",
                     "pct": min(1.0, moved / max(1.0, self.pitch_ft - 50))},
                    {"label": "Nose", "now": f"{pitch:+.0f}\u00b0",
                     "target": "about 5\u00b0 up" if up else "about 5\u00b0 down",
                     "ok": (2 <= pitch <= 9) if up else (-9 <= pitch <= -1)},
                    {"label": "Speed", "now": f"{tel.ias:.0f} kt",
                     "target": "falling" if up else "rising"}]
        if p in ROLL_STEPS:
            bank_ok = d["bank_lo"] - 2 <= abs(tel.bank) <= d["bank_hi"] + 2
            if self._stage == 0:
                return [{"label": "Bank", "now": f"{abs(tel.bank):.0f}\u00b0",
                         "target": f"{d['bank_lo']}\u2013{d['bank_hi']}\u00b0", "ok": bank_ok},
                        {"label": "Hold it", "now": f"{self._held:.0f} s",
                         "target": f"{self.roll_hold_s:.0f} s",
                         "pct": min(1.0, self._held / self.roll_hold_s)}]
            return [{"label": "Wings level", "now": f"{abs(tel.bank):.0f}\u00b0",
                     "target": "under 5\u00b0", "ok": abs(tel.bank) < 5}]
        if p == "POWER":
            if self.mock:
                up = max(0.0, alt - e["alt"])
                return [{"label": "Climbed", "now": f"{_r(up, 10)} ft", "target": "100 ft",
                         "pct": min(1.0, up / 100.0)}]
            t0 = e.get("throttle", 0.0)
            pct = f"{tel.throttle * 100:.0f}%"
            if self._stage == 0:
                return [{"label": "Throttle", "now": pct,
                         "target": f"{min(100, round((t0 + self.power_throttle) * 100))}%+",
                         "pct": min(1.0, max(0.0, tel.throttle - t0) / self.power_throttle)},
                        {"label": "Nose", "now": f"{pitch:+.0f}\u00b0", "target": "watch it rise"}]
            return [{"label": "Throttle back", "now": pct, "target": f"about {t0 * 100:.0f}%",
                     "ok": abs(tel.throttle - t0) <= 0.08},
                    {"label": "Nose", "now": f"{pitch:+.0f}\u00b0", "target": "watch it settle"}]
        if p in ("YAW_LEFT", "YAW_RIGHT"):
            want = -1.0 if p == "YAW_LEFT" else 1.0
            swung = max(0.0, self._turned * want)
            return [{"label": "Nose swung", "now": f"{_r(swung, 1)}\u00b0",
                     "target": f"{self.yaw_deg:.0f}\u00b0", "pct": min(1.0, swung / self.yaw_deg)},
                    {"label": "Bank", "now": f"{abs(tel.bank):.0f}\u00b0", "target": "watch it roll"}]
        if p == "TRIM":
            rows = [{"label": "Steady", "now": f"{self._held:.0f} s", "target": f"{self.trim_s:.0f} s",
                     "pct": min(1.0, self._held / self.trim_s)},
                    {"label": "Climb/descent", "now": f"{_r(tel.vs, 50):+} fpm", "target": "under 300",
                     "ok": abs(tel.vs) < 300}]
            if not self.mock:
                rows.append({"label": "Stick", "now": f"{abs(tel.elevator) * 100:.0f}%",
                             "target": "hands off", "ok": abs(tel.elevator) < 0.1})
            return rows
        if p in self.speeds:
            lo, hi = (d["slow_lo"], d["slow_hi"]) if p == "SLOW_CRUISE" else (d["fast_lo"], d["fast_hi"])
            return [{"label": "Steady", "now": f"{self._held:.0f} s", "target": f"{self.speed_s:.0f} s",
                     "pct": min(1.0, self._held / self.speed_s)},
                    {"label": "Speed", "now": f"{tel.ias:.0f} kt", "target": f"{lo}\u2013{hi} kt",
                     "ok": lo <= tel.ias <= hi}]
        height_row = {"label": "Height", "now": f"{_r(alt, 10):,} ft",
                      "target": f"\u00b1{self.height_band:.0f} ft",
                      "ok": abs(alt - e.get("alt", alt)) < self.height_band}
        if p in LEVEL_OFF_STEPS:
            tgt = e.get("tgt", alt)
            if self._stage == 0:
                span = max(1.0, abs(tgt - e.get("alt", alt)))
                rows = [{"label": "Height", "now": f"{_r(alt, 10):,} ft", "target": f"{tgt:,.0f} ft",
                         "pct": min(1.0, max(0.0, 1.0 - abs(tgt - alt) / span))}]
                if p == "CLIMB_TO":
                    rows.append({"label": "Speed", "now": f"{tel.ias:.0f} kt",
                                 "target": f"{d['spd_lo']}\u2013{d['spd_hi']} kt",
                                 "ok": d["spd_lo"] - 3 <= tel.ias <= d["spd_hi"] + 3})
                return rows
            return [{"label": "Level at", "now": f"{_r(alt, 10):,} ft",
                     "target": f"{tgt:,.0f} \u00b1{self.level_band:.0f} ft",
                     "ok": abs(alt - tgt) < self.level_band},
                    {"label": "Steady", "now": f"{self._held:.0f} s", "target": f"{self.level_hold_s:.0f} s",
                     "pct": min(1.0, self._held / self.level_hold_s)}]
        if p == "GLIDE":
            down = max(0.0, e.get("alt", alt) - alt)
            rows = [{"label": "Glided down", "now": f"{_r(down, 10)} ft", "target": f"{self.glide_ft:.0f} ft",
                     "pct": min(1.0, down / self.glide_ft)},
                    {"label": "Speed", "now": f"{tel.ias:.0f} kt",
                     "target": f"{d['glide_lo']}\u2013{d['glide_hi']} kt",
                     "ok": d["glide_lo"] <= tel.ias <= d["glide_hi"]}]
            if not self.mock:
                rows.append({"label": "Throttle", "now": f"{tel.throttle * 100:.0f}%", "target": "idle",
                             "ok": tel.throttle <= 0.15})
            return rows
        if p == "SLOW_FLIGHT":
            return [{"label": "Steady", "now": f"{self._held:.0f} s", "target": f"{self.sf_hold_s:.0f} s",
                     "pct": min(1.0, self._held / self.sf_hold_s)},
                    {"label": "Speed", "now": f"{tel.ias:.0f} kt", "target": f"{d['sf_lo']}\u2013{d['sf_hi']} kt",
                     "ok": d["sf_lo"] <= tel.ias <= d["sf_hi"]},
                    height_row]
        if p == "STALL":
            if self._stage == 0:
                start = e.get("ias", tel.ias)
                return [{"label": "Speed", "now": f"{tel.ias:.0f} kt", "target": f"down to {self.stall_kt:.0f}",
                         "pct": min(1.0, max(0.0, (start - tel.ias) / max(1.0, start - self.stall_kt)))},
                        {"label": "Nose", "now": f"{pitch:+.0f}\u00b0", "target": "slowly up"}]
            return [{"label": "Speed", "now": f"{tel.ias:.0f} kt", "target": f"{self.recover_kt:.0f}+ kt",
                     "ok": tel.ias >= self.recover_kt},
                    {"label": "Climb/descent", "now": f"{_r(tel.vs, 50):+} fpm", "target": "stop the sink",
                     "ok": tel.vs >= -200}]
        if p in BANKED_TURNS:
            bank, deg = self.turn_target(p)
            k = "med_" if p in MEDIUM_TURNS else "steep_"
            if self._stage == 1:
                return [{"label": "Wings level", "now": f"{abs(tel.bank):.0f}\u00b0",
                         "target": "under 8\u00b0", "ok": abs(tel.bank) < 8}, height_row]
            return [{"label": "Turned", "now": f"{_r(self._progress, 5)}\u00b0",
                     "target": f"{deg - 20:.0f}\u2013{deg:.0f}\u00b0",
                     "pct": min(1.0, self._progress / max(1.0, deg - 20))},
                    {"label": "Bank", "now": f"{abs(tel.bank):.0f}\u00b0",
                     "target": f"{d[k + 'bank_lo']}\u2013{d[k + 'bank_hi']}\u00b0",
                     "ok": abs(tel.bank) >= bank - 8},
                    height_row]
        if p == "FIND_AIRFIELD":
            f = self._field(tel)
            rel = f["rel"]
            on_nose = abs(rel) <= self.point_deg and f["dist_m"] <= self.point_max_m
            rows = [{"label": "Paya Lebar",
                     "now": ("on the nose" if on_nose else
                             f"turn {'right' if rel > 0 else 'left'} {_r(abs(rel), 5)}\u00b0"),
                     "target": f"within {self.point_deg:.0f}\u00b0", "ok": on_nose},
                    {"label": "Distance", "now": f"~{f['dist_nm']} nm", "target": ""}]
            if on_nose:
                rows.append({"label": "Hold it", "now": f"{self._pointing:.0f} s",
                             "target": f"{self.point_s:.0f} s",
                             "pct": min(1.0, self._pointing / self.point_s)})
            return rows
        return []

    def todo(self) -> dict | None:
        """To-do list for the pilot view: every step with its status; the
        active one carries its short checklist lines and live progress."""
        order = self.detector.ORDER[1:-1]
        if self.phase == "BRIEFING":
            cur = -1                              # briefing: preview, nothing active yet
        else:
            cur = order.index(self.phase) if self.phase in order else len(order)
        steps = []
        for i, sid in enumerate(order):
            note = self.notes.get(sid) or {}
            done = "skipped" if sid in self.skipped else "done"
            row = {"id": sid, "title": note.get("title") or STEP_TITLES.get(sid, sid),
                   "status": done if i < cur else ("active" if i == cur else "todo")}
            if i == cur and self._data:
                lines = []
                for ln in note.get("todo") or []:
                    try:
                        lines.append(ln.format(**self._data))
                    except (KeyError, IndexError, ValueError):
                        lines.append(ln)
                row["lines"] = lines
                row["meters"] = self._meters
                if self._stage == 1:
                    msg = {"POWER": "Now bring the throttle back",
                           "STALL": "Stall! Nose down, full power, then level off"}.get(self.phase)
                    if self.phase in (*ROLL_STEPS, *BANKED_TURNS):
                        msg = "Now roll the wings level"
                    elif self.phase in LEVEL_OFF_STEPS:
                        msg = "Now level off and hold it there"
                    if msg:
                        row["progress"] = msg
                if self.held:
                    row["progress"] = "Listen to MAVIS \u2014 aircraft held"
            steps.append(row)
        return {"steps": steps, "complete": self.phase == "DEBRIEF",
                "done_text": ("You found Paya Lebar. After your score you're put back at "
                              "the start of the runway." if self.finds_field else
                              "All done. After your score you're put back at the start of the runway.")}

    @property
    def finds_field(self) -> bool:
        """Does this lesson end by finding Paya Lebar (Flight Familiarization)?"""
        return "FIND_AIRFIELD" in self.detector.ORDER


class FamiliarizationScorer:
    """Practice score for the familiarization flight -- never posted to the
    leaderboard. Metrics (0-100): exercises (share of the lesson's exercises
    flown rather than timed out), altitude hold (straight & level), bank control
    (turns), climb speed, speed hold (speed changes), smoothness -- each only
    when the lesson has an exercise for it. 'Incomplete' until the trainee reaches
    the airfield. main.py hands over the engine's skipped steps before
    finalize()."""
    WEIGHTS = {"exercises": 25, "altitude": 25, "bank": 20, "climb_speed": 10,
               "speed": 15, "level_off": 20, "smoothness": 20}

    def __init__(self, lesson: dict, aircraft: dict):
        ex = lesson.get("exercises") or {}
        self.EXERCISES = tuple(s for s in lesson_steps(lesson) if s != "FIND_AIRFIELD")
        self.bank = float(ex.get("turn_bank_deg", 20))
        self.speeds = {"SLOW_CRUISE": float(ex.get("slow_kt", 80)),
                       "FAST_CRUISE": float(ex.get("fast_kt", 105))}
        self.vy = float(aircraft["v_speeds"]["vy"])
        # the same lesson keys the engine reads, so score and to-do agree
        self.level_ft = {"CLIMB_TO": float(ex.get("climb_to_ft", 500)),
                         "DESCEND_TO": float(ex.get("descend_to_ft", 500))}
        self.speeds.update({"SLOW_FLIGHT": float(ex.get("slow_flight_kt", 55)),
                            "GLIDE": float(ex.get("glide_kt", 68))})
        self.turn_bank = {**{s: float(ex.get("medium_bank_deg", 30)) for s in MEDIUM_TURNS},
                          **{s: float(ex.get("steep_bank_deg", 45)) for s in STEEP_TURNS}}
        # only the metrics this lesson's exercises can produce go on the card
        ex_set = set(self.EXERCISES)
        self.metrics = {"smoothness"}
        if ex_set & {"STRAIGHT_LEVEL", *HOLD_HEIGHT_STEPS}:
            self.metrics.add("altitude")
        if ex_set & {"TURN_LEFT", "TURN_RIGHT", *ROLL_STEPS, *BANKED_TURNS}:
            self.metrics.add("bank")
        if ex_set & {"CLIMB", "CLIMB_TO"}:
            self.metrics.add("climb_speed")
        if ex_set & set(self.speeds):
            self.metrics.add("speed")
        if ex_set & set(LEVEL_OFF_STEPS):
            self.metrics.add("level_off")
        cfg = lesson.get("scoring", {}) or {}
        self.s = {**DEFAULTS["scales"], "bank": 8.0, **cfg.get("scales", {})}
        self.grade_bands = cfg.get("grade_bands", DEFAULTS["grade_bands"])
        self.reset()

    def reset(self) -> None:
        self._acc = {k: [0.0, 0] for k in self.WEIGHTS if k != "exercises"}
        self._seen: set[str] = set()
        self.skipped: set[str] = set()
        self._prev_ctrl = None
        self._phase = None
        self._phase_t = 0.0
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
        if phase in self.EXERCISES:
            self._seen.add(phase)
        if phase != self._phase:
            self._phase, self._ref_alt = phase, tel.get("alt_msl", 0.0)
            self._phase_t = time.time()
        settled = time.time() - self._phase_t
        alt, vs = tel.get("alt_msl", 0.0), tel.get("vs", 0.0)
        # speed changes: judged once the new speed has had time to settle
        # (the glide once it's actually gliding)
        if phase in self.speeds and settled > 15 and (phase != "GLIDE" or vs < -200):
            self._add("speed", abs(tel.get("ias", 0.0) - self.speeds[phase]))
        if phase in ("STRAIGHT_LEVEL", *HOLD_HEIGHT_STEPS):
            self._add("altitude", abs(alt - self._ref_alt))
        # bank accuracy once established in the turn -- rolling in and out
        # passes through small angles on the way and isn't an error
        want_bank = self.turn_bank.get(phase, self.bank)
        if (phase in ("TURN_LEFT", "TURN_RIGHT", *ROLL_STEPS, *BANKED_TURNS)
                and abs(tel.get("bank", 0.0)) >= want_bank / 2):
            self._add("bank", abs(abs(tel.get("bank", 0.0)) - want_bank))
        # climb speed once actually climbing and settled (cruise speed bleeds
        # off for the first several seconds of a correct climb)
        if phase in ("CLIMB", "CLIMB_TO") and vs > 200 and settled > 10:
            self._add("climb_speed", abs(tel.get("ias", 0.0) - self.vy))
        # level-off: how close to the set height once it's levelled
        if phase in LEVEL_OFF_STEPS and abs(vs) < 200:
            err = abs(alt - level_target(phase, self._ref_alt, self.level_ft))
            if err < 300:
                self._add("level_off", err)
        roll = ctrl["roll"] if ctrl and ctrl.get("available") else tel.get("aileron", 0.0)
        pitch = ctrl["pitch"] if ctrl and ctrl.get("available") else -tel.get("elevator", 0.0)
        if self._prev_ctrl is not None:
            self._add("smoothness", abs(roll - self._prev_ctrl[0]) + abs(pitch - self._prev_ctrl[1]))
        self._prev_ctrl = (roll, pitch)

    def finalize(self, name: str, mode: str, lesson_name: str, practice: bool = False,
                 score_caps: bool | None = None) -> ScoreCard:
        scale = {"altitude": self.s["altitude"], "bank": self.s["bank"],
                 "climb_speed": self.s["speed"], "speed": self.s["speed"],
                 "level_off": self.s["altitude"], "smoothness": self.s["smoothness"]}
        scores = {}
        attempted = self._seen | (self.skipped & set(self.EXERCISES))
        scores["exercises"] = (round(100 * len(attempted - self.skipped) / len(attempted))
                               if attempted else None)
        for k, (tot, n) in self._acc.items():
            if k in self.metrics:
                scores[k] = round(_curve(tot / n, scale[k])) if n else None
        flown = {k: v for k, v in scores.items() if v is not None}
        wsum = sum(self.WEIGHTS[k] for k in flown) or 1
        total = round(sum(v * self.WEIGHTS[k] for k, v in flown.items()) / wsum) if flown else 0
        # an exercise left undone can't come out top-grade: each one caps the
        # total 10 lower, starting at 85 (a B)
        n_skipped = len(self.skipped & set(self.EXERCISES))
        capped = n_skipped > 0 and total > 95 - 10 * n_skipped
        if capped:
            total = 95 - 10 * n_skipped
        grade = "F"
        for thr, g in self.grade_bands:
            if total >= thr:
                grade = g
                break
        return ScoreCard(name or "", mode, lesson_name, time.time(), scores,
                         self.retries, total, grade, capped, True,
                         incomplete=not self.reached)
