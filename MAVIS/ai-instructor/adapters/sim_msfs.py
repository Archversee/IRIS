"""MSFS (and Prepar3D) adapter via SimConnect.

Requires: pip install SimConnect   (Python-SimConnect)  -- via `pysimconnect`
(the `simconnect` package, note lowercase) instead, for now: the
`SimConnect` package's Open() call was failing with a generic E_FAIL on a
specific sim PC/Sim Update combination that `pysimconnect` connects to
successfully, so this adapter was ported to it. See git history for the
AircraftRequests/AircraftEvents-based version this replaced.

Writes to the sim: `set_local_time()` sends the CLOCK_*_SET events (ZULU_*
fallback) and confirms against LOCAL TIME; `set_weather()` applies a preset
named in config.yaml sim.weather (a weather theme by default -- EXPERIMENTAL,
MSFS may ignore it); `set_position()` writes
SimConnect's InitPosition struct (the airborne start used by the flight
familiarization lesson, and checkpoint retries), and `set_frozen()` sends the
FREEZE_* toggle events (held still while the instructor explains a step).

Run on the sim PC. Variable/event names may need per-sim-version tweaks --
this is the milestone-1 spike surface. Everything sim-specific lives here.
"""
from __future__ import annotations
import logging
import math

from .sim_base import Telemetry

log = logging.getLogger(__name__)

RAD2DEG = 180.0 / math.pi

# (canonical SimConnect name, unit string) -- pysimconnect's subscribe API
# takes both explicitly (no implicit radians-then-convert the AircraftRequests
# wrapper used to do), so angles are requested pre-converted to degrees here.
_SIMVARS = [
    ("PLANE LATITUDE", "degrees"),
    ("PLANE LONGITUDE", "degrees"),
    ("PLANE ALTITUDE", "feet"),
    ("PLANE ALT ABOVE GROUND", "feet"),
    ("AIRSPEED INDICATED", "knots"),
    ("PLANE HEADING DEGREES TRUE", "degrees"),
    ("PLANE BANK DEGREES", "degrees"),
    ("PLANE PITCH DEGREES", "degrees"),
    ("TRAILING EDGE FLAPS LEFT ANGLE", "degrees"),
    ("SIM ON GROUND", "bool"),
    ("GENERAL ENG RPM:1", "Rpm"),
    ("AILERON POSITION", "Position"),
    ("ELEVATOR POSITION", "Position"),
    ("RUDDER POSITION", "Position"),
    ("GENERAL ENG THROTTLE LEVER POSITION:1", "Percent"),
    ("BRAKE LEFT POSITION", "Position"),
    ("BRAKE RIGHT POSITION", "Position"),
    ("LOCAL TIME", "seconds"),
    ("FLAPS HANDLE PERCENT", "Percent"),
    ("ELEVATOR TRIM PCT", "Percent"),
    ("IS LATITUDE LONGITUDE FREEZE ON", "Bool"),
    ("IS ALTITUDE FREEZE ON", "Bool"),
    ("IS ATTITUDE FREEZE ON", "Bool"),
]

# (state simvar, toggle event) -- set_frozen() reads the state and toggles only
# what differs, so it never depends on how a sim version reads FREEZE_*_SET's arg
_FREEZES = [
    ("IS LATITUDE LONGITUDE FREEZE ON", "FREEZE_LATITUDE_LONGITUDE_TOGGLE"),
    ("IS ALTITUDE FREEZE ON", "FREEZE_ALTITUDE_TOGGLE"),
    ("IS ATTITUDE FREEZE ON", "FREEZE_ATTITUDE_TOGGLE"),
]

# Our own data-definition id for the InitPosition write -- well clear of the
# small sequential ids pysimconnect's DataDefinition hands out (0, 1, 2...).
_INITPOS_DEF_ID = 900


class MSFSAdapter:
    def __init__(self, host: str = "127.0.0.1", port: int = 500, weather: dict | None = None):
        self.host, self.port = host, port
        self._sm = None
        self._datadef = None
        self._initpos_defined = False
        # the tick loop and a teleport both run in executor threads -- one
        # SimConnect call at a time on the shared handle
        import threading
        self._lock = threading.Lock()
        self._weather = weather or {}      # config.yaml sim.weather -- unused for now, see module docstring
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
        if self._sm is not None:
            return  # idempotent -- safe to call again from a retry loop once already connected
        from simconnect import SimConnect, PERIOD_VISUAL_FRAME
        # Always rewritten, regardless of host -- a previous bug here skipped
        # this for host in (127.0.0.1, localhost), which let a bad value
        # written before a config fix get permanently frozen (the DLL kept
        # reading the stale file forever, since nothing ever touched it
        # again once host "looked" local). Every connect() call now
        # guarantees these files match config.yaml's current sim.host/port.
        self._write_simconnect_cfg()
        if self.host not in ("127.0.0.1", "localhost"):
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
        # Built in locals and only kept once telemetry is flowing: a failure
        # half-way (e.g. the subscription raising) used to leave self._sm set
        # with no subscription, so every retry returned "connected" and the
        # first telemetry read crashed MAVIS ("no attribute 'simdata'").
        import time as _time
        sm = SimConnect()
        try:
            subscribe_list = [dict(name=n, units=u) for n, u in _SIMVARS]
            dd = sm.subscribe_simdata(subscribe_list, period=PERIOD_VISUAL_FRAME)
            deadline = _time.time() + 10
            while not dd.simdata and _time.time() < deadline:
                sm.receive()
                _time.sleep(0.05)
            if not dd.simdata:
                err = ConnectionError(
                    "SimConnect connected but no telemetry arrived within 10s -- "
                    "make sure MSFS has an active flight loaded, not just the main menu.")
                err.no_retry = True         # retrying won't load a flight
                raise err
        except Exception:
            try:
                sm.Close()
            except Exception:
                pass
            raise
        self._sm, self._datadef = sm, dd
        log.info("SimConnect connected (pysimconnect) -- flaps handle %s%%, elevator trim %s%%",
                 self._get("FLAPS HANDLE PERCENT", None), self._get("ELEVATOR TRIM PCT", None))

    def _get(self, name: str, default=0.0):
        dd = self._datadef
        v = ((dd.simdata if dd is not None else None) or {}).get(name)
        return default if v is None else v

    def aircraft_ident(self) -> dict | None:
        """Which aircraft is loaded -- used to pick the matching SOP (sop/runtime.py).
        String simvars go in their own data definition so a sim/pysimconnect
        version that can't serve them can never break the telemetry one.
        Returns None if nothing could be read; callers fall back to config."""
        if self._sm is None:
            return None
        import time as _time
        names = ("TITLE", "ATC MODEL", "ATC TYPE")
        for spec in ([dict(name=n) for n in names], [dict(name=n, units="string") for n in names]):
            try:
                from simconnect import PERIOD_VISUAL_FRAME
                dd = self._sm.subscribe_simdata(spec, period=PERIOD_VISUAL_FRAME)
                deadline = _time.time() + 2.0
                while not dd.simdata and _time.time() < deadline:
                    self._sm.receive()
                    _time.sleep(0.05)
                out = {k.lower().replace(" ", "_"): (v.decode(errors="ignore") if isinstance(v, bytes) else str(v))
                       for k, v in (dd.simdata or {}).items() if v not in (None, "", b"")}
                if out:
                    log.info("sim aircraft: %s", out)
                    return out
            except Exception as e:
                log.info("aircraft ident via %s failed: %s", spec[0], e)
        return None

    def _brake(self, v: float) -> float:
        """Toe-brake position normalized to 0..1. SimConnect reports brake
        position either as 0..1 or as a 0..16383 integer depending on version;
        normalize both so the UI threshold is stable."""
        if v > 1.5:
            v = v / 16383.0
        return max(0.0, min(1.0, v))

    def get_telemetry(self) -> Telemetry:
        import time as _time
        if self._sm is None or self._datadef is None:
            # Not connected yet -- connect() is deferred to start() now, but
            # the tick loop polls telemetry continuously from process boot
            # regardless of whether a lesson has started. A default/zeroed
            # Telemetry is a safe placeholder; crashing here would take the
            # whole asyncio.gather()'d process down (confirmed by testing).
            return Telemetry()
        # Drain any buffered messages so self._datadef.simdata reflects the
        # latest frame -- same pump pattern the old LiveAdapter.py used.
        with self._lock:
            while self._sm.receive():
                pass
        g = self._get
        alt = g("PLANE ALTITUDE")
        # vertical speed: computed from altitude ourselves (sim var units are
        # inconsistent across versions and were reading garbage) + smoothing
        now = _time.time()
        if self._prev_alt is not None and now > self._prev_t:
            raw = (alt - self._prev_alt) / (now - self._prev_t) * 60.0  # ft/min
            raw = max(-6000.0, min(6000.0, raw))
            self._vs += 0.25 * (raw - self._vs)
        self._prev_alt, self._prev_t = alt, now
        return Telemetry(
            lat=g("PLANE LATITUDE"),
            lon=g("PLANE LONGITUDE"),
            alt_msl=alt,
            alt_agl=g("PLANE ALT ABOVE GROUND"),
            ias=g("AIRSPEED INDICATED"),
            vs=self._vs,
            hdg=g("PLANE HEADING DEGREES TRUE") % 360,
            bank=-g("PLANE BANK DEGREES"),           # sim: + left -> ours: + right
            pitch=-g("PLANE PITCH DEGREES"),
            flaps_deg=g("TRAILING EDGE FLAPS LEFT ANGLE"),
            on_ground=bool(g("SIM ON GROUND", 1)),
            rpm=g("GENERAL ENG RPM:1"),
            aileron=g("AILERON POSITION"),
            elevator=g("ELEVATOR POSITION"),
            rudder=g("RUDDER POSITION"),
            throttle=g("GENERAL ENG THROTTLE LEVER POSITION:1") / 100.0,
            brake_left=self._brake(g("BRAKE LEFT POSITION", 0.0)),
            brake_right=self._brake(g("BRAKE RIGHT POSITION", 0.0)),
            local_time_h=g("LOCAL TIME", 43200) / 3600.0,
            flaps_handle_pct=g("FLAPS HANDLE PERCENT", None),
            elevator_trim_pct=g("ELEVATOR TRIM PCT", None),
        )

    def _wait_for(self, pred, timeout_s: float) -> bool:
        """Pump SimConnect until pred() holds. Takes the lock per pump, never
        across the wait, so the tick loop's telemetry keeps flowing meanwhile
        (the map/tablet would otherwise stall while the sim confirms)."""
        import time as _time
        deadline = _time.time() + timeout_s
        while _time.time() < deadline:
            with self._lock:
                self._sm.receive()
            if pred():
                return True
            _time.sleep(0.05)
        return pred()

    def _drain(self) -> None:
        with self._lock:
            while self._sm.receive():
                pass

    def set_local_time(self, hour: int, minute: int) -> bool:
        """Set the sim's local time of day (sky, sun, lighting all follow).
        Tries CLOCK_HOURS/MINUTES_SET first, then the ZULU_* events using the
        sim's own local-vs-zulu offset. True only once LOCAL TIME reads back
        within two minutes of what was asked."""
        if self._sm is None:
            return False
        want = (int(hour) * 60 + int(minute)) % 1440

        def landed() -> bool:
            d = abs((self._get("LOCAL TIME", 0) / 60.0) % 1440 - want)
            return min(d, 1440 - d) <= 2

        try:
            self._drain()
            if landed():
                return True
            with self._lock:
                self._sm.send_event("CLOCK_HOURS_SET", int(hour) % 24)
                self._sm.send_event("CLOCK_MINUTES_SET", int(minute) % 60)
            if self._wait_for(landed, 2.0):
                log.info("sim local time -> %02d:%02d", hour, minute)
                return True
            with self._lock:
                zulu = self._sm.get_simdatum("ZULU TIME", "seconds", timeout_seconds=1)
            if zulu is None:
                log.warning("time change: sim didn't report ZULU TIME, can't use the zulu fallback")
                return False
            offset = round((self._get("LOCAL TIME", 0) - zulu) / 60.0)
            z = (want - offset) % 1440
            with self._lock:
                self._sm.send_event("ZULU_HOURS_SET", z // 60)
                self._sm.send_event("ZULU_MINUTES_SET", z % 60)
            if self._wait_for(landed, 2.0):
                log.info("sim local time -> %02d:%02d (via zulu %02d:%02d)",
                         hour, minute, z // 60, z % 60)
                return True
        except Exception as e:
            log.error("SimConnect set_local_time failed: %s", e)
            return False
        log.warning("sim didn't take the time change to %02d:%02d", hour, minute)
        return False

    def set_weather(self, condition: str) -> bool:
        """Apply a weather preset from config.yaml sim.weather. EXPERIMENTAL:
        method "theme" calls SimConnect_WeatherSetModeTheme (the sim may accept
        the call yet not change the sky -- there's no simvar to confirm it);
        "event"/"simvar" fire a key event / set a var you've confirmed at the
        sim. True means the request was sent without error. Never raises."""
        method = self._weather.get("method", "none")
        entry = (self._weather.get("presets") or {}).get((condition or "").lower())
        if self._sm is None or method == "none" or not entry:
            return False
        try:
            with self._lock:
                if method == "theme" and entry.get("theme"):
                    self._sm.WeatherSetModeTheme(entry["theme"])
                elif method == "event" and entry.get("event"):
                    self._sm.send_event(entry["event"], int(entry.get("value", 0)))
                elif method == "simvar" and entry.get("var"):
                    self._sm.set_simdatum(entry["var"], entry.get("value", 0))
                else:
                    return False
        except Exception as e:
            log.warning("set_weather(%s) via %s failed: %s", condition, method, e)
            return False
        log.info("set_weather(%s) requested via %s", condition, method)
        return True

    def set_position(self, lat: float, lon: float, alt_msl: float, hdg: float,
                     ias: float, throttle_pct: float | None = None,
                     on_ground: bool = False) -> bool:
        """Place the aircraft at lat/lon/alt (ft MSL), true heading, already
        flying at `ias` knots, wings level -- SimConnect's InitPosition with
        OnGround=0, the same mechanism MSFS's own airborne starts use
        (on_ground=True instead parks it on the surface, for runway checkpoints).
        Optionally sets throttle too (a hardware throttle overrides it as soon
        as it's moved). Returns False if the sim refused or isn't connected."""
        if self._sm is None:
            return False
        from ctypes import byref, c_void_p, cast, sizeof
        from simconnect.scdefs import (DATA_INITPOSITION, DATATYPE_INITPOSITION,
                                       OBJECT_ID_USER, UNUSED)
        try:
            with self._lock:
                if not self._initpos_defined:
                    self._sm.AddToDataDefinition(_INITPOS_DEF_ID, "Initial Position", "",
                                                 DATATYPE_INITPOSITION, 0.0, UNUSED)
                    self._initpos_defined = True
                ip = DATA_INITPOSITION(Latitude=lat, Longitude=lon, Altitude=alt_msl,
                                       Pitch=0.0, Bank=0.0, Heading=hdg % 360.0,
                                       OnGround=1 if on_ground else 0,
                                       Airspeed=0 if on_ground else int(round(ias)))
                self._sm.SetDataOnSimObject(_INITPOS_DEF_ID, OBJECT_ID_USER, 0, 0,
                                            sizeof(ip), cast(byref(ip), c_void_p))
                if throttle_pct is not None:
                    self._sm.set_simdatum("GENERAL ENG THROTTLE LEVER POSITION:1",
                                          float(throttle_pct), "percent")
        except Exception as e:
            log.error("SimConnect set_position failed: %s", e)
            return False
        self._prev_alt = None        # don't turn the jump into a vertical-speed spike
        log.info("aircraft placed at %.4f,%.4f %.0f ft hdg %.0f %.0f kt",
                 lat, lon, alt_msl, hdg, ias)
        return True

    def demo_control(self, cmd: dict) -> bool:
        """Fly an instructor demonstration (core/demo.py command): the
        aircraft's own autopilot for heading / altitude / vertical speed, the
        throttle lever, and the rudder axis. Only the keys present change.
        A hardware control the trainee moves overrides it, so it's hands off
        while MAVIS has control. Returns False if any write failed."""
        if self._sm is None:
            return False

        def ev(name: str, value: int = 0) -> None:
            self._sm.send_event(name, int(value) & 0xFFFFFFFF)   # negatives as DWORD

        try:
            with self._lock:
                if "throttle" in cmd:
                    self._sm.set_simdatum("GENERAL ENG THROTTLE LEVER POSITION:1",
                                          max(0.0, min(100.0, float(cmd["throttle"]) * 100.0)),
                                          "percent")
                if "rudder" in cmd:
                    ev("AXIS_RUDDER_SET", round(max(-1.0, min(1.0, float(cmd["rudder"]))) * 16383))
                if cmd.get("ap") is False:
                    ev("AUTOPILOT_OFF")
                elif cmd.get("ap") is True:
                    ev("AUTOPILOT_ON")
                    if cmd.get("hdg") is not None:
                        ev("HEADING_BUG_SET", round(float(cmd["hdg"])) % 360)
                        ev("AP_HDG_HOLD_ON")
                    if cmd.get("alt") is not None:
                        ev("AP_ALT_VAR_SET_ENGLISH", round(float(cmd["alt"])))
                        if cmd.get("vs"):
                            ev("AP_VS_VAR_SET_ENGLISH", round(float(cmd["vs"])))
                            ev("AP_VS_ON")
                        else:
                            ev("AP_ALT_HOLD_ON")
        except Exception as e:
            log.error("SimConnect demo_control(%s) failed: %s", cmd, e)
            return False
        return True

    def set_frozen(self, on: bool) -> bool:
        """Hold the aircraft still (position, altitude and attitude) while the
        instructor explains, like MSFS's own lessons; on=False hands it back.
        Returns True once the sim reports the requested state."""
        if self._sm is None:
            return False

        def settled() -> bool:
            return all(bool(self._get(var, 0)) == on for var, _ in _FREEZES)

        try:
            self._drain()
            with self._lock:
                for var, event in _FREEZES:
                    if bool(self._get(var, 0)) != on:
                        self._sm.send_event(event)
            if self._wait_for(settled, 1.5):
                return True
        except Exception as e:
            log.error("SimConnect set_frozen(%s) failed: %s", on, e)
            return False
        log.warning("sim didn't confirm freeze=%s within 1.5 s", on)
        return False

    def close(self) -> None:
        if self._sm:
            try:
                self._sm.Close()
            except Exception:
                pass
