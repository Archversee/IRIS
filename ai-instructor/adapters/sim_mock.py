"""Mock sim: a kinematic C172 that flies Lesson 1's planned circuit.

Follows the same corner coordinates as the grey dotted map line:
  threshold → upwind end → crosswind → downwind → base → final → threshold.
"""
from __future__ import annotations
import math
import time

from .sim_base import Telemetry

KT2MS = 0.51444
FT2M = 0.3048


def wrap(d: float) -> float:
    return d % 360.0


def turn_delta(cur: float, tgt: float) -> float:
    return (tgt - cur + 180) % 360 - 180


class MockAdapter:
    def __init__(self, lesson: dict, aircraft: dict | None = None, **_):
        rw = lesson["runway"]
        pat = lesson["pattern"]
        vs = (aircraft or {}).get("v_speeds") or {}
        self.vr = float(vs.get("vr", 55))
        self.vy = float(vs.get("vy", 74))
        self.rotate_speech_lag_s = 4.0
        self.lat0 = rw["threshold"]["lat"]
        self.lon0 = rw["threshold"]["lon"]
        self.elev = lesson["airport"]["elevation_ft"]
        self.hdg_rw = rw["heading_true"]
        self._side = 1.0 if pat.get("side", "left") == "left" else -1.0
        off = pat["downwind_offset_nm"] * 1852.0
        self._pattern_off = off
        self.pattern_msl = float(pat["altitude_msl_ft"])
        self.pattern_agl_m = (self.pattern_msl - self.elev) * FT2M
        self.xwind_agl_m = float(pat["crosswind_turn_agl_ft"]) * FT2M
        fin = float(pat.get("mock_base_turn_past_abeam_m", 1000.0))
        self._fin = fin
        self._runway_length_m = float(rw["length_m"])
        self._up_a = self._runway_length_m + 420.0

        self._u = (math.sin(math.radians(self.hdg_rw)), math.cos(math.radians(self.hdg_rw)))
        self._lv = (math.sin(math.radians(self.hdg_rw - 90)), math.cos(math.radians(self.hdg_rw - 90)))

        # Planned circuit corners (along, left), identical to drawMap polygon
        self._corners = [
            (0.0, 0.0),
            (self._up_a, 0.0),
            (self._up_a, off),
            (-fin, off),
            (-fin, 0.0),
            (40.0, 0.0),
        ]
        self._leg_spd = [self.vy, self.vy, 85.0, 78.0, 66.0]
        self._leg_alt = [
            self.xwind_agl_m,
            self.pattern_agl_m,
            self.pattern_agl_m,
            self.pattern_agl_m * 0.55,
            self.pattern_agl_m * 0.16,
        ]
        self._leg_flaps = [0.0, 0.0, 0.0, 30.0, 30.0]
        self.stop_after = int(lesson.get("total_circuits", 1))
        self.reset()

    def _pt(self, along: float, left: float) -> tuple[float, float]:
        e = self._u[0] * along + self._lv[0] * left
        n = self._u[1] * along + self._lv[1] * left
        return e, n

    def _along_left(self) -> tuple[float, float]:
        along = self.e * self._u[0] + self.n * self._u[1]
        left = self.e * self._lv[0] + self.n * self._lv[1]
        return along, left

    def _steer_to_corner(self, seg: int) -> float:
        """True heading that points at the next planned corner."""
        bx, by = self._corners[seg + 1]
        e_tgt, n_tgt = self._pt(bx, by)
        return wrap(math.degrees(math.atan2(e_tgt - self.e, n_tgt - self.n)))

    def _at_planned_corner(self, seg: int) -> bool:
        """True when the aircraft has reached the grey-line corner for this leg."""
        a, l = self._along_left()
        ca, cl = self._corners[seg + 1]
        tol = 130.0
        if seg == 0:
            return a >= ca - tol and abs(l - cl) < tol
        if seg == 1:
            return l >= cl - tol and abs(a - ca) < tol
        if seg == 2:
            return a <= ca + tol and abs(l - cl) < tol
        if seg == 3:
            return l <= cl + tol and abs(a - ca) < tol
        return False

    def _advance_segment(self):
        if self._seg >= 4:
            return
        bx, by = self._corners[self._seg + 1]
        px, py = self._along_left()
        # Pull slightly toward the planned corner so the next leg starts on the grey line.
        na = px + (bx - px) * 0.4
        nl = py + (by - py) * 0.4
        self.e, self.n = self._pt(na, nl)
        self._seg += 1

    # -- SimAdapter interface --
    def connect(self):  # noqa: D401
        pass

    def reset(self):
        self.e, self.n = 0.0, 0.0
        self.alt = 0.0
        self.spd = 0.0
        self.hdg = self.hdg_rw
        self.bank = 0.0
        self.flaps = 0.0
        self._seg = 0
        self._airborne = False
        self.local_h = 10.0
        self.circuit = 1
        self.stopping = False
        self.released = False
        self._rotate_at = None
        self._t = time.time()

    def release(self):
        self.released = True
        self._t = time.time()

    def set_local_time(self, hour: int, minute: int) -> bool:
        self.local_h = hour + minute / 60.0
        return True

    _SNAP = ("e", "n", "alt", "spd", "hdg", "bank", "flaps", "_seg", "_airborne",
             "circuit", "stopping", "released", "_rotate_at")

    def save_state(self) -> dict:
        return {k: getattr(self, k) for k in self._SNAP}

    def restore_state(self, s: dict):
        for k in self._SNAP:
            if k in s:
                setattr(self, k, s[k])
        self._t = time.time()

    def comply(self, code: str, data: dict):
        if code in ("ROTATE_NOW", "ROTATE_LATE"):
            if not self._airborne:
                lag = self.rotate_speech_lag_s if code == "ROTATE_NOW" else 0.4
                self._rotate_at = time.time() + lag
            return
        if code in ("SPEED_FAST", "SPEED_SLOW") and data.get("tgt"):
            self._leg_spd[self._seg] = float(data["tgt"])
        elif code in ("ALT_HIGH", "ALT_LOW"):
            self._leg_alt[self._seg] = self.pattern_agl_m
        elif code in ("ABEAM_FLAPS", "FLAPS_REMINDER") and data.get("flaps"):
            self._leg_flaps[2] = float(data["flaps"])

    def close(self):
        pass

    def _fly_heading(self, tgt_hdg: float, tgt_spd: float, tgt_alt: float,
                     tgt_flaps: float, dt: float):
        d = turn_delta(self.hdg, tgt_hdg)
        rate = max(-3.0, min(3.0, d))
        self.hdg = wrap(self.hdg + rate * dt * 3.0)
        self.bank += (rate * 7.0 - self.bank) * min(1.0, 2.0 * dt)
        self.spd += max(-4.0, min(2.5, tgt_spd - self.spd)) * dt
        need = tgt_alt - self.alt
        climb = max(-4.3, min(6.5, need * 0.06))
        self.alt = max(0.0, self.alt + climb * dt)
        self.flaps += max(-5.0, min(5.0, tgt_flaps - self.flaps)) * dt
        v = self.spd * KT2MS
        self.e += v * dt * math.sin(math.radians(self.hdg))
        self.n += v * dt * math.cos(math.radians(self.hdg))
        self._vs = climb / FT2M * 60.0

    def _step(self, dt: float):
        if not self.released:
            return
        if self.stopping:
            self.alt = 0.0
            self.spd = max(0.0, self.spd - 6 * dt)
            d = self.spd * KT2MS * dt
            self.e += d * math.sin(math.radians(self.hdg))
            self.n += d * math.cos(math.radians(self.hdg))
            return

        if not self._airborne:
            if self.spd < self.vr:
                self.spd = min(self.vr, self.spd + 8 * dt)
            else:
                self.spd = self.vr
            d = self.spd * KT2MS * dt
            self.e += d * math.sin(math.radians(self.hdg))
            self.n += d * math.cos(math.radians(self.hdg))
            now = time.time()
            if self._rotate_at is not None and now >= self._rotate_at:
                self._airborne = True
                self._seg = 0
                self._rotate_at = None
            elif self.spd >= self.vr and self._rotate_at is None:
                self._rotate_at = now + 4.0
            return

        # Upwind climb profile on first segment
        if self._seg == 0:
            along, _ = self._along_left()
            if along < self._runway_length_m:
                alt_cap = self.xwind_agl_m * 0.55
            else:
                prog = min(1.0, (along - self._runway_length_m) / 350.0)
                alt_cap = self.xwind_agl_m * (0.55 + 0.45 * prog)
            self._leg_alt[0] = min(self.xwind_agl_m, alt_cap)

        # Advance to next leg when the grey-line corner for this segment is reached.
        if self._seg < 4 and self._at_planned_corner(self._seg):
            self._advance_segment()

        if self._seg >= 4:
            along, left = self._along_left()
            if -30.0 <= along <= 60.0 and abs(left) < 80.0:
                self.stopping = True
                return

        tgt_hdg = self._steer_to_corner(self._seg)
        tgt_spd = self._leg_spd[self._seg]
        tgt_alt = self._leg_alt[self._seg]
        tgt_flaps = self._leg_flaps[self._seg]
        self._fly_heading(tgt_hdg, tgt_spd, tgt_alt, tgt_flaps, dt)

        if self._seg == 4 and self.alt < 0.5 and self.spd < 35:
            self.stopping = True

    def get_telemetry(self) -> Telemetry:
        now = time.time()
        dt, self._t = min(now - self._t, 0.5), now
        self._vs = 0.0
        self._step(dt)
        lat = self.lat0 + self.n / 111320.0
        lon = self.lon0 + self.e / (111320.0 * math.cos(math.radians(self.lat0)))
        on_gnd = self.alt < 0.5
        idle = (not self.released) or (self.stopping and self.spd < 30)
        climbing = not on_gnd and self._vs > 80
        tgt_spd = 60 if not self._airborne else self._leg_spd[min(self._seg, 4)]
        return Telemetry(
            lat=lat, lon=lon,
            alt_msl=self.alt / FT2M + self.elev,
            alt_agl=self.alt / FT2M,
            ias=self.spd, vs=getattr(self, "_vs", 0.0),
            hdg=self.hdg, bank=self.bank,
            pitch=5.0 if climbing else (2.0 if not on_gnd else 0.0),
            flaps_deg=self.flaps, on_ground=on_gnd,
            rpm=(800 if idle else (2400 if tgt_spd > 75 or on_gnd else 1600)),
            aileron=max(-1, min(1, self.bank / 30.0)),
            elevator=-0.15 if climbing else 0.05,
            rudder=0.0,
            throttle=(0.05 if idle else
                      (1.0 if on_gnd else (0.75 if self._vs > 0 else 0.35))),
            local_time_h=self.local_h,
        )
