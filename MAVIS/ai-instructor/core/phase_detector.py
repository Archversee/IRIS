"""Pattern geometry + phase transitions, parameterized by the lesson file.

Works for any single-runway rectangular circuit (left or right traffic).
"""
from __future__ import annotations
import math

from adapters.sim_base import Telemetry


def hdg_diff(a: float, b: float) -> float:
    """Signed smallest difference a-b in degrees (-180..180)."""
    return (a - b + 180) % 360 - 180


class Geometry:
    """Aircraft position relative to the runway, in meters."""
    def __init__(self, along: float, cross: float, hdg_off: float):
        self.along = along        # + = past threshold in takeoff direction
        self.cross = cross        # + = on the pattern side of centerline
        self.hdg_off = hdg_off    # signed heading offset from runway heading


class PhaseDetector:
    ORDER = ["BRIEFING", "PRE_TAKEOFF", "TAKEOFF_ROLL", "CLIMB_OUT", "CROSSWIND",
             "DOWNWIND", "BASE", "FINAL", "FLARE_TOUCHDOWN", "ROLLOUT", "DEBRIEF"]

    def __init__(self, lesson: dict, total_circuits: int = 1):
        rw = lesson["runway"]
        self.lat0 = rw["threshold"]["lat"]
        self.lon0 = rw["threshold"]["lon"]
        self.hdg_rw = rw["heading_true"]
        self.side = 1.0 if lesson["pattern"]["side"] == "left" else -1.0
        # lateral shim (m): if the threshold lat/lon are slightly off the real
        # centerline, a centered aircraft reads a constant cross error. Subtract
        # it here so map + lesson logic agree. See design doc calibration note.
        self.centerline_offset_m = float(lesson["pattern"].get("centerline_offset_m", 0.0))
        self.xwind_agl = lesson["pattern"]["crosswind_turn_agl_ft"]
        pat = lesson["pattern"]
        self._pattern_off_m = float(pat.get("downwind_offset_nm", 0.8)) * 1852.0
        self._downwind_phase_cross_min = self._pattern_off_m * float(
            pat.get("downwind_turn_cross_frac", 0.88))
        self.total_circuits = total_circuits
        # Take-off lesson: the lesson ends on the climb-out at this height (AGL)
        self.end_agl = float(lesson.get("end_at_agl_ft") or 0) or None
        # Go-Around lesson: FINAL -> GO_AROUND when descending through
        # go_around_agl (set by the engine: a random call height) or when the
        # trainee goes around first; the lesson ends climbing through ga_end_agl
        self.go_around = lesson.get("variation") == "go_around"
        self.go_around_agl: float | None = None
        self.ga_end_agl = float((lesson.get("go_around") or {}).get("end_agl_ft", 1000))
        # the rollout ends (-> debrief) below this speed; Short-Field waits for
        # a full stop -- the stopping point is part of its score
        short = lesson.get("variation") == "short_field"
        self.rollout_end_kt = (2.0 if short else
                               10.0 if lesson.get("variation") == "soft_field" else 25.0)
        self._still_kt = 3.0 if short else 10.0       # "parked and quiet" safety net
        if self.go_around:
            order = list(self.ORDER)
            order.insert(order.index("FINAL") + 1, "GO_AROUND")
            self.ORDER = order
        self.phase = "BRIEFING"
        self.circuit = 1
        self._still_since = None

    def geometry(self, tel: Telemetry) -> Geometry:
        dn = (tel.lat - self.lat0) * 111320.0
        de = (tel.lon - self.lon0) * 111320.0 * math.cos(math.radians(self.lat0))
        h = math.radians(self.hdg_rw)
        along = de * math.sin(h) + dn * math.cos(h)
        # left of centerline is positive when side=left
        left = -de * math.cos(h) + dn * math.sin(h) - self.centerline_offset_m
        return Geometry(along, left * self.side, hdg_diff(tel.hdg, self.hdg_rw))

    def advance(self, to: str) -> str:
        self.phase = to
        return to

    def tick(self, tel: Telemetry, g: Geometry) -> str | None:
        """Returns the new phase name if a transition happened.

        Transitions are self-healing: each has a position-based fallback so a
        missed timing window (late tick, odd flying) can't leave the tracker
        stuck a lap behind."""
        import time as _time
        p = self.phase
        # turning toward pattern side: left traffic -> hdg_off goes negative
        turn_sign = -self.side
        on_dw_hdg = abs(abs(g.hdg_off) - 180) < 30           # reciprocal heading
        away = g.cross > 250                                  # displaced to pattern side

        # lesson-over safety net: parked and quiet -> debrief, from any late phase
        if p in ("FINAL", "FLARE_TOUCHDOWN", "ROLLOUT") and tel.on_ground and tel.rolling_kt < self._still_kt:
            if self._still_since is None:
                self._still_since = _time.time()
            elif _time.time() - self._still_since > 6:
                return self.advance("DEBRIEF")
        else:
            self._still_since = None

        if p == "PRE_TAKEOFF" and tel.on_ground and (tel.ias > 15 or tel.throttle > 0.8):
            return self.advance("TAKEOFF_ROLL")
        if p in ("PRE_TAKEOFF", "TAKEOFF_ROLL") and not tel.on_ground and tel.alt_agl > 50:
            return self.advance("CLIMB_OUT")   # also catches 'already airborne' starts
        if p in ("CLIMB_OUT", "CROSSWIND") and self.end_agl and tel.alt_agl >= self.end_agl:
            return self.advance("DEBRIEF")     # take-off lesson: climbed out, done
        if p == "CLIMB_OUT":
            if on_dw_hdg and away and g.cross >= self._downwind_phase_cross_min:
                return self.advance("DOWNWIND")
            if (tel.alt_agl > min(300.0, self.xwind_agl * 0.9) and turn_sign * g.hdg_off > 30) \
                    or (away and abs(g.hdg_off) > 45):
                return self.advance("CROSSWIND")
        if p == "CROSSWIND" and on_dw_hdg and g.cross >= self._downwind_phase_cross_min:
            return self.advance("DOWNWIND")
        if p == "DOWNWIND" and 30 < abs(abs(g.hdg_off) - 180) < 160 and g.along < 200:
            return self.advance("BASE")
        if p in ("BASE", "DOWNWIND") and abs(g.hdg_off) < 30 and not tel.on_ground \
                and abs(g.cross) < 400 and g.along < 0:       # lined up with the runway
            return self.advance("FINAL")
        if p == "FINAL" and self.go_around and not tel.on_ground and (
                (self.go_around_agl and tel.alt_agl <= self.go_around_agl and tel.vs < 100)
                or (tel.throttle >= 0.85 and tel.vs > 300 and tel.alt_agl > 50)):
            return self.advance("GO_AROUND")
        if p == "GO_AROUND":
            if tel.alt_agl >= self.ga_end_agl:
                return self.advance("DEBRIEF")
            if tel.on_ground:
                return self.advance("ROLLOUT")    # landed after all
        if p == "FINAL" and tel.alt_agl < 30:
            return self.advance("FLARE_TOUCHDOWN")
        if p == "FLARE_TOUCHDOWN" and tel.on_ground:
            return self.advance("ROLLOUT")
        if p in ("ROLLOUT", "FLARE_TOUCHDOWN"):
            if p == "ROLLOUT" and self.circuit >= self.total_circuits and tel.rolling_kt < self.rollout_end_kt:
                return self.advance("DEBRIEF")
            if (not tel.on_ground and tel.alt_agl > 50
                    and self.circuit < self.total_circuits):   # touch-and-go (multi-circuit only)
                self.circuit += 1
                return self.advance("CLIMB_OUT")
        return None
