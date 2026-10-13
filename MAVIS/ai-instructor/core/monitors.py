"""Continuous monitors: judge flight + control inputs within a phase and
emit CoachEvents. Thresholds are deliberately forgiving - gentle teacher."""
from __future__ import annotations

from adapters.sim_base import Telemetry
from adapters.input_hid import ControlSample
from .events import CoachEvent
from .phase_detector import Geometry
from .reference_profiles import ReferenceProfile


class Monitors:
    def __init__(self, aircraft: dict, lesson: dict, sim_adapter: str = "mock"):
        v = aircraft["v_speeds"]
        f = aircraft["flaps"]
        self.vr = v["vr"]
        self.tgt_speed = {"CLIMB_OUT": v["vy"], "CROSSWIND": v["vy"],
                          "DOWNWIND": v["pattern_downwind"], "BASE": v["base"],
                          "FINAL": v["final"]}
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
            lesson["pattern"].get("crosswind_turn_min_along_m", rw_len + 120.0))
        self.vy = float(aircraft["v_speeds"]["vy"])
        self.turn_word = "left" if lesson["pattern"].get("side", "left") == "left" else "right"
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
            return CoachEvent(code, "correction", data, cooldown_s=0)
        return None

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
        d: dict = {"dir": self.turn_word}
        if need == "TURN_CROSSWIND":
            d["xwind"] = int(self.xwind_agl)
        if need == "TURN_DOWNWIND":
            d["alt"] = self.pattern_alt
        return CoachEvent(need, "correction", d, cooldown_s=0)

    def reset_turn_cues(self):
        self._turn_spoken.clear()
        self._turn_last.clear()
        self._abeam_prev = False
        for code in ("TURN_CROSSWIND", "TURN_DOWNWIND", "TURN_BASE", "TURN_FINAL"):
            setattr(self, f"_turn_prev_{code}", False)

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
        if phase == "CLIMB_OUT":
            xwind_ready = (
                not tel.on_ground
                and tel.ias >= self.vy - 12
                and tel.alt_agl >= self.xwind_agl - 20
                and g.along >= self._turn_crosswind_along_min
                and abs(g.hdg_off) < 30)
            c = self._turn_cue("TURN_CROSSWIND", xwind_ready, d, now)
            if c: E(c)
        elif phase == "CROSSWIND":
            last_xw = self._turn_last.get("TURN_CROSSWIND", 0.0)
            gap_ok = (not last_xw) or (now - last_xw) >= self._turn_cue_min_gap_s
            downwind_ready = (
                gap_ok
                and tel.alt_msl >= self.pattern_alt - 120
                and g.cross >= self._downwind_cue_cross_min
                and g.along >= self._downwind_cue_along_min
                and abs(abs(g.hdg_off) - 180) > 20)
            c = self._turn_cue("TURN_DOWNWIND", downwind_ready,
                               {**d, "alt": self.pattern_alt}, now)
            if c: E(c)
        elif phase == "DOWNWIND":
            c = self._turn_cue("TURN_BASE", g.along < self._turn_base_along, d, now)
            if c: E(c)
        elif phase == "BASE":
            c = self._turn_cue("TURN_FINAL",
                               abs(g.cross) < self._turn_final_cross and abs(g.hdg_off) > 30,
                               d, now)
            if c: E(c)

        if phase == "TAKEOFF_ROLL":
            if tel.ias >= self.vr and not self._rotate_called:
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
        if band and not tel.on_ground and not settling:
            lo, hi = band                    # e.g. 70-90 kt: "toward 90" / "toward 70"
            if tel.ias > hi + 5:
                E(CoachEvent("SPEED_FAST", "nudge", {"ias": round(tel.ias), "tgt": round(hi)}))
            elif tel.ias < lo - 5:
                sev = "correction" if tel.ias < lo - 10 else "nudge"
                E(CoachEvent("SPEED_SLOW", sev, {"ias": round(tel.ias), "tgt": round(lo)}))
        elif spd and not tel.on_ground and not settling:
            if tel.ias > spd + 10:
                E(CoachEvent("SPEED_FAST", "nudge", {"ias": round(tel.ias), "tgt": spd}))
            elif tel.ias < spd - 10:
                sev = "correction" if tel.ias < spd - 15 else "nudge"
                E(CoachEvent("SPEED_SLOW", sev, {"ias": round(tel.ias), "tgt": spd}))

        if phase == "DOWNWIND" and not settling:
            dv = tel.alt_msl - self.pattern_alt
            if abs(dv) > 100:
                E(CoachEvent("ALT_HIGH" if dv > 0 else "ALT_LOW", "nudge",
                             {"off": round(abs(dv)), "tgt": self.pattern_alt}))
            # Mid-downwind: flap + power (halfway along the long leg, not at base-turn end)
            abeam_ready = g.along < self._abeam_along_mid and tel.flaps_deg < 5
            if abeam_ready and not self._abeam_prev:
                E(CoachEvent("ABEAM_FLAPS", "nudge",
                             {"flaps": self.tgt_flaps["DOWNWIND"]}, cooldown_s=30))
            self._abeam_prev = abeam_ready

        fl = self.tgt_flaps.get(phase)
        if fl and phase in ("BASE", "FINAL") and tel.flaps_deg < fl - 6:
            E(CoachEvent("FLAPS_REMINDER", "nudge", {"flaps": fl}, cooldown_s=25))

        if not tel.on_ground and abs(tel.bank) > 32:
            sev = "safety" if abs(tel.bank) > 45 else "nudge"
            E(CoachEvent("BANK_STEEP", sev, {"bank": round(abs(tel.bank))}, cooldown_s=12))

        if phase == "FINAL":
            if abs(g.cross) > 60 and tel.alt_agl > 100:
                E(CoachEvent("CENTERLINE_DRIFT", "nudge",
                             {"side": "left" if g.cross > 0 else "right"}, cooldown_s=15))
            if tel.vs < -900:
                E(CoachEvent("SINK_HIGH", "correction", {"vs": round(-tel.vs)}, cooldown_s=15))

        # control smoothness (raw HID): large rapid stick reversals
        if ctrl.available and not tel.on_ground and phase in ("FINAL", "FLARE_TOUCHDOWN"):
            if abs(ctrl.roll) > 0.65 or abs(ctrl.pitch) > 0.7:
                E(CoachEvent("SMOOTH_HANDS", "nudge", {}, cooldown_s=20))
        return ev
