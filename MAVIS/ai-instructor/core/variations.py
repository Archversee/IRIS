"""Circuit variations (catalog module "Circuit Variations"): Go-Around,
Short-Field Landing and Soft-Field Technique. Each is a full circuit from
runway 02 on the circuit engine (lesson_engine.py / phase_detector.py), with
one extra piece picked by the lesson's `variation:` key:

  go_around    "Go around!" is called on final at a random height; the
               trainee goes around and climbs out to go_around.end_agl_ft.
  short_field  a tight approach speed onto an aim point, then stopping
               before a marked line.
  soft_field   nose wheel off early, lift off slow and accelerate in ground
               effect; touch down slow and nose high, nose wheel held off.

A Variation both MEASURES (observe(), every tick -- the scorer keeps its own
instance for the numbers on the scorecard) and COACHES (cues(), the engine's
instance -- spoken calls, codes starting VAR_ so they're never reworded).
All measurement uses the telemetry's own clock (tel["t"]).
"""
from __future__ import annotations

import math
import random

from .events import CoachEvent


def _curve(err: float, scale: float) -> float:
    """0-100: 100 at zero error, Gaussian decay by `scale` (as core/scoring.py)."""
    return 100.0 * math.exp(-(max(0.0, err) / scale) ** 2) if scale > 0 else 0.0


def _ev(code: str, data: dict | None = None, severity: str = "correction",
        cooldown_s: float = 0.0, urgent: bool = False) -> CoachEvent:
    d = dict(data or {})
    if urgent:
        d["urgent"] = True          # coach rank: cuts off a less important line
    return CoachEvent(code, severity, d, cooldown_s=cooldown_s)


class Variation:
    """Base: nothing measured, nothing said."""

    def __init__(self, lesson: dict, aircraft: dict):
        self.lesson = lesson
        self.v = aircraft["v_speeds"]
        self.cfg = lesson.get(lesson.get("variation", ""), {}) or {}
        self.reset()

    def reset(self) -> None:
        self._said: set[str] = set()
        self._prev_ground: bool | None = None

    def _once(self, code: str, *args, **kw) -> list[CoachEvent]:
        if code in self._said:
            return []
        self._said.add(code)
        return [_ev(code, *args, **kw)]

    def observe(self, tel: dict, along: float, cross: float, phase: str) -> None:
        pass

    def cues(self, tel: dict, along: float, cross: float, phase: str) -> list[CoachEvent]:
        return []

    def scores(self) -> dict:
        return {}

    def incomplete(self) -> bool | None:
        """True/False to decide the scorecard's "not finished"; None = default rule."""
        return None


# ---------------------------------------------------------------- go-around
class GoAround(Variation):
    """Measured from the moment the lesson enters GO_AROUND (the call, or the
    trainee going around on their own first): reaction (call -> full power),
    height lost, the flap sequence (30 -> 20 at once, 10 with a positive
    climb, up above 300 ft and 65 kt) and the climb speed."""

    FULL_POWER = 0.9

    def reset(self) -> None:
        super().reset()
        lo, hi = (self.cfg.get("call_agl_ft") or [150, 300])
        self.call_agl = random.uniform(float(lo), float(hi))
        self.t0 = self.h0 = self.f0 = None
        self.t_power = None
        self.min_agl = None
        self.climbing_since = None
        self.positive = False         # positive rate of climb established
        self.t_f20 = None
        self.penalties: list[str] = []
        self.flaps_prev = None
        self.speed_err = [0.0, 0]
        self.ended_flaps = None

    def observe(self, tel, along, cross, phase):
        if phase != "GO_AROUND":
            if self.t0 is not None:
                self.ended_flaps = tel.get("flaps_deg", 0.0)
            return
        t, agl, flaps = tel["t"], tel.get("alt_agl", 0.0), tel.get("flaps_deg", 0.0)
        ias, vs = tel.get("ias", 0.0), tel.get("vs", 0.0)
        if self.t0 is None:
            self.t0, self.h0, self.f0, self.min_agl = t, agl, flaps, agl
            self.flaps_prev = flaps
        dt = t - self.t0
        if self.t_power is None and tel.get("throttle", 0.0) >= self.FULL_POWER:
            self.t_power = t
        if not self.positive:
            self.min_agl = min(self.min_agl, agl)
            self.climbing_since = (self.climbing_since or t) if vs > 200 else None
            self.positive = self.climbing_since is not None and t - self.climbing_since >= 1.5
        # flap sequence, judged as each stage goes in
        prev, self.flaps_prev = self.flaps_prev, flaps
        if self.t_f20 is None and flaps <= 25 and (self.f0 or 0) > 25:
            self.t_f20 = t
        if prev > 15 >= flaps and (not self.positive or ias < 55):
            self.penalties.append("flaps to 10 before a positive climb")
        if prev > 5 >= flaps and (agl < 300 or ias < 62):
            self.penalties.append("flaps up too low or too slow")
        if (self.f0 or 0) > 25 and self.t_f20 is None and dt > 8 and "slow_f20" not in self.penalties:
            self.penalties.append("slow_f20")
        # climb speed once climbing away: 60-85 kt
        if self.positive:
            self.speed_err[0] += max(0.0, 60.0 - ias, ias - 85.0)
            self.speed_err[1] += 1

    def cues(self, tel, along, cross, phase):
        if phase != "GO_AROUND" or self.t0 is None:
            return []
        t, flaps = tel["t"], tel.get("flaps_deg", 0.0)
        ias, vs, agl = tel.get("ias", 0.0), tel.get("vs", 0.0), tel.get("alt_agl", 0.0)
        dt = t - self.t0
        full = tel.get("throttle", 0.0) >= self.FULL_POWER
        out: list[CoachEvent] = []
        if not full and dt >= 2.5:
            out.append(_ev("VAR_GA_POWER", cooldown_s=4, urgent=True))
        if ias < 55:
            out.append(_ev("VAR_GA_SLOW", {"ias": round(ias)}, cooldown_s=5, urgent=True))
        elif full and dt >= 4 and vs < -100 and not self.positive:
            out.append(_ev("VAR_GA_PITCH", cooldown_s=5, urgent=True))
        if full and flaps > 25 and self.t_power is not None and t - self.t_power >= 1.5:
            out += self._once("VAR_GA_FLAPS_20", urgent=True)
        if self.positive and ias >= 60 and 15 < flaps <= 25:
            out += self._once("VAR_GA_FLAPS_10", urgent=True)
        if agl >= 300 and ias >= 65 and 5 < flaps <= 15:
            out += self._once("VAR_GA_FLAPS_UP", {"vy": self.v["vy"]}, urgent=True)
        return out

    def scores(self):
        if self.t0 is None:
            return {"reaction": None, "height_loss": None, "flaps": None, "climb_speed": None}
        react = (self.t_power - self.t0) if self.t_power is not None else 10.0
        loss = max(0.0, (self.h0 or 0.0) - (self.min_agl if self.min_agl is not None else 0.0))
        pen = 100.0
        for p in self.penalties:
            pen -= 30.0
        if self.ended_flaps is not None and self.ended_flaps > 5:
            pen -= 20.0                       # still not cleaned up at the end
        n = self.speed_err[1]
        return {"reaction": round(_curve(react - 1.0, 2.0)),
                "height_loss": round(_curve(loss - 20.0, 60.0)),
                "flaps": max(0, round(pen)),
                "climb_speed": round(_curve(self.speed_err[0] / n, 8.0)) if n else None}

    def incomplete(self):
        return self.t0 is None                # never went around


# ---------------------------------------------------------------- short field
class ShortField(Variation):
    """Touchdown distance from the aim point, and stopping before the line."""

    def reset(self) -> None:
        super().reset()
        self.aim = float(self.cfg.get("aim_point_m", 150))
        self.stop_by = float(self.cfg.get("stop_by_m", 750))
        self.td_along = None
        self.stop_along = None
        self.airborne_seen = False

    def observe(self, tel, along, cross, phase):
        ground = bool(tel.get("on_ground", True))
        if not ground:
            self.airborne_seen = True
        landing = phase in ("FINAL", "FLARE_TOUCHDOWN", "ROLLOUT")
        if (self.td_along is None and landing and self.airborne_seen
                and ground and self._prev_ground is False):
            self.td_along = along
        if self.td_along is not None and self.stop_along is None and ground and tel.get("ias", 0.0) < 3:
            self.stop_along = along
        self._prev_ground = ground

    def cues(self, tel, along, cross, phase):
        out: list[CoachEvent] = []
        agl, ias = tel.get("alt_agl", 0.0), tel.get("ias", 0.0)
        if phase == "FINAL" and agl > 100 and along < self.aim - 150:
            ideal = (self.aim - along) * 0.1719          # 3-degree path to the aim point, ft
            diff = agl - ideal
            if diff > 60:
                out.append(_ev("VAR_SF_HIGH", {"off": round(diff, -1)}, cooldown_s=10))
            elif diff < -50:
                out.append(_ev("VAR_SF_LOW", {"off": round(-diff, -1)}, cooldown_s=10))
        if phase in ("FINAL", "FLARE_TOUCHDOWN") and -60 <= along and agl < 60:
            out += self._once("VAR_SF_OVER_FENCE", urgent=True)
        if self.td_along is not None and "VAR_SF_TOUCHDOWN" not in self._said:
            d = self.td_along - self.aim
            out += self._once("VAR_SF_TOUCHDOWN",
                              {"dist": abs(round(d / 10) * 10), "where": "past" if d >= 0 else "short of"},
                              urgent=True)
        if (self.td_along is not None and self.stop_along is None and ias > 20
                and along > self.stop_by - 200):
            out.append(_ev("VAR_SF_BRAKE", cooldown_s=4, urgent=True))
        if self.stop_along is not None:
            m = self.stop_by - self.stop_along
            out += self._once("VAR_SF_STOPPED_OK" if m >= 0 else "VAR_SF_STOPPED_OVER",
                              {"margin": abs(round(m / 10) * 10)}, severity="info")
        return out

    def scores(self):
        if self.td_along is None:
            return {"touchdown": None, "stop": None}
        # up to 30 m past the aim point is fine; short of it counts double
        # (as a check ride: land on or just beyond the point, never before it);
        # short of the runway is never fine
        d = self.td_along - self.aim
        err = max(0.0, d - 30.0) if d >= 0 else -2.0 * d
        td = 0.0 if self.td_along < 0 else _curve(err, 60.0)
        stop = None
        if self.stop_along is not None:
            stop = 100.0 if self.stop_along <= self.stop_by else _curve(self.stop_along - self.stop_by, 80.0)
        return {"touchdown": round(td), "stop": round(stop) if stop is not None else None}


# ---------------------------------------------------------------- soft field
class SoftField(Variation):
    """Take-off: nose wheel off early, lift off slow, stay in ground effect
    until the speed builds. Landing: touch down slow, nose high, a little
    power, nose wheel held off, light brakes."""

    def reset(self) -> None:
        super().reset()
        c = self.cfg
        self.nose_up = float(c.get("nose_up_pitch_deg", 4))
        self.liftoff_max = float(c.get("liftoff_max_kt", 52))
        self.ge_agl = float(c.get("ground_effect_agl_ft", 20))
        self.accel_to = float(c.get("accelerate_to_kt", self.v["vx"]))
        self.td_max = float(c.get("touchdown_max_kt", 50))
        self.hold_s = float(c.get("hold_nose_s", 4))
        self.stage = "ground"           # ground -> roll -> ground_effect -> climb -> ... -> landed
        self.nose_ias = None
        self.liftoff_ias = None
        self.ge_max_agl = 0.0
        self.accelerated = False        # reached accel_to while still low (as taught)
        self.td = None                  # {ias, pitch, sink, throttle, t}
        self.nose_down_t = None
        self.max_brake = 0.0

    def observe(self, tel, along, cross, phase):
        ground = bool(tel.get("on_ground", True))
        ias, pitch, agl = tel.get("ias", 0.0), tel.get("pitch", 0.0), tel.get("alt_agl", 0.0)
        if self.stage == "ground" and phase == "TAKEOFF_ROLL":
            self.stage = "roll"
        if self.stage == "roll":
            if ground and self.nose_ias is None and pitch >= self.nose_up and ias > 5:
                self.nose_ias = ias
            if not ground and self._prev_ground:
                self.liftoff_ias = ias
                self.stage = "ground_effect"
        elif self.stage == "ground_effect":
            self.ge_max_agl = max(self.ge_max_agl, agl)
            if ias >= self.accel_to:
                self.accelerated, self.stage = True, "climb"
            elif agl > 150:
                self.stage = "climb"         # climbed away early: no "now climb" call
        elif self.stage == "climb" and phase in ("FINAL", "FLARE_TOUCHDOWN", "ROLLOUT"):
            if ground and self._prev_ground is False:
                self.td = {"ias": ias, "pitch": pitch, "sink": max(0.0, -tel.get("vs", 0.0)),
                           "throttle": tel.get("throttle", 0.0), "t": tel["t"]}
                self.stage = "landed"
        if self.stage == "landed":
            if self.nose_down_t is None and pitch < 1.5:
                self.nose_down_t = tel["t"]
            self.max_brake = max(self.max_brake, tel.get("brake_left", 0.0), tel.get("brake_right", 0.0))
        self._prev_ground = ground

    def cues(self, tel, along, cross, phase):
        out: list[CoachEvent] = []
        ground = bool(tel.get("on_ground", True))
        ias, pitch, agl = tel.get("ias", 0.0), tel.get("pitch", 0.0), tel.get("alt_agl", 0.0)
        if phase == "PRE_TAKEOFF" and ground and not 5 <= tel.get("flaps_deg", 0.0) <= 15:
            out += self._once("VAR_SOFT_FLAPS", severity="info")
        if self.stage == "roll" and ground and ias > 20 and pitch < self.nose_up:
            out.append(_ev("VAR_SOFT_NOSE_UP", cooldown_s=6, urgent=True))
        if self.stage == "roll" and self.nose_ias is not None and ias > 30:
            out += self._once("VAR_SOFT_HOLD", severity="info")
        if self.stage == "ground_effect":
            if agl > self.ge_agl + 15 and ias < self.accel_to - 3:
                out.append(_ev("VAR_SOFT_STAY_LOW", cooldown_s=5, urgent=True))
        if self.stage == "climb" and self.accelerated:
            out += self._once("VAR_SOFT_CLIMB", {"kt": round(self.accel_to)}, urgent=True)
        if phase == "FLARE_TOUCHDOWN" and not ground and agl < 25 and tel.get("throttle", 0.0) < 0.05:
            out.append(_ev("VAR_SOFT_POWER", cooldown_s=6, urgent=True))
        if self.stage == "landed" and self.td and tel["t"] - self.td["t"] < self.hold_s:
            if pitch < 2.0 and ias > 15:
                out.append(_ev("VAR_SOFT_NOSE_OFF", cooldown_s=4, urgent=True))
            if max(tel.get("brake_left", 0.0), tel.get("brake_right", 0.0)) > 0.5:
                out.append(_ev("VAR_SOFT_BRAKES", cooldown_s=6))
        return out

    def scores(self):
        out = {"soft_takeoff": None, "soft_landing": None}
        if self.liftoff_ias is not None:
            nose = _curve((self.nose_ias if self.nose_ias is not None else self.liftoff_ias) - 30.0, 15.0)
            lift = _curve(self.liftoff_ias - self.liftoff_max, 8.0)
            ge = _curve(self.ge_max_agl - self.ge_agl, 40.0)
            out["soft_takeoff"] = round(0.3 * nose + 0.35 * lift + 0.35 * ge)
        if self.td is not None:
            held = ((self.nose_down_t if self.nose_down_t is not None else self.td["t"] + self.hold_s)
                    - self.td["t"])
            out["soft_landing"] = round(
                0.3 * _curve(self.td["ias"] - self.td_max, 6.0)
                + 0.2 * _curve(self.nose_up - self.td["pitch"], 3.0)
                + 0.15 * _curve(self.td["sink"] - 150.0, 200.0)
                + 0.2 * _curve(self.hold_s - held, 3.0)
                + 0.15 * _curve(self.max_brake - 0.5, 0.3))
        return out

    def incomplete(self):
        return self.td is None


_KINDS = {"go_around": GoAround, "short_field": ShortField, "soft_field": SoftField}


def make_variation(lesson: dict, aircraft: dict) -> Variation | None:
    kind = _KINDS.get(lesson.get("variation") or "")
    return kind(lesson, aircraft) if kind else None
