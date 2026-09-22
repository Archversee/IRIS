"""MSFS (and Prepar3D) adapter via SimConnect.

Requires: pip install SimConnect   (Python-SimConnect)
Run on the sim PC. Variable/event names may need per-sim-version tweaks --
this is the milestone-1 spike surface. Everything sim-specific lives here.
"""
from __future__ import annotations
import logging
import math

from .sim_base import Telemetry

log = logging.getLogger(__name__)

RAD2DEG = 180.0 / math.pi


class MSFSAdapter:
    def __init__(self, host: str = "127.0.0.1", port: int = 500, weather: dict | None = None):
        self.host, self.port = host, port
        self._sm = None
        self._req = None
        self._events = {}
        self._weather = weather or {}      # config.yaml sim.weather
        self._weather_events = {}          # cache of resolved weather key events
        self._prev_alt = None   # for self-computed, smoothed vertical speed
        self._prev_t = 0.0
        self._vs = 0.0

    def _write_simconnect_cfg(self) -> None:
        """For remote MSFS (app on laptop, sim on another PC).
        The SimConnect DLL does NOT read the working directory - it looks for
        SimConnect.cfg next to the running executable (python.exe!) and in the
        user's Documents folder. Write to every candidate location."""
        import sys
        from pathlib import Path
        cfg = (f"[SimConnect]\nProtocol=IPv4\nAddress={self.host}\n"
               f"Port={self.port}\nMaxReceiveSize=41088\n")
        targets = [Path(sys.executable).parent / "SimConnect.cfg",   # venv Scripts dir
                   Path.home() / "Documents" / "SimConnect.cfg",
                   Path("SimConnect.cfg")]
        for p in targets:
            try:
                p.write_text(cfg)
                log.info("wrote %s -> %s:%s", p, self.host, self.port)
            except Exception as e:
                log.warning("could not write %s: %s", p, e)

    def connect(self) -> None:
        from SimConnect import SimConnect, AircraftRequests, AircraftEvents
        if self.host not in ("127.0.0.1", "localhost"):
            self._write_simconnect_cfg()
            # fail fast with a useful message if the sim PC isn't reachable
            import socket
            try:
                with socket.create_connection((self.host, self.port), timeout=4):
                    log.info("sim PC %s:%s is reachable", self.host, self.port)
            except OSError as e:
                raise ConnectionError(
                    f"Cannot reach {self.host}:{self.port} - MSFS's network SimConnect "
                    "isn't answering. On the sim PC check: (1) SimConnect.xml has the "
                    "global IPv4/port 500 section, (2) MSFS was RESTARTED after editing "
                    "it, (3) firewall allows TCP 500. Verify with: "
                    f"Test-NetConnection {self.host} -Port {self.port}") from e
        self._sm = SimConnect()
        self._req = AircraftRequests(self._sm, _time=50)  # ms cache
        ae = AircraftEvents(self._sm)
        for name in ("ZULU_HOURS_SET", "ZULU_MINUTES_SET",
                     "CLOCK_HOURS_SET", "CLOCK_MINUTES_SET"):
            try:
                self._events[name] = ae.find(name)
            except Exception:
                log.warning("event %s unavailable", name)
        log.info("SimConnect connected")

    def _get(self, name: str, default=0.0):
        try:
            v = self._req.get(name)
            return default if v is None else v
        except Exception:
            return default

    def _brake(self, name: str) -> float:
        """Toe-brake position normalized to 0..1. SimConnect reports brake
        position either as 0..1 or as a 0..16383 integer depending on version;
        normalize both so the UI threshold is stable."""
        v = self._get(name, 0.0)
        if v > 1.5:
            v = v / 16383.0
        return max(0.0, min(1.0, v))

    def get_telemetry(self) -> Telemetry:
        import time as _time
        g = self._get
        alt = g("PLANE_ALTITUDE")
        # vertical speed: computed from altitude ourselves (sim var units are
        # inconsistent across versions and were reading garbage) + smoothing
        now = _time.time()
        if self._prev_alt is not None and now > self._prev_t:
            raw = (alt - self._prev_alt) / (now - self._prev_t) * 60.0  # ft/min
            raw = max(-6000.0, min(6000.0, raw))
            self._vs += 0.25 * (raw - self._vs)
        self._prev_alt, self._prev_t = alt, now
        return Telemetry(
            lat=g("PLANE_LATITUDE"),
            lon=g("PLANE_LONGITUDE"),
            alt_msl=alt,
            alt_agl=g("PLANE_ALT_ABOVE_GROUND"),
            ias=g("AIRSPEED_INDICATED"),
            vs=self._vs,
            hdg=(g("PLANE_HEADING_DEGREES_TRUE") * RAD2DEG) % 360,
            bank=-g("PLANE_BANK_DEGREES") * RAD2DEG,  # sim: + left -> ours: + right
            pitch=-g("PLANE_PITCH_DEGREES") * RAD2DEG,
            flaps_deg=g("TRAILING_EDGE_FLAPS_LEFT_ANGLE") * RAD2DEG,
            on_ground=bool(g("SIM_ON_GROUND", 1)),
            rpm=g("GENERAL_ENG_RPM:1"),
            aileron=g("AILERON_POSITION"),
            elevator=g("ELEVATOR_POSITION"),
            rudder=g("RUDDER_POSITION"),
            throttle=g("GENERAL_ENG_THROTTLE_LEVER_POSITION:1") / 100.0,
            brake_left=self._brake("BRAKE_LEFT_POSITION"),
            brake_right=self._brake("BRAKE_RIGHT_POSITION"),
            local_time_h=g("LOCAL_TIME", 43200) / 3600.0,
        )

    def set_local_time(self, hour: int, minute: int) -> bool:
        """Day/night switch. Uses zulu-set events; returns False if unsupported
        so the caller can fall back (e.g. instruct a pre-bound keybind)."""
        # WSAP is UTC+8: local 20:30 -> zulu 12:30
        zh, zm = (hour - 8) % 24, minute
        ok = False
        for name, val in (("ZULU_HOURS_SET", zh), ("ZULU_MINUTES_SET", zm)):
            ev = self._events.get(name)
            if ev:
                try:
                    ev(val)
                    ok = True
                except Exception as e:
                    log.error("time set %s failed: %s", name, e)
        return ok

    def set_weather(self, condition: str) -> bool:
        """Set weather to a named preset. EXPERIMENTAL and version-dependent.

        MSFS 2020 CANNOT set weather via SimConnect (deprecated weather API,
        read-only ambient vars) -> leave config sim.weather.method=none and this
        returns False, so the caller degrades gracefully.

        MSFS 2024's SDK is more capable, but the exact programmatic trigger for a
        weather preset is not a settled/documented one-liner yet - so rather than
        hardcode a call that may not exist, this fires whatever event or settable
        var you CONFIRM works at your sim, mapped in config.yaml (sim.weather):
          method: event  -> AircraftEvents.find(<event>)(<value>)
          method: simvar -> AircraftRequests.set(<var>, <value>)
        Always returns a bool and never raises, so testing is safe.
        """
        method = self._weather.get("method", "none")
        entry = (self._weather.get("presets") or {}).get((condition or "").lower())
        if method == "none" or not entry:
            return False
        try:
            if method == "event":
                name = entry.get("event")
                if not name:
                    return False
                ev = self._weather_events.get(name)
                if ev is None:
                    from SimConnect import AircraftEvents
                    ev = AircraftEvents(self._sm).find(name)
                    self._weather_events[name] = ev
                ev(int(entry.get("value", 0)))
                log.info("set_weather(%s) via event %s", condition, name)
                return True
            if method == "simvar":
                var = entry.get("var")
                if not var:
                    return False
                self._req.set(var, entry.get("value", 0))
                log.info("set_weather(%s) via simvar %s", condition, var)
                return True
        except Exception as e:
            log.warning("set_weather(%s) via %s failed: %s", condition, method, e)
        return False

    def set_position(self, lat: float, lon: float, alt_ft: float,
                     hdg_deg: float, ias_kt: float = 70.0) -> bool:
        """Teleport for 'try that again' checkpoints. Best-effort via settable
        sim vars - EXPERIMENTAL, verify at the sim (milestone-2 item)."""
        ok = True
        for name, val in (("PLANE_LATITUDE", lat),
                          ("PLANE_LONGITUDE", lon),
                          ("PLANE_ALTITUDE", alt_ft),
                          ("PLANE_HEADING_DEGREES_TRUE", math.radians(hdg_deg)),
                          ("VELOCITY_BODY_Z", ias_kt * 1.68781)):  # kt -> ft/s
            try:
                self._req.set(name, val)
            except Exception as e:
                log.warning("teleport: set %s failed: %s", name, e)
                ok = False
        return ok

    def close(self) -> None:
        if self._sm:
            self._sm.exit()
