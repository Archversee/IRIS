"""Free Flight phase tracker: take-off, landing and the flying between, with no runway.

The circuit PhaseDetector (core/phase_detector.py) places the aircraft against runway 02
at Paya Lebar and waits for it to fly crosswind / downwind / base / final around it. Free
Flight has no set track -- the pilot goes anywhere -- so that detector stalls on whatever
leg it last saw (and never sees a landing made elsewhere). This tracker uses only what the
aircraft itself reports: on the ground or not, height above ground, speed, climb rate and
throttle. Phase names reuse the circuit ones where they mean the same thing, so SOP rules
written for them (take-off roll, climb-out, final, flare, rollout) still line up:

  PRE_TAKEOFF     on the ground, not rolling (before take-off, or stopped after landing)
  TAKEOFF_ROLL    on the ground and accelerating / full power
  CLIMB_OUT       lifted off, climbing out to ~700 ft AGL
  AIRBORNE        flying: anything between climb-out and the approach
  FINAL           low (< 800 ft AGL) and descending steadily: the approach to land
  FLARE_TOUCHDOWN within 30 ft of the ground, descending
  ROLLOUT         on the ground after landing, slowing
A touch-and-go goes ROLLOUT -> CLIMB_OUT again; a go-around goes FINAL -> AIRBORNE.
"""
from __future__ import annotations

from adapters.sim_base import Telemetry

# Display names for the event log (circuit lessons keep their own plain names)
LABELS = {
    "PRE_TAKEOFF": "Stopped on the ground",
    "TAKEOFF_ROLL": "Take-off roll",
    "CLIMB_OUT": "Climb-out",
    "AIRBORNE": "Airborne",
    "FINAL": "Final approach",
    "FLARE_TOUCHDOWN": "Flare & touchdown",
    "ROLLOUT": "Landing rollout",
}

LIFTOFF_AGL_FT = 50.0        # same threshold the circuit detector uses
CLIMB_DONE_AGL_FT = 700.0    # climb-out ends here ...
FINAL_MAX_AGL_FT = 800.0     # below this, a steady descent is an approach
FINAL_VS_FPM = -200.0
GO_AROUND_VS_FPM = 300.0
GO_AROUND_AGL_FT = 1200.0
FLARE_AGL_FT = 30.0
HOLD_S = 6.0                 # a condition must hold this long (turbulence / a dip is not a phase)
STOPPED_KT = 8.0


class FreeFlightTracker:
    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.phase = "PRE_TAKEOFF"
        self._since: dict[str, float | None] = {}
        self._rollout_min = 1e9     # slowest speed seen since touchdown

    def _held(self, key: str, cond: bool, t: float, need: float = HOLD_S) -> bool:
        """True once `cond` has been true continuously for `need` seconds."""
        if not cond:
            self._since[key] = None
            return False
        if self._since.get(key) is None:
            self._since[key] = t
        return t - self._since[key] >= need

    def _go(self, phase: str) -> str:
        self.phase = phase
        self._since.clear()
        return phase

    def tick(self, tel: Telemetry) -> str:
        p, t = self.phase, tel.t
        agl, ias, vs = tel.alt_agl, tel.ias, tel.vs
        if tel.on_ground:
            if p in ("CLIMB_OUT", "AIRBORNE", "FINAL"):
                return self._go("FLARE_TOUCHDOWN")               # wheels down: always logs a touchdown,
                                                                 # even when the flare fell between samples
            if p == "FLARE_TOUCHDOWN":
                self._rollout_min = ias
                return self._go("ROLLOUT")
            if p == "PRE_TAKEOFF" and (ias > 15 or (tel.throttle > 0.8 and ias > 5)):   # actually moving
                return self._go("TAKEOFF_ROLL")
            if p == "ROLLOUT":
                self._rollout_min = min(self._rollout_min, ias)
                if tel.throttle > 0.8 and ias > self._rollout_min + 8:
                    return self._go("TAKEOFF_ROLL")              # speeding up again: touch-and-go
                if self._held("stopped", ias < STOPPED_KT, t, 3.0):
                    return self._go("PRE_TAKEOFF")               # landed and stopped
            if p == "TAKEOFF_ROLL" and self._held("aborted", ias < STOPPED_KT and tel.throttle < 0.3, t, 5.0):
                return self._go("PRE_TAKEOFF")                   # rejected take-off / never left
            return p
        # ---- in the air ----
        if p in ("PRE_TAKEOFF", "TAKEOFF_ROLL", "ROLLOUT"):
            if agl > LIFTOFF_AGL_FT:
                return self._go("CLIMB_OUT")                     # lift-off (or touch-and-go)
            return p
        if p == "CLIMB_OUT":
            if agl >= CLIMB_DONE_AGL_FT or self._held("desc", vs < -300, t, 5.0):
                return self._go("AIRBORNE")
            return p
        if p == "AIRBORNE":
            if agl < FLARE_AGL_FT and vs < 0:
                return self._go("FLARE_TOUCHDOWN")               # came down without a steady approach
            if self._held("final", agl < FINAL_MAX_AGL_FT and vs < FINAL_VS_FPM, t, 8.0):
                return self._go("FINAL")
            return p
        if p == "FINAL":
            if agl < FLARE_AGL_FT:
                return self._go("FLARE_TOUCHDOWN")
            if agl > GO_AROUND_AGL_FT or self._held("goaround", vs > GO_AROUND_VS_FPM, t, 5.0):
                return self._go("AIRBORNE")                      # go-around / abandoned approach
            return p
        if p == "FLARE_TOUCHDOWN":
            if agl > 100:
                return self._go("AIRBORNE")                      # ballooned / bounced away
            return p
        return p
