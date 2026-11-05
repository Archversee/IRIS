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
from .monitors import Monitors, band, roll_out_lead
from .phase_detector import PhaseDetector, hdg_diff
from .scoring import DEFAULTS, ScoreCard, _curve
from .meters import standard_all
from . import standards

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


def _clock(tel: Telemetry) -> str:
    """The sim's local time as a pilot writes it: 0942."""
    h = int(tel.local_time_h) % 24
    return f"{h:02d}{int((tel.local_time_h % 1) * 60) % 60:02d}"


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
               "UPSET_HIGH_LEFT": "Nose high, banked left", "UPSET_HIGH_RIGHT": "Nose high, banked right",
               "UPSET_LOW_LEFT": "Nose low, banked left", "UPSET_LOW_RIGHT": "Nose low, banked right",
               "CHECKPOINT_1": "Checkpoint 1", "CHECKPOINT_2": "Checkpoint 2",
               "CHECKPOINT_3": "Checkpoint 3", "CHECKPOINT_4": "Checkpoint 4",
               "DIVERT_TURN": "Turn for the new airfield", "DIVERT_TRACK": "Fly to the new airfield",
               "LOST_CLIMB": "Lost: climb",
               "TIMED_TURN_LEFT": "Timed turn left", "TIMED_TURN_RIGHT": "Timed turn right",
               "COMPASS_TURN": "Turn by the compass",
               "FIND_AIRFIELD": "Find Paya Lebar & point at it"}

DEFAULT_STEPS = ["STRAIGHT_LEVEL", "TURN_LEFT", "TURN_RIGHT", "CLIMB", "DESCEND", "FIND_AIRFIELD"]
ROLL_STEPS = ("ROLL_LEFT", "ROLL_RIGHT")
# How an unusual-attitude recovery went, said once it's recovered
UPSET_FEEDBACK = ("FAM_UPSET_GOOD", "FAM_UPSET_FIX_HIGH", "FAM_UPSET_FIX_LOW",
                  "FAM_UPSET_STALLED", "FAM_UPSET_FAST", "FAM_UPSET_NOT_SET")
# Spoken the moment the first half of a two-part step is done, so the trainee
# knows when to come back: roll level, feet level, throttle back, level off.
PART_CUES = ("FAM_ROLL_OUT", "FAM_CENTRE_PEDALS", "FAM_THROTTLE_BACK", "FAM_LEVEL_OFF",
             *UPSET_FEEDBACK)
LEVEL_OFF_STEPS = ("CLIMB_TO", "DESCEND_TO")            # to a set height, then level off there
MEDIUM_TURNS = ("MEDIUM_TURN_LEFT", "MEDIUM_TURN_RIGHT")
STEEP_TURNS = ("STEEP_TURN_LEFT", "STEEP_TURN_RIGHT")
BANKED_TURNS = (*MEDIUM_TURNS, *STEEP_TURNS)
# Unusual attitudes: MAVIS puts the aircraft there (runtime _fam_upset), the
# trainee recovers. Nose high (speed falling): power, nose down, wings level.
# Nose low (speed rising): power off, wings level, then ease the nose up.
UPSET_HIGH = ("UPSET_HIGH_LEFT", "UPSET_HIGH_RIGHT")
UPSET_LOW = ("UPSET_LOW_LEFT", "UPSET_LOW_RIGHT")
UPSET_STEPS = (*UPSET_HIGH, *UPSET_LOW)
# Navigation: fly to a place the lesson names (`checkpoints`, `diversion`).
CHECKPOINTS = ("CHECKPOINT_1", "CHECKPOINT_2", "CHECKPOINT_3", "CHECKPOINT_4")
TARGET_STEPS = (*CHECKPOINTS, "DIVERT_TURN", "DIVERT_TRACK")
# Partial panel: 90 degrees at standard rate (3 degrees a second) by the clock
TIMED_TURNS = ("TIMED_TURN_LEFT", "TIMED_TURN_RIGHT")
# Height is held (and scored) on these, whatever else they ask
HOLD_ALT_STEPS = (*TARGET_STEPS, *TIMED_TURNS, "COMPASS_TURN")
# To-do card: only what the step asks for, no reference height row
NO_HEIGHT_ROW = (*UPSET_STEPS, *HOLD_ALT_STEPS, "LOST_CLIMB")
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


def speed_bands(ex: dict) -> dict:
    """Slow down / speed up / glide: the band that counts, (lo, hi) kt. A
    lesson can set slow_band_kt / fast_band_kt / glide_band_kt; otherwise
    slow_kt / fast_kt / glide_kt +/- 5."""
    out = {}
    for step, key, default in (("SLOW_CRUISE", "slow", 80), ("FAST_CRUISE", "fast", 105),
                               ("GLIDE", "glide", 68), ("SLOW_FLIGHT", "slow_flight", 55)):
        mid, band = float(ex.get(f"{key}_kt", default)), ex.get(f"{key}_band_kt")
        out[step] = (float(band[0]), float(band[1])) if band else (mid - 5, mid + 5)
    return out


def climb_band(ex: dict, vy: float) -> tuple[float, float]:
    """Climb speed band (kt): the lesson's climb_speed_band_kt, else Vy +/- 5."""
    b = ex.get("climb_speed_band_kt")
    return (float(b[0]), float(b[1])) if b else (vy - 5, vy + 5)


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
        self.step_max_s = float(ex.get("step_timeout_s", 180))
        self.hint_s = float(ex.get("hint_every_s", 35))
        self.alt_band = float(ex.get("altitude_band_ft", 200))
        self.pitch_ft = float(ex.get("pitch_change_ft", 200))
        self.yaw_deg = float(ex.get("yaw_deg", 10))
        self.roll_hold_s = float(ex.get("roll_hold_s", 2))
        self.power_throttle = float(ex.get("power_throttle_change", 0.2))   # 0..1 lever travel
        self.trim_s = float(ex.get("trim_hold_s", 10))
        self.trim_vs = float(ex.get("trim_max_vs_fpm", 500))   # "steady": climb/descent under this
        self.speed_s = float(ex.get("speed_hold_s", 10))
        self.speeds = {"SLOW_CRUISE": float(ex.get("slow_kt", 80)),
                       "FAST_CRUISE": float(ex.get("fast_kt", 105))}
        self.speed_band = speed_bands(ex)
        self.climb_band = climb_band(ex, self.vy)
        # climbs & descents
        self.level_ft = {"CLIMB_TO": float(ex.get("climb_to_ft", 500)),
                         "DESCEND_TO": float(ex.get("descend_to_ft", 500))}
        self.level_band = float(ex.get("level_band_ft", 100))     # "level at the target" means within this
        self.level_hold_s = float(ex.get("level_hold_s", 5))
        self.glide_kt = float(ex.get("glide_kt", 68))
        self.glide_ft = float(ex.get("glide_ft", 300))
        # the up-front demo climbs / descends / glides only this far, so the
        # level-off fits in a short demo; the trainee's turn uses the full amount
        self.demo_level_ft = {"CLIMB_TO": float(ex.get("demo_climb_to_ft", 100)),
                              "DESCEND_TO": float(ex.get("demo_descend_to_ft", 100))}
        self.demo_glide_ft = float(ex.get("demo_glide_ft", 100))
        # slow flight & stalls
        vs1 = float(aircraft["v_speeds"].get("vs1", 48))
        self.sf_kt = float(ex.get("slow_flight_kt", 55))
        self.sf_hold_s = float(ex.get("slow_flight_hold_s", 10))
        self.stall_kt = float(ex.get("stall_kt", vs1 + 2))
        self.recover_kt = float(ex.get("recover_kt", 65))
        self.recover_sink = float(ex.get("recover_max_sink_fpm", 200))  # "sink stopped": slower than this
        self.vs1 = vs1                                                   # the real stall speed (demo guard)
        # medium & steep turns: (bank, degrees of turn)
        self.turn_spec = {"MEDIUM": (float(ex.get("medium_bank_deg", 30)), float(ex.get("medium_turn_deg", 180))),
                          "STEEP": (float(ex.get("steep_bank_deg", 45)), float(ex.get("steep_turn_deg", 360)))}
        self.height_band = float(ex.get("height_band_ft", 200))  # where height counts: within this
        # unusual attitudes: (pitch + up, bank, speed) MAVIS sets each one up at
        self.upset_spec = {
            "HIGH": (float(ex.get("nose_high_pitch_deg", 20)), float(ex.get("nose_high_bank_deg", 30)),
                     float(ex.get("nose_high_ias_kt", 80))),
            "LOW": (-float(ex.get("nose_low_pitch_deg", 15)), float(ex.get("nose_low_bank_deg", 45)),
                    float(ex.get("nose_low_ias_kt", 115)))}
        self.recovered_hold_s = float(ex.get("recovered_hold_s", 2))
        self.upset_max_kt = float(ex.get("nose_low_max_kt", 125))   # "speed back under control"
        self.feedback_s = float(ex.get("feedback_s", 4))            # "well done" before the next one
        self.upset_seen_s = float(ex.get("upset_seen_s", 6))        # no upset by then: it didn't set up
        self.vne = float(aircraft["v_speeds"].get("vne", 163))
        self.upset_results: dict[str, dict] = {}     # step -> how its recovery went (scorer)
        # navigation
        self.checkpoints = lesson.get("checkpoints") or []
        self.diversion = lesson.get("diversion") or {}
        self.track_band = float(ex.get("track_band_deg", 15))      # on heading: within this (ACS +/-15)
        self.lost_climb_ft = float(ex.get("lost_climb_ft", 500))
        # partial panel
        self.timed_deg = float(ex.get("timed_turn_deg", 90))
        self.timed_s = self.timed_deg / 3.0                        # standard rate
        self.compass_deg = float(ex.get("compass_turn_deg", 90))   # + right
        self.heading_ok = float(ex.get("heading_ok_deg", 10))      # a turn within this is good
        self.vacuum_failed = bool(lesson.get("vacuum_failure"))    # attitude gauge is out
        self.results: dict[str, dict] = {}           # step -> {"heading_err": deg} (scorer)
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
        self.upset_results.clear()
        self.results.clear()
        self._last_fire.clear()
        self._reset_step()

    def load_phase(self, phase: str, circuit: int):
        """Checkpoint retry: re-enter the step fresh (re-announced on next tick)."""
        self.detector.advance(phase)
        order = self.detector.ORDER
        self.skipped -= set(order[order.index(phase):])
        for s in order[order.index(phase):]:
            self.upset_results.pop(s, None)
            self.results.pop(s, None)
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

    def bands(self) -> dict:
        """Allowed ranges for the Live Monitor's dials, for the step being
        flown -- the same numbers its to-do list and checks use: the step's
        speed band (else the 58 / 140 kt safety limits), height held within
        height_band_ft (a level-off: level_band_ft of the target), the turn's
        bank band (else wings level, or the steep-bank limit), and the trim
        step's climb / descent limit. Altitudes are MSL; bank + = right."""
        p, e, out = self.phase, self._entry, {}
        if p in ("BRIEFING", "DEBRIEF", "FIND_AIRFIELD") or not e:
            return out
        if p in self.speed_band:
            lo, hi = self.speed_band[p]
            out["speed"] = band(lo, hi, (lo + hi) / 2)
        elif p in ("CLIMB", "CLIMB_TO") and self._stage == 0:
            lo, hi = self.climb_band
            out["speed"] = band(lo, hi, (lo + hi) / 2)
        elif p == "STALL":
            out["speed"] = band(lo=self.recover_kt, kind="limit") if self._stage else band(target=self.stall_kt)
        else:
            out["speed"] = band(58, 140, kind="limit")
        if p in LEVEL_OFF_STEPS and e.get("tgt") is not None:
            out["alt"] = band(e["tgt"] - self.level_band, e["tgt"] + self.level_band, e["tgt"])
        elif p in LEVEL_STEPS and e.get("alt") is not None:
            out["alt"] = band(e["alt"] - self.height_band, e["alt"] + self.height_band, e["alt"])
        elif self._target_alt() is not None:
            out["alt"] = band(target=self._target_alt())
        want = turn_dir(p)
        if p in BANKED_TURNS and self._stage == 0:
            bank, _ = self.turn_target(p)
            lo, hi = sorted((want * (bank - 7), want * (bank + 7)))
            out["bank"] = band(lo, hi, want * bank)
        elif p in ("TURN_LEFT", "TURN_RIGHT") and self._stage == 0:
            lo, hi = sorted((want * (self.bank - 7), want * (self.bank + 7)))
            out["bank"] = band(lo, hi, want * self.bank)
        elif p in ("STRAIGHT_LEVEL", "TRIM", "SLOW_CRUISE", "FAST_CRUISE", "SLOW_FLIGHT")                 or (p in BANKED_TURNS and self._stage == 1):
            out["bank"] = band(-6, 6, 0)
        else:
            lim = 58 if p in STEEP_TURNS else 40
            out["bank"] = band(-lim, lim, kind="limit")
        if p == "TRIM":
            out["vs"] = band(-self.trim_vs, self.trim_vs, 0)
        elif p == "STALL" and self._stage:
            out["vs"] = band(lo=-self.recover_sink, kind="limit")
        return out

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
        self._upset: dict = {}                   # unusual attitude: the recovery as it goes
        self._done_t = None                      # step finished: MAVIS is saying how it went
        self._err = [0.0, 0.0]                   # diversion: off-heading error, time

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
        if self.phase in LEVEL_STEPS or self.phase in UPSET_STEPS or self.phase in HOLD_ALT_STEPS:
            return a
        if self.phase == "LOST_CLIMB":
            return self._entry.get("tgt")
        return None

    def level_target(self, step: str, alt: float, demo: bool = False) -> float:
        return level_target(step, alt, self.demo_level_ft if demo else self.level_ft)

    def turn_target(self, step: str) -> tuple[float, float]:
        """(bank, degrees of turn) for a medium or steep turn."""
        return self.turn_spec["MEDIUM" if step in MEDIUM_TURNS else "STEEP"]

    def upset_attitude(self, step: str) -> dict:
        """Where MAVIS puts the aircraft for an unusual-attitude step: pitch
        (+ nose up), bank (+ right) and speed."""
        pitch, bank, ias = self.upset_spec["HIGH" if step in UPSET_HIGH else "LOW"]
        return {"pitch": pitch, "bank": turn_dir(step) * bank, "ias": ias}

    def _place(self, p: str) -> dict | None:
        """The place a navigation step flies to: name, lat, lon, radius_nm, look."""
        if p in CHECKPOINTS:
            i = CHECKPOINTS.index(p)
            return self.checkpoints[i] if i < len(self.checkpoints) else None
        return (self.diversion or None) if p in ("DIVERT_TURN", "DIVERT_TRACK") else None

    def _target(self, p: str, tel: Telemetry) -> dict:
        """Where the step's place is from here, in the words MAVIS says it."""
        pl = self._place(p)
        if not pl:
            return {}
        brg, dist = bearing_dist(tel.lat, tel.lon, float(pl["lat"]), float(pl["lon"]))
        rel = hdg_diff(brg, tel.hdg)
        nm = dist / M_PER_NM
        return {"tgt_name": pl["name"], "tgt_look": pl.get("look", ""), "tgt_hdg": _hdg(brg),
                "tgt_dist_nm": max(1, round(nm)), "tgt_clock": clock_position(brg - tel.hdg),
                "tgt_dir": "right" if rel > 0 else "left",
                "tgt_min": max(1, round(nm / max(60.0, tel.ias) * 60)),
                "rel": rel, "dist_m": dist, "radius_m": float(pl.get("radius_nm", 0.5)) * M_PER_NM}

    def _snap_entry(self, tel: Telemetry) -> None:
        """What the step measures from: its height and heading as it starts."""
        self._entry.update(alt=tel.alt_msl, hdg=tel.hdg, throttle=tel.throttle, ias=tel.ias)
        if self.phase == "LOST_CLIMB":
            self._entry["tgt"] = round((tel.alt_msl + self.lost_climb_ft) / 100.0) * 100.0
        if self.phase == "COMPASS_TURN":
            self._entry["tgt"] = float(_hdg(tel.hdg + self.compass_deg))

    def rebase(self, tel: Telemetry) -> dict:
        """A step that teleports: the aircraft has just moved, so measure from
        here and word the step from here. Returns its new data."""
        self._snap_entry(tel)
        self._data = self.step_data(self.phase, tel)
        self._meters = standard_all(self._readout(tel))
        note = self.notes.get(self.phase) or {}
        return {**self._data, "say_meters": [] if note.get("say_targets") is False else self._meters}

    def _finish(self, code: str, data: dict | None = None) -> None:
        """Say how the step went; it ends once that's had time to be said."""
        self._feedback(code, data)
        self._done_t = time.time()

    def _feedback(self, code: str, data: dict | None = None) -> None:
        """How a step went. Untagged (phase None): the next step starting must
        not drop it or cut it off (coach.new_phase) -- it's said in full."""
        self._cue(code, {**(data or {}), "phase": None})

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
        self._snap_entry(tel)
        self._held = self._turned = self._progress = self._prev_turned = 0.0
        self._stage = 0
        self._events = []
        self._upset = {}
        self._done_t = None
        self._err = [0.0, 0.0]
        self._last_hint = now
        p = self.phase
        f = self._field(tel)
        data = self.step_data(p, tel)
        self._data = data
        self._meters = standard_all(self._readout(tel))   # targets visible while it's explained
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
        if p in UPSET_STEPS:
            mock = {"hdg_delta": 0, "spd": 95}       # mock student: straight back to level
        if p in TARGET_STEPS:
            mock = {"hdg_delta": data.get("rel", 0.0), "spd": 95}
        if p in TIMED_TURNS:
            mock = {"turn_deg": turn_dir(p) * self.timed_deg, "bank": 15, "spd": 95}
        if p == "COMPASS_TURN":
            mock = {"turn_deg": self.compass_deg, "bank": 15, "spd": 95}
        if p == "LOST_CLIMB":
            mock = {"alt_delta_ft": self.lost_climb_ft + 50, "spd": self.vy}
        if mock is not None:
            data["mock"] = mock
        out = [CoachEvent(f"PHASE_ENTER_{p}", "info", data, cooldown_s=0)]
        note = self.notes.get(p)
        if note:
            out.append(CoachEvent("LESSON_NOTE", "info",
                                  # say_targets: false -- the line already gives what to aim for
                                  {**data, **note, "say_meters": (
                                      [] if note.get("say_targets") is False else self._meters)},
                                  cooldown_s=0))
        log.info("familiarization step -> %s (alt %.0f, hdg %.0f, field ~%s nm, bearing from %s)",
                 p, tel.alt_msl, tel.hdg, f["dist_nm"], f["brg_lo"])
        return out

    def preview(self, phase: str, tel: Telemetry, demo: bool = False) -> dict:
        """A step's LESSON_NOTE data as if it began now -- what the up-front
        demonstration narrates -- without touching the lesson's progress."""
        return {**self.step_data(phase, tel, demo), **(self.notes.get(phase) or {})}

    def step_data(self, p: str, tel: Telemetry, demo: bool = False) -> dict:
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
        hp, hb, hk = self.upset_spec["HIGH"]
        lp, lb, lk = self.upset_spec["LOW"]
        return {"phase": p, "circuit": 1, "total_circuits": 1, "alt_now": _r(a, 10),
                **self._target(p, tel),
                "time_now": _clock(tel),
                "lost_ft": round(self.lost_climb_ft),
                "lost_tgt": int(round((a + self.lost_climb_ft) / 100.0) * 100),
                "timed_deg": round(self.timed_deg), "timed_s": round(self.timed_s),
                # standard-rate bank: speed / 10 + 7 (a rule of thumb), to the nearest 5
                "std_bank": _r(tel.ias / 10.0 + 7.0, 5),
                "compass_hdg": _hdg(tel.hdg + self.compass_deg),
                "compass_dir": "right" if self.compass_deg > 0 else "left",
                "high_pitch": round(hp), "high_bank": round(hb), "high_kt": round(hk),
                "low_pitch": round(-lp), "low_bank": round(lb), "low_kt": round(lk),
                "upset_max_kt": round(self.upset_max_kt),
                "tgt_alt": (round(self.level_target(p, a, demo)) if p in LEVEL_OFF_STEPS
                            else round(a)),
                "level_band": round(self.level_band), "level_hold_s": round(self.level_hold_s),
                "glide_lo": round(self.speed_band["GLIDE"][0]),
                "glide_hi": round(self.speed_band["GLIDE"][1]),
                "glide_ft": round(self.glide_ft),
                "sf_lo": round(self.speed_band["SLOW_FLIGHT"][0]),
                "sf_hi": round(self.speed_band["SLOW_FLIGHT"][1]),
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
                "spd_lo": _r(self.climb_band[0], 5), "spd_hi": _r(self.climb_band[1], 5),
                "hold_s": round(self.hold_s),
                "pitch_lo": _r(self.pitch_ft - 50, 50), "pitch_hi": _r(self.pitch_ft + 100, 50),
                "yaw_deg": round(self.yaw_deg), "trim_s": round(self.trim_s),
                "speed_s": round(self.speed_s),
                "slow_lo": round(self.speed_band["SLOW_CRUISE"][0]),
                "slow_hi": round(self.speed_band["SLOW_CRUISE"][1]),
                "fast_lo": round(self.speed_band["FAST_CRUISE"][0]),
                "fast_hi": round(self.speed_band["FAST_CRUISE"][1]),
                "trim_vs": round(self.trim_vs),
                **{k: f[k] for k in ("brg_lo", "brg_hi", "dist_nm", "clock", "field")}}

    def _achieved(self, tel: Telemetry, dt: float) -> bool:
        """Has the trainee done what the current step asks?"""
        p, e = self.phase, self._entry
        if self._done_t is not None:             # done: just saying how it went
            return time.time() - self._done_t >= self.feedback_s
        if p in CHECKPOINTS:
            t = self._target(p, tel)
            if not t:
                return True                      # the lesson names fewer checkpoints
            if t["dist_m"] <= t["radius_m"]:
                self._finish("FAM_CP_REACHED", {"tgt_name": t["tgt_name"], "time_now": _clock(tel)})
            return False
        if p == "DIVERT_TURN":
            t = self._target(p, tel)
            return not t or (abs(t["rel"]) <= self.track_band and abs(tel.bank) < 10)
        if p == "DIVERT_TRACK":
            t = self._target(p, tel)
            if not t:
                return True
            self._err[0] += max(0.0, abs(t["rel"]) - self.track_band) * dt
            self._err[1] += dt
            if t["dist_m"] <= t["radius_m"]:
                self.results[p] = {"heading_err": self._err[0] / max(1.0, self._err[1])}
                self._finish("FAM_DIVERT_DONE", {
                    "tgt_name": t["tgt_name"], "mins": max(1, round((time.time() - e["t"]) / 60.0))})
            return False
        if p == "LOST_CLIMB":
            return tel.alt_msl >= e["tgt"] - 50
        if p in TIMED_TURNS:
            return self._timed_turn(tel)
        if p == "COMPASS_TURN":
            return self._compass_turn(tel)
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
            far_enough = self._turned * want >= self.turn_deg - 20
            if self._stage == 0 and self._turned * want >= self.turn_deg - roll_out_lead(tel.bank):
                self._stage = 1
                if abs(tel.bank) >= 8:                # still banked: time to roll out
                    self._cue("FAM_ROLL_OUT")
            return far_enough and abs(tel.bank) < 8
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
                        self._cue("FAM_ROLL_OUT")
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
                    self._cue("FAM_THROTTLE_BACK")
                return False
            return abs(tel.throttle - t0) <= 0.08
        if p in ("YAW_LEFT", "YAW_RIGHT"):
            # nose swung the right way with the pedals, not the stick (the mock
            # aircraft has no rudder, so there the swing alone counts)
            want = -1.0 if p == "YAW_LEFT" else 1.0
            self._rudder = max(self._rudder, abs(tel.rudder))
            swung = self._turned * want >= self.yaw_deg and (self.mock or self._rudder >= 0.2)
            if swung:
                self._cue("FAM_CENTRE_PEDALS")
            return swung
        if p == "TRIM":
            # hands (nearly) off the stick and it stays steady -- no height target
            if abs(tel.vs) < self.trim_vs and (self.mock or abs(tel.elevator) < 0.1):
                self._held += dt
            else:
                self._held = 0.0
            return self._held >= self.trim_s
        if p in self.speeds:
            lo, hi = self.speed_band[p]
            if lo <= tel.ias <= hi:                    # the speed only
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
                    self._cue("FAM_LEVEL_OFF")
                return False
            if abs(tel.alt_msl - tgt) < self.level_band and abs(tel.vs) < 200:
                self._held += dt
            return self._held >= self.level_hold_s
        if p == "GLIDE":
            return tel.alt_msl <= e["alt"] - self.glide_ft
        if p == "SLOW_FLIGHT":
            lo, hi = self.speed_band["SLOW_FLIGHT"]
            if lo <= tel.ias <= hi and abs(tel.alt_msl - e["alt"]) < self.height_band:
                self._held += dt
            return self._held >= self.sf_hold_s
        if p == "STALL":
            if self._stage == 0:
                # stalled: down at the stall speed, or nose up and sinking anyway
                if tel.ias <= self.stall_kt or (tel.pitch > 8 and tel.vs < -200):
                    self._stage = 1
                    self._events.append(CoachEvent(
                        "FAM_STALL", "safety",
                        {"phase": p, "mock": {"spd": self.recover_kt + 10, "alt_delta_ft": 0}},
                        cooldown_s=0))
                return False
            return tel.ias >= self.recover_kt and tel.vs >= -self.recover_sink
        if p in UPSET_STEPS:
            return self._upset_recovery(tel, dt)
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
                if self._progress >= deg - roll_out_lead(tel.bank):
                    self._stage = 1
                    self._cue("FAM_ROLL_OUT")
                return False
            return abs(tel.bank) < 8
        return False

    def _upset_recovery(self, tel: Telemetry, dt: float) -> bool:
        """Unusual attitude, from the hand-over (the step is held until then).
        Stage 0: recover -- wings level, nose near the horizon, speed back in
        hand -- noting when power, nose and wings each went in, to judge the
        order (nose high: power, nose down, wings level; nose low: power off,
        wings level, then nose up). Nothing counts until the upset has actually
        been seen: a recovery from level flight isn't one. Stage 1: MAVIS says
        how it went, then the step ends (the next one teleports back up)."""
        p, u = self.phase, self._upset
        high = p in UPSET_HIGH
        now = time.time()
        if not u:                                 # first tick after the hand-over
            u.update(t0=now, th0=tel.throttle, ias_min=tel.ias, ias_max=tel.ias)
        if self._stage == 1:
            return now - u["done_t"] >= self.feedback_s
        u["ias_min"], u["ias_max"] = min(u["ias_min"], tel.ias), max(u["ias_max"], tel.ias)
        if not u.get("seen"):
            u["seen"] = self.mock or (tel.pitch >= 8 if high else tel.pitch <= -5) or abs(tel.bank) >= 15
            if not u["seen"]:
                if now - u["t0"] > self.upset_seen_s:      # never got there: not set up
                    log.warning("unusual attitude %s never seen -- moving on", p)
                    self._feedback("FAM_UPSET_NOT_SET")
                    return True
                return False
        if "power" not in u and (self.mock or (
                tel.throttle >= min(0.9, u["th0"] + 0.15) if high
                else tel.throttle <= max(0.3, u["th0"] - 0.15))):
            u["power"] = now
        if "nose" not in u and (tel.pitch <= 5 if high else tel.pitch >= -2):
            u["nose"] = now
            u["bank_at_nose"] = abs(tel.bank)
        if "wings" not in u and abs(tel.bank) < 10:
            u["wings"] = now
        speed_ok = (tel.ias >= self.recover_kt if high
                    else tel.ias <= self.upset_max_kt and tel.vs > -500)
        if abs(tel.bank) < 10 and -5 <= tel.pitch <= 8 and speed_ok:
            self._held += dt
        else:
            self._held = 0.0
        if self._held < self.recovered_hold_s:
            return False
        late = u.get("wings", now) + 1.0
        if self.mock:
            seq_ok = True
        elif high:   # power and nose down before (or as) the wings come level
            seq_ok = u.get("power", now) <= late and u.get("nose", now) <= late
        else:        # power off first; never pulling the nose up while still banked
            seq_ok = (u.get("power", now) <= u.get("nose", now)
                      and u.get("bank_at_nose", 90.0) < 15)
        res = {"high": high, "sequence_ok": seq_ok,
               "recover_s": round(now - u["t0"], 1),
               "stalled": u["ias_min"] < self.vs1,
               "overspeed": u["ias_max"] > self.vne}
        self.upset_results[p] = res
        self._stage, u["done_t"] = 1, now
        self._feedback("FAM_UPSET_GOOD" if seq_ok and not res["stalled"] and not res["overspeed"] else
                       "FAM_UPSET_STALLED" if res["stalled"] else
                       "FAM_UPSET_FAST" if res["overspeed"] else
                       "FAM_UPSET_FIX_HIGH" if high else "FAM_UPSET_FIX_LOW")
        return False

    def _timed_turn(self, tel: Telemetry) -> bool:
        """Roll in (the clock starts), MAVIS calls the time, roll out: judged on
        how far it actually turned. Nothing counts until the bank goes on."""
        u, want = self._upset, turn_dir(self.phase)
        now = time.time()
        if "t_in" not in u:
            if self.mock or tel.bank * want >= 8:
                u.update(t_in=now, turned0=self._turned)
            return False
        if self._stage == 0:
            if now - u["t_in"] >= self.timed_s:
                self._stage = 1
                self._cue("FAM_TIMED_ROLL_OUT", {"timed_s": round(self.timed_s)})
            return False
        if abs(tel.bank) < 5:
            turned = abs(self._turned - u["turned0"])
            err = turned - self.timed_deg
            self.results[self.phase] = {"heading_err": abs(err)}
            self._finish("FAM_TIMED_GOOD" if abs(err) <= self.heading_ok else
                         "FAM_TIMED_MORE" if err > 0 else "FAM_TIMED_LESS",
                         {"turned": _r(turned, 5), "timed_deg": round(self.timed_deg)})
        return False

    def _compass_turn(self, tel: Telemetry) -> bool:
        """Turn to the heading MAVIS gave, reading it off the compass. The first
        roll-out is what's judged; if it's off, MAVIS says which way to fix it."""
        u, tgt = self._upset, self._entry["tgt"]
        if abs(self._turned) < 45 or abs(tel.bank) >= 8:
            return False                         # not turned yet, or still turning
        off = hdg_diff(tgt, tel.hdg)
        if self.phase not in self.results:
            self.results[self.phase] = {"heading_err": abs(off)}
        if abs(off) <= self.heading_ok:
            self._finish("FAM_COMPASS_GOOD", {"compass_hdg": _hdg(tgt), "off": round(abs(off))})
        elif not u.get("told"):
            u["told"] = True
            self._cue("FAM_COMPASS_FIX", {"compass_hdg": _hdg(tgt), "off": round(abs(off)),
                                          "dir": "right" if off > 0 else "left"})
        return False

    def _cue(self, code: str, data: dict | None = None) -> None:
        """Queue a call to say this tick (PART_CUES, how a step went)."""
        self._events.append(CoachEvent(code, "correction", {"phase": self.phase, **(data or {})},
                                       cooldown_s=0))

    def _step_done(self, tel: Telemetry, dt: float, now: float) -> bool:
        """Done, or out of time (finding the field never times out: it's the
        lesson's goal). A timed-out step moves on but is marked skipped."""
        if self._achieved(tel, dt):
            return True
        limit = float((self.notes.get(self.phase) or {}).get("timeout_s", self.step_max_s))
        if self.phase != "FIND_AIRFIELD" and now - self._entry["t"] > limit:
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
        if self.phase in UPSET_STEPS and self._stage == 0:
            # mid-recovery: steep, slow or fast is the exercise, and "ease the
            # nose up" would contradict "wings level first"
            return out
        if tel.ias < 58 and self.phase not in ("SLOW_FLIGHT", "STALL"):   # slow on purpose there
            out.append(CoachEvent("FAM_SLOW", "correction", {"ias": round(tel.ias)}, cooldown_s=15))
        elif tel.ias > 140:
            # already at idle, or diving: pulling the throttle won't fix it
            diving = tel.throttle <= 0.2 or tel.vs < -300
            out.append(CoachEvent("FAM_FAST_DIVE" if diving else "FAM_FAST", "correction",
                                  {"ias": round(tel.ias)}, cooldown_s=15))
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
        if self.phase in TARGET_STEPS and self._done_t is None:
            t = self._target(self.phase, tel)
            if t and now - self._last_hint >= self.hint_s and abs(t["rel"]) > self.track_band + 5:
                self._last_hint = now
                out.append(CoachEvent("FAM_TARGET_HINT", "nudge", {"phase": self.phase, **t}, cooldown_s=0))
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
        self._meters = standard_all(self._readout(tel))
        if self._step_done(tel, dt, now):
            pending, self._events = self._events, []
            order = self.detector.ORDER
            self.detector.advance(order[order.index(self.phase) + 1])
            if self.phase == "DEBRIEF":
                self._meters = []
                return pending + [CoachEvent("PHASE_ENTER_DEBRIEF", "info", {"phase": "DEBRIEF"},
                                             cooldown_s=0)]
            return pending + self._enter(tel, now)
        out.extend(self._events)                 # e.g. the stall callout
        self._events = []
        for ev in self._monitors(tel, now):
            if ev.cooldown_s == 0 or self._cooled(ev):
                out.append(ev)
        return out


    def _readout(self, tel: Telemetry) -> list[dict]:
        """The step's to-do rows, always with a Height row: the height to hold
        or reach where the step has one, otherwise the current height for
        reference only (no target, so it isn't read out or marked red)."""
        rows = self._readout_rows(tel)
        if self.vacuum_failed:             # the card mustn't give back what the gauge lost
            rows = [r for r in rows if r.get("label") not in ("Bank", "Wings level")]
        if any(r.get("label") == "Height" for r in rows) or self.phase in NO_HEIGHT_ROW:
            return rows                    # only what the step needs
        p, e, d = self.phase, self._entry, self._data
        alt = tel.alt_msl
        row = {"label": "Height", "now": f"{_r(alt, 10):,} ft", "target": ""}
        if p in ("CLIMB", "DESCEND", "PITCH_UP", "PITCH_DOWN") and d:
            row.update(target=f"{d['alt_lo']:,}\u2013{d['alt_hi']:,} ft",
                       ok=d["alt_lo"] <= alt <= d["alt_hi"])
        elif p == "GLIDE" and e:
            tgt = _r(e["alt"] - self.glide_ft, 10)
            row.update(target=f"{tgt:,} ft", ok=alt <= tgt + 10)
        return rows + [row]

    def _readout_rows(self, tel: Telemetry) -> list[dict]:
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
        # climbs / descents: the height to reach is the Height row (_readout)
        if p == "CLIMB":
            return [{"label": "Nose", "now": f"{pitch:+.0f}\u00b0", "target": "5\u201310\u00b0 up",
                     "ok": 4 <= pitch <= 11},
                    {"label": "Speed", "now": f"{tel.ias:.0f} kt",
                     "target": f"{d['spd_lo']}\u2013{d['spd_hi']} kt",
                     "ok": d["spd_lo"] - 3 <= tel.ias <= d["spd_hi"] + 3}]
        if p == "DESCEND":
            return [{"label": "Nose", "now": f"{pitch:+.0f}\u00b0", "target": "2\u20135\u00b0 down",
                     "ok": -6 <= pitch <= -1}]
        if p in ("PITCH_UP", "PITCH_DOWN"):
            up = p == "PITCH_UP"
            return [{"label": "Nose", "now": f"{pitch:+.0f}\u00b0",
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
                    {"label": "Climb/descent", "now": f"{_r(tel.vs, 50):+} fpm",
                     "target": f"under {self.trim_vs:.0f} fpm", "ok": abs(tel.vs) < self.trim_vs}]
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
                      "target": f"{_r(e.get('alt', alt), 10):,} ft \u00b1{self.height_band:.0f} ft",
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
            rows = [{"label": "Speed", "now": f"{tel.ias:.0f} kt",
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
                return [{"label": "Speed", "now": f"{tel.ias:.0f} kt", "target": f"down to {self.stall_kt:.0f} kt",
                         "pct": min(1.0, max(0.0, (start - tel.ias) / max(1.0, start - self.stall_kt)))},
                        {"label": "Nose", "now": f"{pitch:+.0f}\u00b0", "target": "slowly up"}]
            return [{"label": "Speed", "now": f"{tel.ias:.0f} kt", "target": f"{self.recover_kt:.0f}+ kt",
                     "ok": tel.ias >= self.recover_kt},
                    {"label": "Sink", "now": f"{max(0.0, -tel.vs):.0f} fpm",
                     "target": f"under {self.recover_sink:.0f} fpm", "ok": tel.vs >= -self.recover_sink}]
        if p in TARGET_STEPS:
            t = self._target(p, tel)
            if not t or self._done_t is not None:
                return []
            on = abs(t["rel"]) <= self.track_band
            rows = [{"label": t["tgt_name"],
                     "now": "on the nose" if on else f"turn {t['tgt_dir']} {_r(abs(t['rel']), 5)}\u00b0",
                     "target": f"heading {t['tgt_hdg']:03d}\u00b0", "ok": on},
                    {"label": "Distance", "now": f"~{t['tgt_dist_nm']} nm", "target": ""}]
            if p == "DIVERT_TRACK":
                rows.append(height_row)
            return rows
        if p == "LOST_CLIMB":
            tgt = e.get("tgt", alt)
            return [{"label": "Climb to", "now": f"{_r(alt, 10):,} ft", "target": f"{tgt:,.0f} ft",
                     "ok": alt >= tgt - 50}]
        if p in TIMED_TURNS:
            if self._done_t is not None:
                return []
            if self._stage == 1:
                return [{"label": "Wings level", "now": f"{abs(tel.bank):.0f}\u00b0", "target": "under 5\u00b0",
                         "ok": abs(tel.bank) < 5}]
            t_in = self._upset.get("t_in")
            bank = d.get("std_bank", 15)
            return [{"label": "Bank", "now": f"{abs(tel.bank):.0f}\u00b0", "target": f"about {bank}\u00b0",
                     "ok": abs(abs(tel.bank) - bank) <= 5},
                    {"label": "Time", "now": f"{time.time() - t_in:.0f} s" if t_in else "bank to start",
                     "target": f"{self.timed_s:.0f} s"}]
        if p == "COMPASS_TURN":
            if self._done_t is not None:
                return []
            tgt = e.get("tgt", tel.hdg)
            return [{"label": "Compass", "now": f"{_hdg(tel.hdg):03d}\u00b0", "target": f"{_hdg(tgt):03d}\u00b0",
                     "ok": abs(hdg_diff(tgt, tel.hdg)) <= self.heading_ok}]
        if p in UPSET_STEPS:
            if self._stage == 1:
                return []                  # recovered: MAVIS says how it went
            high = p in UPSET_HIGH
            return [{"label": "Wings level", "now": f"{abs(tel.bank):.0f}°", "target": "under 10°",
                     "ok": abs(tel.bank) < 10},
                    {"label": "Nose", "now": f"{pitch:+.0f}°", "target": "on the horizon",
                     "ok": -5 <= pitch <= 8},
                    {"label": "Speed", "now": f"{tel.ias:.0f} kt",
                     "target": (f"{self.recover_kt:.0f}+ kt" if high else f"under {self.upset_max_kt:.0f} kt"),
                     "ok": tel.ias >= self.recover_kt if high else tel.ias <= self.upset_max_kt}]
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
                    if self.phase in (*ROLL_STEPS, *BANKED_TURNS, "TURN_LEFT", "TURN_RIGHT"):
                        msg = "Now roll the wings level"
                    elif self.phase in LEVEL_OFF_STEPS:
                        msg = "Now level off and hold it there"
                    elif self.phase in TIMED_TURNS:
                        msg = "Now roll the wings level"
                    elif self.phase in UPSET_STEPS:
                        msg = "Done — next one coming up"
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
               "speed": 15, "level_off": 20, "smoothness": 20,
               "sequence": 30, "recovery": 25, "heading": 30}

    def __init__(self, lesson: dict, aircraft: dict):
        ex = lesson.get("exercises") or {}
        self.EXERCISES = tuple(s for s in lesson_steps(lesson) if s != "FIND_AIRFIELD")
        self.bank = float(ex.get("turn_bank_deg", 20))
        self.speeds = {"SLOW_CRUISE": float(ex.get("slow_kt", 80)),
                       "FAST_CRUISE": float(ex.get("fast_kt", 105))}
        self.speed_band = speed_bands(ex)
        self.vy = float(aircraft["v_speeds"]["vy"])
        self.climb_band = climb_band(ex, self.vy)
        # the same lesson keys the engine reads, so score and to-do agree
        self.level_ft = {"CLIMB_TO": float(ex.get("climb_to_ft", 500)),
                         "DESCEND_TO": float(ex.get("descend_to_ft", 500))}
        self.speeds.update({"SLOW_FLIGHT": float(ex.get("slow_flight_kt", 55)),
                            "GLIDE": float(ex.get("glide_kt", 68))})
        self.turn_bank = {**{s: float(ex.get("medium_bank_deg", 30)) for s in MEDIUM_TURNS},
                          **{s: float(ex.get("steep_bank_deg", 45)) for s in STEEP_TURNS}}
        # only the metrics this lesson's exercises can produce go on the card
        ex_set = set(self.EXERCISES)
        # an unusual-attitude recovery is meant to be prompt and firm: no smoothness
        # score there, but the order of the controls and how quickly it was done
        self.metrics = set() if ex_set & set(UPSET_STEPS) else {"smoothness"}
        if ex_set & set(UPSET_STEPS):
            self.metrics |= {"sequence", "recovery"}
        self.recover_target_s = float(ex.get("recover_target_s", 8))
        if ex_set & {"DIVERT_TRACK", "COMPASS_TURN", *TIMED_TURNS}:
            self.metrics.add("heading")
        if ex_set & {"STRAIGHT_LEVEL", *HOLD_HEIGHT_STEPS, *HOLD_ALT_STEPS}:
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
        self._acc = {k: [0.0, 0] for k in self.WEIGHTS if k not in ("exercises", "sequence", "recovery", "heading")}
        self._seen: set[str] = set()
        self.skipped: set[str] = set()
        self.upsets: dict[str, dict] = {}       # the engine's upset_results, handed over before finalize()
        self.results: dict[str, dict] = {}      # the engine's results (heading errors), likewise
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
            ias = tel.get("ias", 0.0)
            if phase in self.speed_band:              # slow down / speed up: inside the band = no error
                lo, hi = self.speed_band[phase]
                self._add("speed", max(0.0, lo - ias, ias - hi))
            else:
                self._add("speed", abs(ias - self.speeds[phase]))
        if phase in ("STRAIGHT_LEVEL", *HOLD_HEIGHT_STEPS, *HOLD_ALT_STEPS):
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
            lo, hi = self.climb_band
            ias = tel.get("ias", 0.0)
            self._add("climb_speed", max(0.0, lo - ias, ias - hi))
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

    def _scores_total(self, f: float) -> tuple[dict, int, bool]:
        """Scores, total and whether it was capped, at tolerance factor f
        (core/standards.py: 1 = the lesson's own scales)."""
        scale = {"altitude": self.s["altitude"], "bank": self.s["bank"],
                 "climb_speed": self.s["speed"], "speed": self.s["speed"],
                 "level_off": self.s["altitude"], "smoothness": self.s["smoothness"]}
        scores = {}
        attempted = self._seen | (self.skipped & set(self.EXERCISES))
        scores["exercises"] = (round(100 * len(attempted - self.skipped) / len(attempted))
                               if attempted else None)
        for k, (tot, n) in self._acc.items():
            if k in self.metrics:
                scores[k] = round(_curve(tot / n, scale[k] * f)) if n else None
        if "heading" in self.metrics:
            errs = [r["heading_err"] for r in self.results.values()]
            scores["heading"] = (round(sum(_curve(e, 15.0 * f) for e in errs) / len(errs))
                                 if errs else None)
        if "sequence" in self.metrics:
            ups = list(self.upsets.values())
            scores["sequence"] = (round(100 * sum(u["sequence_ok"] for u in ups) / len(ups))
                                  if ups else None)
            # seconds past the target to recover, plus 10 s each for a stall or
            # going past Vne on the way
            errs = [max(0.0, u["recover_s"] - self.recover_target_s)
                    + 10.0 * u["stalled"] + 10.0 * u["overspeed"] for u in ups]
            scores["recovery"] = (round(sum(_curve(e, 6.0 * f) for e in errs) / len(errs))
                                  if errs else None)
        flown = {k: v for k, v in scores.items() if v is not None}
        wsum = sum(self.WEIGHTS[k] for k in flown) or 1
        total = round(sum(v * self.WEIGHTS[k] for k, v in flown.items()) / wsum) if flown else 0
        # an exercise left undone can't come out top-grade: each one caps the
        # total 10 lower, starting at 85 (a B)
        n_skipped = len(self.skipped & set(self.EXERCISES))
        capped = n_skipped > 0 and total > 95 - 10 * n_skipped
        if capped:
            total = 95 - 10 * n_skipped
        return scores, total, capped

    def _grade(self, total: int) -> str:
        for thr, g in self.grade_bands:
            if total >= thr:
                return g
        return "F"

    def finalize(self, name: str, mode: str, lesson_name: str, practice: bool = False,
                 score_caps: bool | None = None) -> ScoreCard:
        scores, total, capped = self._scores_total(standards.factor(mode))
        card = ScoreCard(name or "", mode, lesson_name, time.time(), scores,
                         self.retries, total, self._grade(total), capped, practice,
                         incomplete=not self.reached, level=standards.label(mode))
        _, cr_total, _ = self._scores_total(standards.CHECKRIDE)
        card.checkride = {"total": cr_total, "grade": self._grade(cr_total)}
        return card
