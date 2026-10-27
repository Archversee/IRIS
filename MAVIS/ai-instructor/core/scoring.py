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

from .monitors import final_speed_band
from .variations import make_variation
from . import standards
from .reference_profiles import latlon_to_pattern

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


# Planned circuit corner radius (m) when the lesson doesn't set one: a C172 at
# circuit speeds (~80 kt) in a normal ~23-degree bank. The map (MapCanvas.jsx),
# IRIS's Review route (backend routes.py) and the track score all use it.
CORNER_RADIUS_M = 400.0


def rounded_path(corners: list[tuple[float, float]], radius: float, n: int = 12) -> list[tuple[float, float]]:
    """The corners of a polyline replaced by smooth turns: each interior
    corner becomes a curve starting `radius` metres before it and ending
    `radius` metres after it along the two legs (the same curve the map
    draws). The first and last points are kept as they are."""
    out = [corners[0]]
    for i in range(1, len(corners) - 1):
        p0, p1, p2 = corners[i - 1], corners[i], corners[i + 1]
        v1 = (p0[0] - p1[0], p0[1] - p1[1])
        v2 = (p2[0] - p1[0], p2[1] - p1[1])
        l1, l2 = math.hypot(*v1) or 1.0, math.hypot(*v2) or 1.0
        r = min(radius, l1 / 2, l2 / 2)
        t1 = (p1[0] + v1[0] / l1 * r, p1[1] + v1[1] / l1 * r)
        t2 = (p1[0] + v2[0] / l2 * r, p1[1] + v2[1] / l2 * r)
        for k in range(n + 1):
            u = k / n
            out.append(((1 - u) ** 2 * t1[0] + 2 * (1 - u) * u * p1[0] + u ** 2 * t2[0],
                        (1 - u) ** 2 * t1[1] + 2 * (1 - u) * u * p1[1] + u ** 2 * t2[1]))
    out.append(corners[-1])
    return out


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
    level: str = ""                  # the standard it was judged at (Visitor / Trainee / Check-ride)
    checkride: dict | None = None    # the same flight at the check-ride standard: {total, grade}
    # The same run scored against the expert reference flown (None when the
    # planned circuit was the reference) -- the leaderboard's second column.
    expert_total: int | None = None
    expert_reference: str = ""
    aircraft: str = ""
    session_id: str = ""
    user_id: str = ""                # IRIS account that flew it (may delete it from the board)

    def to_entry(self) -> dict:
        e = {"name": self.name, "mode": self.mode, "lesson": self.lesson,
             "ts": self.ts, "scores": self.scores, "retries": self.retries,
             "total": self.total, "grade": self.grade, "capped": self.capped}
        for k in ("given", "family", "expert_reference", "aircraft", "session_id", "user_id"):
            if getattr(self, k):
                e[k] = getattr(self, k)
        if self.expert_total is not None:
            e["expert_total"] = self.expert_total
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
        # the planned circuit, with turns a real aircraft can fly (not square corners)
        self.poly = rounded_path([(0.0, 0.0), (upA, 0.0), (upA, off), (-fin, off), (-fin, 0.0), (0.0, 0.0)],
                                 float(p.get("corner_radius_m", CORNER_RADIUS_M)))
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
        self._lesson, self._v = lesson, v
        self.tgt_speed = {"CLIMB_OUT": v["vy"], "CROSSWIND": v["vy"],
                          "DOWNWIND": v["pattern_downwind"], "BASE": v["base"],
                          "FINAL": round(float(lesson.get("final_speed_kt") or v["final"]))}
        # Circuit Variations: their own measurements (reaction, touchdown point, ...)
        self.variation = make_variation(lesson, aircraft)
        b = lesson.get("climb_speed_band_kt")      # climb legs flown to a band
        self.speed_band = ({"CLIMB_OUT": (float(b[0]), float(b[1])),
                            "CROSSWIND": (float(b[0]), float(b[1]))} if b else {})
        self._ref = None             # expert reference targets (set_reference), scored alongside
        self.reset()

    def set_reference(self, prof) -> None:
        """Also score the run against an expert reference profile: its flown
        track, its per-phase speeds and its downwind height replace the
        planned circuit's for track / speed / altitude; landing and smoothness
        are the same either way. The planned circuit (or None) turns it off.
        Switching mid-run starts the expert score over from that point."""
        if prof is None or prof.id == "planned" or not prof.track:
            self._ref = None
        else:
            rw, p = self._lesson["runway"], self._lesson["pattern"]
            self._ref = {
                "name": prof.display_name,
                "poly": [latlon_to_pattern(rw, p, pt[0], pt[1]) for pt in prof.track],
                # only phases the expert has a speed for; the rest score like the planned circuit
                "speed": {ph: float(t["ias"]) for ph, t in (prof.targets or {}).items() if "ias" in t},
                "alt": prof.circuit_alt(self.pattern_alt),
            }
        self._xacc = {k: [0.0, 0] for k in ("track", "speed", "altitude")}

    def reset(self) -> None:
        self._acc = {k: [0.0, 0] for k in ("track", "speed", "altitude", "smoothness")}
        self._xacc = {k: [0.0, 0] for k in ("track", "speed", "altitude")}
        self._prev_ctrl = None
        self._landings: list[dict] = []
        self.retries = 0
        self.reached = False         # got to DEBRIEF: how a take-off lesson is "complete"
        if getattr(self, "variation", None):
            self.variation.reset()

    def note_retry(self) -> None:
        self.retries += 1

    # ---- per-tick sampling (tel/ctrl are plain dicts; geom is {along,cross} or None) ----
    def sample(self, tel: dict, geom: dict | None, ctrl: dict | None, phase: str) -> None:
        if phase == "DEBRIEF":
            self.reached = True
        if self.variation and geom is not None:      # measured on the ground too
            self.variation.observe(tel, geom["along"], geom["cross"], phase)
        if tel.get("on_ground", True):
            return                                   # score airborne flying only
        ref = self._ref
        if geom is not None:
            self._add("track", self._track_err(geom))
            aerr = self._alt_err(tel, geom, phase)
            if aerr is not None:
                self._add("altitude", aerr)
            if ref:
                self._add("track", self._track_err(geom, ref["poly"]), self._xacc)
                aerr = self._alt_err(tel, geom, phase, ref["alt"])
                if aerr is not None:
                    self._add("altitude", aerr, self._xacc)
        spd = self.tgt_speed.get(phase)
        band = self.speed_band.get(phase)
        if phase == "FINAL" and band is None:
            fb = final_speed_band(self._lesson, self._v, tel.get("alt_agl", 0.0))
            band = fb[:2] if fb else None
        serr = None
        ias = tel.get("ias", 0.0)
        if band:
            serr = max(0.0, band[0] - ias, ias - band[1])
        elif spd:
            serr = abs(ias - spd)
        if serr is not None:
            self._add("speed", serr)
        if ref:
            xspd = ref["speed"].get(phase)
            if xspd is not None:
                self._add("speed", abs(ias - xspd), self._xacc)
            elif serr is not None:
                self._add("speed", serr, self._xacc)
        roll = ctrl["roll"] if ctrl and ctrl.get("available") else tel.get("aileron", 0.0)
        pitch = ctrl["pitch"] if ctrl and ctrl.get("available") else -tel.get("elevator", 0.0)
        if self._prev_ctrl is not None:
            self._add("smoothness", abs(roll - self._prev_ctrl[0]) + abs(pitch - self._prev_ctrl[1]))
        self._prev_ctrl = (roll, pitch)

    def on_landing(self, tel: dict, geom: dict | None) -> None:
        """Capture a touchdown (on_ground transition). The last one is graded
        (L1 has a single full-stop landing)."""
        ias = tel.get("ias", 0.0)
        fb = final_speed_band(self._lesson, self._v, 0.0)        # the touchdown end of the band
        err = (max(0.0, fb[0] - ias, ias - fb[1]) if fb
               else abs(ias - self.tgt_speed.get("FINAL", 65)))
        self._landings.append({
            "sink": abs(tel.get("vs", 0.0)),
            "center": abs(geom["cross"]) if geom else 0.0,
            "speed_err": err,
        })

    def _add(self, key: str, err: float, acc: dict | None = None) -> None:
        a = (self._acc if acc is None else acc)[key]
        a[0] += err
        a[1] += 1

    def _track_err(self, geom: dict, poly: list | None = None) -> float:
        poly = poly or self.poly
        px, py = geom["along"], geom["cross"]
        return min(_seg_dist(px, py, *poly[i], *poly[i + 1])
                   for i in range(len(poly) - 1))

    def _alt_err(self, tel: dict, geom: dict, phase: str, pattern_alt: float | None = None):
        if phase == "DOWNWIND":
            return abs(tel.get("alt_msl", 0.0) - (self.pattern_alt if pattern_alt is None else pattern_alt))
        if phase == "FINAL":
            dist_m = max(0.0, -geom["along"])           # metres before threshold
            ideal_agl = dist_m * 0.1719                  # ~3deg glidepath, in feet
            return abs(tel.get("alt_agl", 0.0) - ideal_agl)
        return None

    def _landing_score(self, f: float = 1.0) -> float | None:
        if not self._landings:
            return None
        L = self._landings[-1]
        return (0.5 * _curve(L["sink"], self.s["land_sink"] * f)
                + 0.3 * _curve(L["center"], self.s["land_center"] * f)
                + 0.2 * _curve(L["speed_err"], self.s["land_speed"] * f))

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

    def _scores(self, f: float, acc: dict | None = None) -> dict:
        """Every metric at tolerance factor f (core/standards.py: 1 = the
        lesson's own scales, bigger = more forgiving). acc = the expert
        reference's errors (self._xacc) for track / speed / altitude instead
        of the planned circuit's; landing, smoothness and a variation's own
        measurements are the same either way."""
        def mean(key):
            a = (acc if acc is not None and key in acc else self._acc)[key]
            return a[0] / a[1] if a[1] else 0.0

        landing_raw = self._landing_score(f)
        scores = {
            "track": round(_curve(mean("track"), self.s["track"] * f)),
            "speed": round(_curve(mean("speed"), self.s["speed"] * f)),
            "altitude": round(_curve(mean("altitude"), self.s["altitude"] * f)),
            "landing": round(landing_raw) if landing_raw is not None else None,
            "smoothness": round(_curve(mean("smoothness"), self.s["smoothness"] * f)),
            **(self.variation.scores(f) if self.variation else {}),
        }
        # a metric this lesson doesn't weigh (weight 0) isn't on its card --
        # the take-off lesson has no landing, no circuit height
        return {k: v for k, v in scores.items() if self.w.get(k, 0) > 0}

    def _total(self, scores: dict, score_caps: bool) -> tuple[int, bool]:
        wsum = sum(w for k, w in self.w.items() if k in scores) or 1
        total = sum((scores.get(k) or 0) * self.w[k] for k in self.w) / wsum
        capped = False
        if score_caps and self.retries > 0:
            cap = self.retry_caps.get(self.retries, self.retry_cap_floor)
            if total > cap:
                total, capped = cap, True
        return round(total), capped

    def finalize(self, name: str, mode: str, lesson_name: str, practice: bool = False,
                 score_caps: bool | None = None) -> ScoreCard:
        f = standards.factor(mode)
        scores = self._scores(f)
        incomplete = (not self.reached) if self.segment == "takeoff" else self._landing_score() is None
        v_inc = self.variation.incomplete() if self.variation else None
        if v_inc is not None:
            incomplete = v_inc
        # Trainee / pilot (and legacy serious) apply retry caps; visitor / exam do not.
        if score_caps is None:
            score_caps = mode in ("trainee", "pilot", "serious")
        total, capped = self._total(scores, score_caps)
        card = ScoreCard(name or "", mode, lesson_name, time.time(), scores,
                         self.retries, total, self._grade(total, scores, incomplete), capped, practice,
                         incomplete=incomplete, level=standards.label(mode))
        # the same flight at the check-ride standard (the line under the grade)
        cr_scores = self._scores(standards.CHECKRIDE)
        cr_total, _ = self._total(cr_scores, score_caps)
        card.checkride = {"total": cr_total, "grade": self._grade(cr_total, cr_scores, incomplete)}
        # the same flight against the expert reference flown, at the same standard
        if self._ref:
            card.expert_total = self._total(self._scores(f, self._xacc), score_caps)[0]
            card.expert_reference = self._ref["name"]
        return card
