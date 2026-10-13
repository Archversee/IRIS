"""Deterministic lesson scoring (design spec §14).

The NUMBERS here are rule-based and reproducible - computed from telemetry, the
flown track vs the ideal pattern, and monitor signals - so two runs are
comparable and the score can't be gamed. The warm debrief TEXT is written by the
LLM elsewhere (main.py); this module never asks the model for a number.

Five metrics (0-100 each): track, speed, altitude, landing, smoothness.
Retries are a count that HARD-CAPS the grade when score_caps is on
(trainee / pilot modes, spec §14.5).
Weights / tolerances / caps / grade bands come from the lesson YAML `scoring:`
block, with the defaults below.
"""
from __future__ import annotations
import math
import time
from dataclasses import dataclass

M_PER_NM = 1852.0

DEFAULTS = {
    "weights":    {"track": 25, "speed": 15, "altitude": 20, "landing": 30, "smoothness": 10},
    # `scale` = the error that scores ~37/100 (100*e^-1); bigger = more forgiving
    "scales":     {"track": 75.0, "speed": 9.0, "altitude": 120.0, "smoothness": 0.22,
                   "land_sink": 300.0, "land_center": 40.0, "land_speed": 10.0},
    "grade_bands": [[88, "A"], [76, "B"], [64, "C"], [50, "D"]],   # else "F"
    "retry_caps": {"1": 85, "2": 78, "3": 70},                     # 0 = uncapped; 4+ = floor
    "retry_cap_floor": 60,
    "final_length_m": 500.0,      # base-to-final leg distance (matches the map)
}


def _seg_dist(px, py, ax, ay, bx, by) -> float:
    """Distance from point P to segment AB."""
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _curve(err: float, scale: float) -> float:
    """0-100: 100 at zero error, smooth Gaussian decay by `scale`."""
    if scale <= 0:
        return 0.0
    return 100.0 * math.exp(-(err / scale) ** 2)


@dataclass
class ScoreCard:
    name: str
    mode: str
    lesson: str
    ts: float
    scores: dict          # {track, speed, altitude, landing, smoothness}
    retries: int
    total: int
    grade: str
    capped: bool
    practice: bool = False
    debrief: str = ""
    given: str = ""
    family: str = ""
    incomplete: bool | None = None   # None = circuit rule (no landing scored)

    def to_entry(self) -> dict:
        e = {"name": self.name, "mode": self.mode, "lesson": self.lesson,
             "ts": self.ts, "scores": self.scores, "retries": self.retries,
             "total": self.total, "grade": self.grade, "capped": self.capped}
        if self.given:
            e["given"] = self.given
        if self.family:
            e["family"] = self.family
        return e


class Scorer:
    def __init__(self, lesson: dict, aircraft: dict):
        p, rw = lesson["pattern"], lesson["runway"]
        cfg = lesson.get("scoring", {}) or {}
        self.w = {**DEFAULTS["weights"], **cfg.get("weights", {})}
        self.s = {**DEFAULTS["scales"], **cfg.get("scales", {})}
        self.grade_bands = cfg.get("grade_bands", DEFAULTS["grade_bands"])
        self.retry_caps = {int(k): v for k, v in
                           {**DEFAULTS["retry_caps"], **cfg.get("retry_caps", {})}.items()}
        self.retry_cap_floor = cfg.get("retry_cap_floor", DEFAULTS["retry_cap_floor"])
        # ideal circuit polygon in (along, cross) metres; cross is pattern-side-positive
        upA = rw["length_m"] + 420.0
        off = p.get("downwind_offset_nm", 0.8) * M_PER_NM
        fin = float(p.get("base_turn_past_abeam_m")
                    or cfg.get("final_length_m", DEFAULTS["final_length_m"]))
        self.poly = [(0.0, 0.0), (upA, 0.0), (upA, off), (-fin, off), (-fin, 0.0), (0.0, 0.0)]
        # Take-off / Landing lessons fly one piece of the circuit: the ideal
        # track is the runway centreline (extended out along the final approach)
        self.segment = lesson.get("segment")
        if self.segment == "takeoff":
            self.poly = [(0.0, 0.0), (upA, 0.0)]
        elif self.segment == "landing":
            final_m = float((lesson.get("airborne_start") or {}).get("final_m", 3000.0))
            self.poly = [(-final_m, 0.0), (rw["length_m"], 0.0)]
        self.pattern_alt = p["altitude_msl_ft"]
        v = aircraft["v_speeds"]
        self.tgt_speed = {"CLIMB_OUT": v["vy"], "CROSSWIND": v["vy"],
                          "DOWNWIND": v["pattern_downwind"], "BASE": v["base"], "FINAL": v["final"]}
        b = lesson.get("climb_speed_band_kt")      # climb legs flown to a band
        self.speed_band = ({"CLIMB_OUT": (float(b[0]), float(b[1])),
                            "CROSSWIND": (float(b[0]), float(b[1]))} if b else {})
        self.reset()

    def reset(self) -> None:
        self._acc = {k: [0.0, 0] for k in ("track", "speed", "altitude", "smoothness")}
        self._prev_ctrl = None
        self._landings: list[dict] = []
        self.retries = 0
        self.reached = False         # got to DEBRIEF: how a take-off lesson is "complete"

    def note_retry(self) -> None:
        self.retries += 1

    # ---- per-tick sampling (tel/ctrl are plain dicts; geom is {along,cross} or None) ----
    def sample(self, tel: dict, geom: dict | None, ctrl: dict | None, phase: str) -> None:
        if phase == "DEBRIEF":
            self.reached = True
        if tel.get("on_ground", True):
            return                                   # score airborne flying only
        if geom is not None:
            self._add("track", self._track_err(geom))
            aerr = self._alt_err(tel, geom, phase)
            if aerr is not None:
                self._add("altitude", aerr)
        spd = self.tgt_speed.get(phase)
        band = self.speed_band.get(phase)
        if band:
            ias = tel.get("ias", 0.0)
            self._add("speed", max(0.0, band[0] - ias, ias - band[1]))
        elif spd:
            self._add("speed", abs(tel.get("ias", 0.0) - spd))
        roll = ctrl["roll"] if ctrl and ctrl.get("available") else tel.get("aileron", 0.0)
        pitch = ctrl["pitch"] if ctrl and ctrl.get("available") else -tel.get("elevator", 0.0)
        if self._prev_ctrl is not None:
            self._add("smoothness", abs(roll - self._prev_ctrl[0]) + abs(pitch - self._prev_ctrl[1]))
        self._prev_ctrl = (roll, pitch)

    def on_landing(self, tel: dict, geom: dict | None) -> None:
        """Capture a touchdown (on_ground transition). The last one is graded
        (L1 has a single full-stop landing)."""
        self._landings.append({
            "sink": abs(tel.get("vs", 0.0)),
            "center": abs(geom["cross"]) if geom else 0.0,
            "speed_err": abs(tel.get("ias", 0.0) - self.tgt_speed.get("FINAL", 65)),
        })

    def _add(self, key: str, err: float) -> None:
        a = self._acc[key]
        a[0] += err
        a[1] += 1

    def _track_err(self, geom: dict) -> float:
        px, py = geom["along"], geom["cross"]
        return min(_seg_dist(px, py, *self.poly[i], *self.poly[i + 1])
                   for i in range(len(self.poly) - 1))

    def _alt_err(self, tel: dict, geom: dict, phase: str):
        if phase == "DOWNWIND":
            return abs(tel.get("alt_msl", 0.0) - self.pattern_alt)
        if phase == "FINAL":
            dist_m = max(0.0, -geom["along"])           # metres before threshold
            ideal_agl = dist_m * 0.1719                  # ~3deg glidepath, in feet
            return abs(tel.get("alt_agl", 0.0) - ideal_agl)
        return None

    def _landing_score(self) -> float | None:
        if not self._landings:
            return None
        L = self._landings[-1]
        return (0.5 * _curve(L["sink"], self.s["land_sink"])
                + 0.3 * _curve(L["center"], self.s["land_center"])
                + 0.2 * _curve(L["speed_err"], self.s["land_speed"]))

    def _grade(self, total: float, scores: dict | None = None, incomplete: bool = False) -> str:
        # Hidden top tier, never listed in the public grade-band table.
        if not incomplete and scores and self.retries == 0 and total >= 96:
            vals = [v for v in scores.values() if v is not None]
            if vals and min(vals) >= 90:
                return "S"
        for thr, g in self.grade_bands:
            if total >= thr:
                return g
        return "F"

    def finalize(self, name: str, mode: str, lesson_name: str, practice: bool = False,
                 score_caps: bool | None = None) -> ScoreCard:
        def mean(key):
            a = self._acc[key]
            return a[0] / a[1] if a[1] else 0.0

        landing_raw = self._landing_score()
        scores = {
            "track": round(_curve(mean("track"), self.s["track"])),
            "speed": round(_curve(mean("speed"), self.s["speed"])),
            "altitude": round(_curve(mean("altitude"), self.s["altitude"])),
            "landing": round(landing_raw) if landing_raw is not None else None,
            "smoothness": round(_curve(mean("smoothness"), self.s["smoothness"])),
        }
        # a metric this lesson doesn't weigh (weight 0) isn't on its card --
        # the take-off lesson has no landing, no circuit height
        scores = {k: v for k, v in scores.items() if self.w.get(k, 0) > 0}
        incomplete = (not self.reached) if self.segment == "takeoff" else landing_raw is None
        wsum = sum(self.w.values()) or 1
        total = sum((scores.get(k) or 0) * self.w[k] for k in self.w) / wsum
        capped = False
        # Trainee / pilot (and legacy serious) apply retry caps; visitor / exam do not.
        if score_caps is None:
            score_caps = mode in ("trainee", "pilot", "serious")
        if score_caps and self.retries > 0:
            cap = self.retry_caps.get(self.retries, self.retry_cap_floor)
            if total > cap:
                total, capped = cap, True
        total = round(total)
        return ScoreCard(name or "", mode, lesson_name, time.time(), scores,
                         self.retries, total, self._grade(total, scores, incomplete), capped, practice,
                         incomplete=incomplete)
