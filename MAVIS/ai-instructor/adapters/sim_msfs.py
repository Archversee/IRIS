"""MSFS (and Prepar3D) adapter via SimConnect.

Requires: pip install SimConnect   (Python-SimConnect)  -- via `pysimconnect`
(the `simconnect` package, note lowercase) instead, for now: the
`SimConnect` package's Open() call was failing with a generic E_FAIL on a
specific sim PC/Sim Update combination that `pysimconnect` connects to
successfully, so this adapter was ported to it. See git history for the
AircraftRequests/AircraftEvents-based version this replaced.

Event-SENDING (day/night time-of-day switch, weather) is not wired up for
`pysimconnect` yet -- `set_local_time()` always returns False (its documented
"unsupported" contract, so callers already fall back gracefully), and
`set_weather()` is omitted entirely (called via getattr elsewhere in MAVIS, so
its absence degrades gracefully too). `set_position()` IS ported: it writes
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
        self._sm = SimConnect()
        subscribe_list = [dict(name=n, units=u) for n, u in _SIMVARS]
        self._datadef = self._sm.subscribe_simdata(subscribe_list, period=PERIOD_VISUAL_FRAME)

        import time as _time
        deadline = _time.time() + 10
        while not self._datadef.simdata and _time.time() < deadline:
            self._sm.receive()
            _time.sleep(0.05)
        if not self._datadef.simdata:
            raise ConnectionError(
                "SimConnect connected but no telemetry arrived within 10s -- "
                "make sure MSFS has an active flight loaded, not just the main menu.")
        log.info("SimConnect connected (pysimconnect) -- flaps handle %s%%, elevator trim %s%%",
                 self._get("FLAPS HANDLE PERCENT", None), self._get("ELEVATOR TRIM PCT", None))

    def _get(self, name: str, default=0.0):
        v = (self._datadef.simdata or {}).get(name)
        return default if v is None else v

    def _brake(self, v: float) -> float:
        """Toe-brake position normalized to 0..1. SimConnect reports brake
        position either as 0..1 or as a 0..16383 integer depending on version;
        normalize both so the UI threshold is stable."""
        if v > 1.5:
            v = v / 16383.0
        return max(0.0, min(1.0, v))

    def get_telemetry(self) -> Telemetry:
        import time as _time
        if self._sm is None:
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

    def set_local_time(self, hour: int, minute: int) -> bool:
        """Event-sending isn't ported to pysimconnect yet (see module
        docstring) -- always False, which is this method's documented
        "unsupported" contract, so callers (night-lesson switch, the ground
        clock chat command) already fall back gracefully rather than error."""
        return False

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

    def set_frozen(self, on: bool) -> bool:
        """Hold the aircraft still (position, altitude and attitude) while the
        instructor explains, like MSFS's own lessons; on=False hands it back.
        Returns True once the sim reports the requested state."""
        import time as _time
        if self._sm is None:
            return False
        try:
            with self._lock:
                while self._sm.receive():
                    pass
                for var, event in _FREEZES:
                    if bool(self._get(var, 0)) != on:
                        self._sm.send_event(event)
                deadline = _time.time() + 1.5
                while _time.time() < deadline:
                    self._sm.receive()
                    if all(bool(self._get(var, 0)) == on for var, _ in _FREEZES):
                        return True
                    _time.sleep(0.05)
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
