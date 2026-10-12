"""Instructor demonstration for the airborne exercise lessons.

After the briefing MAVIS says "I have control" and the sim flies the whole
lesson, step after step, while each step is explained. Then the aircraft goes
back to the start and is handed over -- "Your turn. You have control." -- and
the trainee flies every step in one go, with a short cue per step.

DemoPilot is a small script per step (main.py chains one per step): a list of segments, each a control
command for the adapter (`sim.demo_control(cmd)`) and a condition that ends
it. main.py feeds it telemetry and sends the commands; nothing here talks to
the sim directly. Command keys (all optional):
  ap        True = autopilot on (holds `hdg`, and `alt` -- climbing/descending
            to it at `vs` fpm when given); False = autopilot off
  hdg       heading to hold (deg true); "current" = wherever the nose is now
  alt       altitude to hold / capture (ft MSL)
  vs        vertical speed to it (fpm, + up)
  throttle  lever 0..1
  rudder    -1..1 (+ right); 0 = centred
  spd       mock aircraft only: the speed it settles at (the real one gets
            there from the throttle and attitude)
"""
from __future__ import annotations

from .phase_detector import hdg_diff

# Steps that aren't demonstrated: finding the field is the lesson's own test.
NO_DEMO = {"FIND_AIRFIELD"}


class DemoPilot:
    def __init__(self, step: str, eng, tel0: dict, mock: bool = False,
                 base_throttle: float | None = None):
        self.step = step
        self.mock = mock
        self.a0 = float(tel0.get("alt_msl", 0.0))
        self.h0 = float(tel0.get("hdg", 0.0))
        # cruise power the demo returns to: the lesson start's, not wherever
        # the previous step left the lever
        self.th0 = float(base_throttle if base_throttle is not None else tel0.get("throttle", 0.7))
        self.segs = self._script(step, eng)
        self.i = -1
        self.t_seg = 0.0
        self._held = 0.0
        self._turned = 0.0
        self._prev_hdg = None
        self._rudder_sign = 1.0
        self._rudder_checked = False

    @property
    def done(self) -> bool:
        return self.i >= len(self.segs)

    def end_command(self) -> dict:
        """Hand-back: autopilot off, pedals centred, throttle where it began."""
        return {"ap": False, "rudder": 0.0, "throttle": self.th0}

    # ---- the scripts ----
    def _script(self, p: str, eng) -> list[dict]:
        a0, h0, th0 = self.a0, self.h0, self.th0
        level = {"ap": True, "hdg": h0, "alt": a0, "spd": 95, "throttle": th0}
        if p in ("STRAIGHT_LEVEL", "TRIM"):
            return [{"cmd": level, "min_s": 8, "max_s": 8}]
        if p in ("PITCH_UP", "PITCH_DOWN"):
            up = p == "PITCH_UP"
            tgt = a0 + eng.pitch_ft if up else a0 - eng.pitch_ft
            return [{"cmd": {"ap": True, "hdg": h0, "alt": tgt, "vs": 500 if up else -500,
                             "spd": 80 if up else 105},
                     "until": "alt", "tgt": tgt, "max_s": 45},
                    {"cmd": {"ap": True, "hdg": h0, "alt": tgt}, "min_s": 2, "max_s": 2}]
        if p in ("CLIMB", "DESCEND"):
            up = p == "CLIMB"
            tgt = a0 + eng.climb_ft if up else a0 - eng.descend_ft
            return [{"cmd": {"ap": True, "hdg": h0, "alt": tgt, "vs": 700 if up else -500,
                             "throttle": 1.0 if up else max(0.2, th0 - 0.2),
                             "spd": eng.vy if up else 95},
                     "until": "alt", "tgt": tgt, "max_s": 90}]
        if p in ("TURN_LEFT", "TURN_RIGHT"):
            tgt = (h0 + (-1 if p == "TURN_LEFT" else 1) * eng.turn_deg) % 360
            return [{"cmd": {"ap": True, "hdg": tgt, "alt": a0, "spd": 95},
                     "until": "heading", "tgt": tgt, "min_s": 3, "max_s": 60}]
        if p in ("ROLL_LEFT", "ROLL_RIGHT"):
            want = -1 if p == "ROLL_LEFT" else 1
            return [{"cmd": {"ap": True, "hdg": (h0 + want * 45) % 360, "alt": a0, "spd": 95},
                     "until": "bank", "want": want, "bank": eng.bank - 7, "hold_s": 2, "max_s": 15},
                    {"cmd": {"ap": True, "hdg": "current", "alt": a0, "spd": 95},
                     "until": "level", "min_s": 1, "max_s": 15}]
        if p in ("YAW_LEFT", "YAW_RIGHT"):
            want = -1 if p == "YAW_LEFT" else 1
            return [{"cmd": {"ap": False, "rudder": want * 0.5, "hdg": (h0 + want * 20) % 360},
                     "until": "yaw", "want": want, "deg": eng.yaw_deg, "max_s": 8},
                    {"cmd": {"rudder": 0.0}, "min_s": 2, "max_s": 2},
                    {"cmd": {"ap": True, "hdg": "current", "alt": a0, "spd": 95},
                     "until": "level", "min_s": 1, "max_s": 10}]
        if p == "POWER":
            return [{"cmd": {"ap": False, "throttle": min(1.0, th0 + 0.3), "alt": a0 + 150, "spd": 95},
                     "min_s": 7, "max_s": 7},
                    {"cmd": {"throttle": th0, "alt": a0 + 150}, "min_s": 4, "max_s": 4}]
        if p in ("SLOW_CRUISE", "FAST_CRUISE"):
            slow = p == "SLOW_CRUISE"
            spd = eng.speeds[p]
            return [{"cmd": {"ap": True, "hdg": h0, "alt": a0, "spd": spd,
                             "throttle": max(0.2, th0 - 0.25) if slow else 1.0},
                     "until": "speed", "tgt": spd, "min_s": 3, "max_s": 60}]
        return []

    # ---- running ----
    def _resolve(self, cmd: dict, tel: dict) -> dict:
        out = dict(cmd)
        if out.get("hdg") == "current":
            out["hdg"] = tel.get("hdg", self.h0)
        if "rudder" in out:
            out["rudder"] = out["rudder"] * self._rudder_sign
        return out

    def _finished(self, seg: dict, tel: dict, dt: float) -> bool:
        t = self.t_seg
        if t >= seg.get("max_s", 30):
            return True
        if t < seg.get("min_s", 0):
            return False
        kind = seg.get("until")
        if kind is None:
            return True
        if kind == "alt":
            return abs(tel.get("alt_msl", 0.0) - seg["tgt"]) < 40
        if kind == "heading":
            return abs(hdg_diff(seg["tgt"], tel.get("hdg", 0.0))) < 5 and abs(tel.get("bank", 0.0)) < 5
        if kind == "bank":
            if tel.get("bank", 0.0) * seg["want"] >= seg["bank"]:
                self._held += dt
            return self._held >= seg["hold_s"]
        if kind == "level":
            return abs(tel.get("bank", 0.0)) < 5
        if kind == "yaw":
            return self._turned * seg["want"] >= seg["deg"]
        if kind == "speed":
            return abs(tel.get("ias", 0.0) - seg["tgt"]) < 4
        return True

    def tick(self, tel: dict, dt: float) -> dict | None:
        """One step of the demo. Returns a command to send now, or None."""
        if self.done:
            return None
        hdg = tel.get("hdg", 0.0)
        if self._prev_hdg is not None:
            self._turned += hdg_diff(hdg, self._prev_hdg)
        self._prev_hdg = hdg
        if self.i < 0:
            return self._next(tel)
        seg = self.segs[self.i]
        self.t_seg += dt
        # the rudder axis's sign isn't documented the same way everywhere:
        # if the pedal reads the opposite way a second in, flip it once
        if ("rudder" in seg["cmd"] and seg["cmd"]["rudder"] and not self.mock
                and not self._rudder_checked and self.t_seg >= 1.0):
            self._rudder_checked = True
            if tel.get("rudder", 0.0) * seg["cmd"]["rudder"] < -0.05:
                self._rudder_sign = -1.0
                return self._resolve(seg["cmd"], tel)
        if self._finished(seg, tel, dt):
            return self._next(tel)
        return None

    def _next(self, tel: dict) -> dict | None:
        self.i += 1
        self.t_seg = 0.0
        self._held = 0.0
        self._turned = 0.0
        if self.done:
            return None
        cmd = self._resolve(self.segs[self.i]["cmd"], tel)
        if self.i == 0:
            cmd.setdefault("throttle", self.th0)    # each step starts from cruise power
        return cmd
