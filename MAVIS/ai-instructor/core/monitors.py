"""Continuous monitors: judge flight + control inputs within a phase and
emit CoachEvents. Thresholds are deliberately forgiving - gentle teacher."""
from __future__ import annotations
import math

from adapters.sim_base import Telemetry
from adapters.input_hid import ControlSample
from .events import CoachEvent
from .phase_detector import Geometry, hdg_diff
from .reference_profiles import ReferenceProfile


def roll_out_lead(bank: float) -> float:
    """Degrees before the new heading to call "stop turning": half the bank,
    as a pilot leads a roll-out (20 deg bank -> 10 deg early)."""
    return max(5.0, min(25.0, abs(bank) / 2))


def final_speed_band(lesson: dict, v_speeds: dict, agl: float):
    """Final approach speed band for a lesson with final_speed_band_kt
    (e.g. the Landing lesson): (lo, hi, aim) kt, or None. The aim is the
    approach speed, sliding down to touchdown_speed_kt over the last
    slow_down_below_agl_ft; the band is aim +/- final_speed_band_kt, never
    below min_speed_kt (kept clear of the stall)."""
    band = lesson.get("final_speed_band_kt")
    if not band:
        return None
    fin = float(lesson.get("final_speed_kt") or v_speeds["final"])
    td = float(lesson.get("touchdown_speed_kt", fin))
    below = float(lesson.get("slow_down_below_agl_ft", 100))
    f = max(0.0, min(1.0, agl / below)) if below > 0 else 1.0
    aim = td + (fin - td) * f
    return max(float(lesson.get("min_speed_kt", 0)), aim - band), aim + band, aim


def band(lo=None, hi=None, target=None, kind: str = "band") -> dict:
    """One dial range for IRIS's Live Monitor (state["bands"]): lo/hi = what's
    allowed (either may be None: a one-sided limit), target = the number aimed
    for. kind "band" = what this step asks for; "limit" = only a safety limit
    (the coach speaks up past it)."""
    r = lambda v: None if v is None else round(float(v), 1)
    return {"lo": r(lo), "hi": r(hi), "target": r(target), "kind": kind}


class Monitors:
    def __init__(self, aircraft: dict, lesson: dict, sim_adapter: str = "mock"):
        v = aircraft["v_speeds"]
        f = aircraft["flaps"]
        self._lesson, self._v = lesson, v
        # how far off the centreline on final before an "off track" nudge (m)
        self.centreline_nudge_m = float(lesson.get("centreline_nudge_m", 60))
        # ... and off the other straight legs (climb-out, downwind, base)
        self.off_track_nudge_m = float(lesson.get("off_track_nudge_m", 150))
        # the crosswind leg's planned line is only a guide (the turn may come
        # earlier, at turn height), so it gets more room
        self.crosswind_off_track_m = float(lesson.get("crosswind_off_track_m", 300))
        # rotate call speed: the aircraft's Vr, or the lesson's own rotate_kt
        self.vr = float(lesson.get("rotate_kt") or v["vr"])
        # Soft-Field: no "rotate now" -- the stick is back from the start of the roll
        self._rotate_call = not lesson.get("no_rotate_call")
        self.tgt_speed = {"CLIMB_OUT": v["vy"], "CROSSWIND": v["vy"],
                          "DOWNWIND": v["pattern_downwind"], "BASE": v["base"],
                          "FINAL": round(float(lesson.get("final_speed_kt") or v["final"]))}
        self.tgt_flaps = {"DOWNWIND": f["downwind"], "BASE": f["base"], "FINAL": f["final"]}
        # legs flown without a speed to hold (the Take-off lesson's climb-out):
        # no speed shown, called or nudged there
        for ph in lesson.get("no_speed_target") or []:
            self.tgt_speed.pop(ph, None)
        # climb legs flown to a band rather than one number (lesson
        # climb_speed_band_kt): nudged only once well outside it
        b = lesson.get("climb_speed_band_kt")
        self.speed_band = ({ph: (float(b[0]), float(b[1])) for ph in ("CLIMB_OUT", "CROSSWIND")
                            if ph in self.tgt_speed} if b else {})
        self.pattern_alt = lesson["pattern"]["altitude_msl_ft"]
        self.xwind_agl = lesson["pattern"]["crosswind_turn_agl_ft"]
        rw_len = float(lesson["runway"]["length_m"])
        self._runway_length_m = rw_len
        # Crosswind turn is after the runway ends, height + position (not mid-runway).
        self._turn_crosswind_along_min = float(
            # (the departure end: the planned circuit's rounded crosswind turn starts there)
            lesson["pattern"].get("crosswind_turn_min_along_m", rw_len))
        self.vy = float(aircraft["v_speeds"]["vy"])
        self.turn_word = "left" if lesson["pattern"].get("side", "left") == "left" else "right"
        # circuit turns: left traffic turns left (heading offset decreasing)
        self._turn_sign = -1.0 if self.turn_word == "left" else 1.0
        # heading offset from the runway heading each turn ends on
        s = self._turn_sign
        self._leg_hdg_off = {"TURN_CROSSWIND": 90.0 * s, "TURN_DOWNWIND": 180.0,
                             "TURN_BASE": -90.0 * s, "TURN_FINAL": 0.0}
        self._roll_out: tuple[str, float] | None = None   # (turn code, time armed)
        pat = lesson["pattern"]
        fin_planned = float(pat.get("base_turn_past_abeam_m")
                            or lesson.get("scoring", {}).get("final_length_m", 500))
        fin_mock = float(pat.get("mock_base_turn_past_abeam_m", 1000.0))
        # Mock autopilot path is shorter; keep voice cue geometry on that leg unless MSFS.
        fin = fin_mock if sim_adapter == "mock" else fin_planned
        self._fin = fin
        # Cue ~280 m before the planned base corner (matches rounded grey circuit arc)
        self._turn_base_along = float(
            lesson["pattern"].get("base_turn_along_m", -(fin - 280)))
        self._turn_final_cross = 250.0
        self._default_turn_base_along = self._turn_base_along
        self._default_tgt_speed = dict(self.tgt_speed)
        self._default_pattern_alt = self.pattern_alt
        self._default_turn_final_cross = self._turn_final_cross
        self._rotate_called = False
        self._phase_prev = None
        self._phase_t = 0.0
        self._turn_last: dict[str, float] = {}   # last time each turn cue fired
        self._turn_spoken: set[str] = set()      # turn prompts delivered this circuit
        off_m = float(lesson["pattern"].get("downwind_offset_nm", 0.8)) * 1852.0
        self._pattern_off_m = off_m
        self._up_a = float(lesson["runway"]["length_m"]) + 420.0
        # Downwind turn cue: near the END of the crosswind leg (not halfway out).
        self._downwind_cue_cross_min = off_m * float(
            pat.get("downwind_turn_cross_frac", 0.88))
        self._downwind_cue_along_min = self._up_a - float(
            pat.get("downwind_turn_along_margin_m", 250))
        self._turn_cue_min_gap_s = float(pat.get("turn_cue_min_gap_s", 14.0))
        # Real sim: downwind / base / final cues fire one turn radius (at the
        # current speed) plus the time to voice the call ahead of the next leg,
        # so a trainee who turns on the word rolls out on the leg, not past it.
        # The mock autopilot turns at its own fixed corners, so mock keeps the
        # fixed cue points below; so does a lesson that pins a cue point.
        self._lead_turns = sim_adapter != "mock"
        self._lead_downwind = self._lead_turns and "downwind_turn_cross_frac" not in pat
        self._lead_base = self._lead_turns and "base_turn_along_m" not in pat
        self._cue_bank = float(pat.get("turn_cue_bank_deg", 25))
        # voice to say it (~1-2 s) + the trainee's reaction time to start rolling
        self._cue_lead_s = float(pat.get("turn_cue_lead_s", 4.0))
        self._ref_turns = False    # an expert reference supplies its own turn points
        # A slow climb must not carry the trainee far past the planned circuit:
        # the crosswind turn is also called on position (approaching the planned
        # corner), once at least this high -- whichever comes first.
        self._xwind_min_agl = float(pat.get("crosswind_turn_min_agl_ft", 400))
        # Take-off lesson: straight ahead to end_at_agl_ft and done -- no turn
        # is ever called (heads-up, turn or roll-out)
        self._no_turns = lesson.get("segment") == "takeoff"
        # sat-nav style heads-up ("in 500 metres, get ready to turn left")
        # this many seconds before each turn call; 0 = off
        self._pre_turn_s = float(pat.get("turn_precue_s", 12.0))
        self._pre_spoken: set[str] = set()       # turns already announced this circuit
        self.grace_s = 8.0     # settle-in for speed/alt nudges only (not turn cues)
        # Mid-downwind flap callout: halfway along the long leg (up_a → -fin), not past the threshold.
        self._abeam_along_mid = float(
            pat.get("abeam_flaps_along_m", (self._up_a - fin) / 2.0))
        self._abeam_prev = False

    def apply_reference(self, prof: ReferenceProfile) -> None:
        """Swap speed/alt targets and turn thresholds to match the active reference."""
        self.tgt_speed = dict(self._default_tgt_speed)
        self.pattern_alt = self._default_pattern_alt
        self._turn_base_along = self._default_turn_base_along
        self._turn_final_cross = self._default_turn_final_cross
        self._ref_turns = prof.id != "planned"
        if prof.id == "planned":
            return
        for phase, td in (prof.targets or {}).items():
            if "ias" in td:
                self.tgt_speed[phase] = float(td["ias"])
        self.pattern_alt = int(prof.circuit_alt(self._default_pattern_alt))
        tp = prof.turn_points or {}
        tb = tp.get("TURN_BASE") or {}
        tf = tp.get("TURN_FINAL") or {}
        if "along_max" in tb:
            self._turn_base_along = float(tb["along_max"])
        if "cross_max" in tf:
            self._turn_final_cross = float(tf["cross_max"])

    def turn_lead_m(self, ias: float) -> float:
        """Distance before the next leg to call the turn: the turn radius at
        this speed and turn_cue_bank_deg, plus turn_cue_lead_s of travel for
        the voice to say it and the trainee to react."""
        v = max(ias, 50.0) * 0.5144        # kt -> m/s
        r = v * v / (9.81 * math.tan(math.radians(self._cue_bank)))
        return r + v * self._cue_lead_s

    # straight legs on the planned circuit: a point on the line and the
    # direction it's flown, in (along, cross) metres.
    def _leg_line(self, phase: str):
        if phase in ("CLIMB_OUT", "FINAL", "GO_AROUND"):
            return (0.0, 0.0), (1.0, 0.0)
        if phase == "CROSSWIND":
            return (self._up_a, 0.0), (0.0, 1.0)
        if phase == "DOWNWIND":
            return (0.0, self._pattern_off_m), (-1.0, 0.0)
        if phase == "BASE":
            return (-self._fin, 0.0), (0.0, -1.0)
        return None

    def _off_track_cue(self, phase: str, tel: Telemetry, g: Geometry, settling: bool, now: float):
        """Off the planned leg and not heading back to it: say which way to
        turn. Quiet while turning, settling into a new leg, or with a turn
        call / roll-out still to come (the turn itself fixes it)."""
        line = self._leg_line(phase)
        # mock autopilot flies its own path: only the centreline on final applies there
        if (not line or (not self._lead_turns and phase != "FINAL") or self._ref_turns
                or tel.on_ground or settling
                or self._roll_out or abs(tel.bank) > 10
                or now - max(self._turn_last.values(), default=0.0) < 15):
            return None
        if phase == "CLIMB_OUT" and tel.alt_agl < 200:
            return None
        if phase == "FINAL" and tel.alt_agl < 100:
            return None
        (a0, c0), (da, dc) = line
        pa, pc = g.along - a0, g.cross - c0
        off = pa * dc - pc * da            # signed distance from the line
        limit = {"FINAL": self.centreline_nudge_m,
                 "CROSSWIND": self.crosswind_off_track_m}.get(phase, self.off_track_nudge_m)
        if abs(off) <= limit:
            return None
        # aim at a point 600 m further along the line: the way to turn is
        # toward it (heading offsets: +cross is 90 deg toward the pattern side)
        along_line = pa * da + pc * dc + 600.0
        va, vc = a0 + da * along_line - g.along, c0 + dc * along_line - g.cross
        hdg = lambda a, c: math.degrees(math.atan2(c * self._turn_sign, a))
        want = hdg(va, vc)
        err = hdg_diff(want, g.hdg_off)
        toward = hdg_diff(want, hdg(da, dc))   # sign: which way the line lies
        if abs(err) < 10 or err * toward < 0:
            return None                    # already heading back to it (or cutting back in steeply)
        leg = {"CLIMB_OUT": "runway centreline", "FINAL": "centreline",
               "GO_AROUND": "runway centreline"}.get(phase, phase.lower() + " leg")
        # how much to turn: the intercept toward that point, 10-45 deg
        deg = int(min(45, max(10, round(abs(err) / 5) * 5)))
        # a correction (not a nudge), so a busy cockpit doesn't drop it; repeated
        # sooner the further off it is
        return CoachEvent("OFF_TRACK", "correction",
                          {"dir": "right" if err > 0 else "left", "leg": leg,
                           "off": round(abs(off)), "deg": deg},
                          cooldown_s=8 if abs(off) > 2 * limit else 12)

    def bands_for(self, phase: str, tel=None) -> dict:
        """The tolerances this class coaches against in this phase, for the
        Live Monitor's dials: speed band (or target +/- 10 kt, the callout
        point), circuit height +/- 100 ft on downwind, the steep-bank limit,
        and the sink-rate limit on final. Altitudes are MSL."""
        out: dict = {}
        airborne = tel is None or not tel.on_ground
        spd, b = self.tgt_speed.get(phase), self.speed_band.get(phase)
        fb = final_speed_band(self._lesson, self._v, tel.alt_agl if tel else 0.0) if phase == "FINAL" else None
        if fb:
            out["speed"] = band(fb[0], fb[1], fb[2])
        elif b:
            out["speed"] = band(b[0], b[1], (b[0] + b[1]) / 2)
        elif spd:
            out["speed"] = band(spd - 10, spd + 10, spd)
        if phase == "DOWNWIND":
            out["alt"] = band(self.pattern_alt - 100, self.pattern_alt + 100, self.pattern_alt)
        elif phase == "CROSSWIND":
            out["alt"] = band(target=self.pattern_alt)          # climbing to it
        if airborne and phase not in ("TAKEOFF_ROLL", "ROLLOUT"):
            out["bank"] = band(-32, 32, kind="limit")
        if phase == "FINAL":
            out["vs"] = band(lo=-900, kind="limit")
        return out

    def targets_for(self, phase: str) -> dict:
        # show the circuit-height band while climbing to it, not just on downwind
        alt = self.pattern_alt if phase in ("CROSSWIND", "DOWNWIND") else None
        return {"speed": self.tgt_speed.get(phase),
                "alt": alt,
                "flaps": self.tgt_flaps.get(phase)}

    def _turn_cue(self, code: str, cond: bool, data: dict, now: float):
        """Emit a turn prompt once when the condition becomes true (edge-triggered)."""
        prev_attr = f"_turn_prev_{code}"
        was = getattr(self, prev_attr, False)
        setattr(self, prev_attr, cond)
        if cond and not was and code not in self._turn_spoken:
            self._turn_last[code] = now
            self._turn_spoken.add(code)
            self._roll_out = (code, now)
            return CoachEvent(code, "correction", data, cooldown_s=0)
        return None

    def _pre_turn(self, code: str, secs: float, dist_m: float | None, data: dict):
        """Heads-up before a turn call, once per turn: when the turn point is
        _pre_turn_s away, but not so close it would run into the call itself."""
        if (not self._pre_turn_s or code in self._pre_spoken or code in self._turn_spoken
                or not 5.0 <= secs <= self._pre_turn_s):
            return None
        self._pre_spoken.add(code)
        d = {**data, "leg": code.removeprefix("TURN_").lower()}
        if dist_m is not None:
            d["dist"] = max(100, int(dist_m // 100) * 100)   # "in 500 metres", rounded down
        return CoachEvent("PRE_TURN" if dist_m is not None else "PRE_TURN_ALT",
                          "correction", d, cooldown_s=0)

    def catchup_turn(self, new_phase: str) -> CoachEvent | None:
        """Phase advanced before the proactive turn line was spoken, deliver it now."""
        need = {
            "CROSSWIND": "TURN_CROSSWIND",
            "DOWNWIND": "TURN_DOWNWIND",
            "BASE": "TURN_BASE",
            "FINAL": "TURN_FINAL",
        }.get(new_phase)
        if not need or need in self._turn_spoken:
            return None
        self._turn_spoken.add(need)
        import time as _time
        self._turn_last[need] = _time.time()
        self._roll_out = (need, _time.time())
        d: dict = {"dir": self.turn_word}
        if need == "TURN_CROSSWIND":
            d["xwind"] = int(self.xwind_agl)
        if need == "TURN_DOWNWIND":
            d["alt"] = self.pattern_alt
        return CoachEvent(need, "correction", d, cooldown_s=0)

    def reset_turn_cues(self):
        self._pre_spoken.clear()
        self._turn_spoken.clear()
        self._turn_last.clear()
        self._abeam_prev = False
        self._roll_out = None
        for code in ("TURN_CROSSWIND", "TURN_DOWNWIND", "TURN_BASE", "TURN_FINAL"):
            setattr(self, f"_turn_prev_{code}", False)

    def _roll_out_cue(self, tel: Telemetry, g: Geometry, now: float):
        """After a turn cue: once the heading nears the new leg's (or has gone
        past it) while the wings are still banked into the turn, tell the
        trainee to stop turning."""
        if not self._roll_out:
            return None
        code, t0 = self._roll_out
        if tel.on_ground or now - t0 > 90:
            self._roll_out = None              # never turned / landed: drop it
            return None
        # degrees still to turn (negative = past the new heading)
        rem = hdg_diff(self._leg_hdg_off[code], g.hdg_off) * self._turn_sign
        bank_in = tel.bank * self._turn_sign   # + = banked into the turn
        if not -45 <= rem <= roll_out_lead(tel.bank):
            return None
        self._roll_out = None
        if bank_in < 8:
            return None                        # already rolling out on their own
        leg = code.removeprefix("TURN_").lower()
        return CoachEvent("ROLL_OUT_LEG", "correction", {"leg": leg}, cooldown_s=0)

    def tick(self, phase: str, tel: Telemetry, g: Geometry,
             ctrl: ControlSample) -> list[CoachEvent]:
        import time as _time
        now = _time.time()
        if phase != self._phase_prev:
            self._phase_prev = phase
            self._phase_t = now
            if phase == "DOWNWIND":
                self._abeam_prev = False
            for code in ("TURN_CROSSWIND", "TURN_DOWNWIND", "TURN_BASE", "TURN_FINAL"):
                setattr(self, f"_turn_prev_{code}", False)
        settling = now - self._phase_t < self.grace_s
        ev: list[CoachEvent] = []
        E = ev.append

        # ---- proactive turn cues (NOT gated by phase settle-in, must beat phase advance) ----
        d = {"dir": self.turn_word, "xwind": int(self.xwind_agl)}
        v = max(tel.ias, 50.0) * 0.5144          # m/s, for "seconds to the turn"
        if phase == "CLIMB_OUT" and not self._no_turns:
            # position: one turn lead before the planned crosswind line, but
            # never before the runway end (+ margin)
            line_at = max(self._up_a - self.turn_lead_m(tel.ias), self._turn_crosswind_along_min)
            on_position = self._lead_turns and not self._ref_turns
            if not tel.on_ground:
                # height: seconds until both turn height and the runway end are
                # reached; position: seconds to the planned corner -- first wins
                t_alt = max(0.0, self.xwind_agl - 20 - tel.alt_agl) / (max(tel.vs, 100.0) / 60.0)
                t_pos = max(0.0, self._turn_crosswind_along_min - g.along) / v
                # (the position call also waits for the minimum turn height)
                t_min = max(0.0, self._xwind_min_agl - tel.alt_agl) / (max(tel.vs, 100.0) / 60.0)
                t_line = max((line_at - g.along) / v, t_min) if on_position else 1e9
                if max(t_alt, t_pos) <= t_line:
                    t_h = max(t_alt, t_pos)
                    # "coming up to 600 feet" only when it's the height we're waiting for
                    c = self._pre_turn("TURN_CROSSWIND", t_h, None if t_alt > t_pos else t_h * v, d)
                else:
                    c = self._pre_turn("TURN_CROSSWIND", t_line, t_line * v, d)
                if c: E(c)
            by_height = (tel.ias >= self.vy - 12
                         and tel.alt_agl >= self.xwind_agl - 20
                         and g.along >= self._turn_crosswind_along_min)
            by_position = (on_position and g.along >= line_at
                           and tel.alt_agl >= self._xwind_min_agl)
            xwind_ready = not tel.on_ground and abs(g.hdg_off) < 30 and (by_height or by_position)
            # the call says the height we're actually at
            d = {**d, "xwind": int(tel.alt_agl // 50 * 50)}
            c = self._turn_cue("TURN_CROSSWIND", xwind_ready, d, now)
            if c: E(c)
        elif phase == "CROSSWIND":
            last_xw = self._turn_last.get("TURN_CROSSWIND", 0.0)
            gap_ok = (not last_xw) or (now - last_xw) >= self._turn_cue_min_gap_s
            if self._lead_downwind:
                # the call says "level off around {alt}": no need to wait for the height
                to_go = self._pattern_off_m - g.cross - self.turn_lead_m(tel.ias)
                near = to_go <= 0
            else:
                to_go = self._downwind_cue_cross_min - g.cross
                near = (tel.alt_msl >= self.pattern_alt - 120
                        and g.cross >= self._downwind_cue_cross_min)
            downwind_ready = (
                gap_ok
                and near
                and g.along >= self._downwind_cue_along_min
                and abs(abs(g.hdg_off) - 180) > 20)
            if gap_ok:
                c = self._pre_turn("TURN_DOWNWIND", to_go / v, to_go, d)
                if c: E(c)
            c = self._turn_cue("TURN_DOWNWIND", downwind_ready,
                               {**d, "alt": self.pattern_alt}, now)
            if c: E(c)
        elif phase == "DOWNWIND":
            if self._lead_base and not self._ref_turns:
                base_at = -self._fin + self.turn_lead_m(tel.ias)
            else:
                base_at = self._turn_base_along
            to_go = g.along - base_at
            c = self._pre_turn("TURN_BASE", to_go / v, to_go, d)
            if c: E(c)
            c = self._turn_cue("TURN_BASE", g.along < base_at, d, now)
            if c: E(c)
        elif phase == "BASE":
            final_at = (self.turn_lead_m(tel.ias) if self._lead_turns and not self._ref_turns
                        else self._turn_final_cross)
            if abs(g.hdg_off) > 30:              # flying the base leg, not already turning in
                to_go = abs(g.cross) - final_at
                c = self._pre_turn("TURN_FINAL", to_go / v, to_go, d)
                if c: E(c)
            c = self._turn_cue("TURN_FINAL",
                               abs(g.cross) < final_at and abs(g.hdg_off) > 30,
                               d, now)
            if c: E(c)
        c = self._roll_out_cue(tel, g, now)
        if c: E(c)

        if phase == "TAKEOFF_ROLL":
            if tel.ias >= self.vr and not self._rotate_called and self._rotate_call:
                self._rotate_called = True
                E(CoachEvent("ROTATE_NOW", "correction",
                             {"vr": self.vr}, cooldown_s=60))
            # timely-control check: called rotate but no back pressure yet
            if self._rotate_called and tel.ias > self.vr + 8 and tel.on_ground \
                    and (ctrl.available and ctrl.pitch < 0.08 or tel.elevator > -0.05):
                E(CoachEvent("ROTATE_LATE", "correction", {}, cooldown_s=15))
        else:
            self._rotate_called = False

        spd = self.tgt_speed.get(phase)
        band = self.speed_band.get(phase)
        slack = 5.0                          # climb band: nudge once well outside it
        if phase == "FINAL" and band is None:
            fb = final_speed_band(self._lesson, self._v, tel.alt_agl)
            band = fb[:2] if fb else None
            slack = 0.0                      # final: the band's already wide; its floor guards the stall
        # downwind is flown level: there the speed is the throttle's job;
        # climbing / descending legs it's the nose's (the *_LEVEL wording)
        lvl = "_LEVEL" if phase == "DOWNWIND" else ""
        if band and not tel.on_ground and not settling:
            lo, hi = band                    # e.g. 70-90 kt: "toward 90" / "toward 70"
            if tel.ias > hi + slack:
                E(CoachEvent("SPEED_FAST" + lvl, "nudge", {"ias": round(tel.ias), "tgt": round(hi)}))
            elif tel.ias < lo - slack:
                sev = "correction" if tel.ias < lo - slack - 5 else "nudge"
                E(CoachEvent("SPEED_SLOW" + lvl, sev, {"ias": round(tel.ias), "tgt": round(lo)}))
        elif spd and not tel.on_ground and not settling:
            if tel.ias > spd + 10:
                E(CoachEvent("SPEED_FAST" + lvl, "nudge", {"ias": round(tel.ias), "tgt": spd}))
            elif tel.ias < spd - 10:
                sev = "correction" if tel.ias < spd - 15 else "nudge"
                E(CoachEvent("SPEED_SLOW" + lvl, sev, {"ias": round(tel.ias), "tgt": spd}))

        if phase == "DOWNWIND" and not settling:
            dv = tel.alt_msl - self.pattern_alt
            if abs(dv) > 100:
                # off height: low and fast -> the nose (the extra speed climbs
                # us back up); high and slow -> the nose too; otherwise power
                dw = self.tgt_speed.get("DOWNWIND") or 85
                if dv < 0:
                    code = "ALT_LOW_FAST" if tel.ias > dw + 5 else "ALT_LOW"
                else:
                    code = "ALT_HIGH_SLOW" if tel.ias < dw - 5 else "ALT_HIGH"
                E(CoachEvent(code, "nudge", {"off": round(abs(dv)), "tgt": self.pattern_alt}))
                if code in ("ALT_LOW_FAST", "ALT_HIGH_SLOW"):
                    # the nose fixes height and speed together: no
                    # contradicting "more / less power" speed call as well
                    clash = "SPEED_FAST_LEVEL" if code == "ALT_LOW_FAST" else "SPEED_SLOW_LEVEL"
                    ev[:] = [x for x in ev if x.code != clash]
            # Mid-downwind: flap + power (halfway along the long leg, not at base-turn end)
            abeam_ready = g.along < self._abeam_along_mid and tel.flaps_deg < 5
            if abeam_ready and not self._abeam_prev:
                E(CoachEvent("ABEAM_FLAPS", "nudge",
                             {"flaps": self.tgt_flaps["DOWNWIND"]}, cooldown_s=30))
            self._abeam_prev = abeam_ready

        fl = self.tgt_flaps.get(phase)
        # a reminder only: the leg's own call has just asked for these flaps
        if (fl and phase in ("BASE", "FINAL") and tel.flaps_deg < fl - 6
                and now - self._phase_t > 15):
            E(CoachEvent("FLAPS_REMINDER", "nudge", {"flaps": fl}, cooldown_s=25))

        if not tel.on_ground and abs(tel.bank) > 32:
            sev = "safety" if abs(tel.bank) > 45 else "nudge"
            E(CoachEvent("BANK_STEEP", sev, {"bank": round(abs(tel.bank))}, cooldown_s=12))

        c = self._off_track_cue(phase, tel, g, settling, now)
        if c: E(c)

        if phase == "FINAL":
            if tel.vs < -900 and not settling:
                # sinking fast: with the speed up as well the nose is too low --
                # ease it up, don't add power (that only adds speed); otherwise
                # it's a power problem: a little more throttle
                fb = final_speed_band(self._lesson, self._v, tel.alt_agl)
                aim = fb[2] if fb else (self.tgt_speed.get("FINAL") or 65)
                code = "SINK_HIGH_FAST" if tel.ias > aim + 5 else "SINK_HIGH"
                E(CoachEvent(code, "correction", {"vs": round(-tel.vs), "ias": round(tel.ias)},
                             cooldown_s=15))

        # control smoothness (raw HID): large rapid stick reversals
        if ctrl.available and not tel.on_ground and phase in ("FINAL", "FLARE_TOUCHDOWN"):
            if abs(ctrl.roll) > 0.65 or abs(ctrl.pitch) > 0.7:
                E(CoachEvent("SMOOTH_HANDS", "nudge", {}, cooldown_s=20))
        return ev
