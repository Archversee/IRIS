"""MSFS (and Prepar3D) adapter via SimConnect.

Requires: pip install SimConnect   (Python-SimConnect)  -- via `pysimconnect`
(the `simconnect` package, note lowercase) instead, for now: the
`SimConnect` package's Open() call was failing with a generic E_FAIL on a
specific sim PC/Sim Update combination that `pysimconnect` connects to
successfully, so this adapter was ported to it. See git history for the
AircraftRequests/AircraftEvents-based version this replaced.

Event-SENDING (day/night time-of-day switch, experimental teleport, weather)
is not wired up for `pysimconnect` yet -- `set_local_time()` always returns
False (its documented "unsupported" contract, so callers already fall back
gracefully), and `set_position()`/`set_weather()` are omitted entirely
(both are called via hasattr/getattr elsewhere in MAVIS, so their absence
degrades gracefully too, by the same design). Only telemetry READING is
ported. Restoring event-sending on `pysimconnect` -- or switching back once
`SimConnect` is fixed -- is follow-up work, not done here.

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
]


class MSFSAdapter:
    def __init__(self, host: str = "127.0.0.1", port: int = 500, weather: dict | None = None):
        self.host, self.port = host, port
        self._sm = None
        self._datadef = None
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
        if self._sm is None:
            # Not connected yet -- connect() is deferred to start() now, but
            # the tick loop polls telemetry continuously from process boot
            # regardless of whether a lesson has started. A default/zeroed
            # Telemetry is a safe placeholder; crashing here would take the
            # whole asyncio.gather()'d process down (confirmed by testing).
            return Telemetry()
        # Drain any buffered messages so self._datadef.simdata reflects the
        # latest frame -- same pump pattern the old LiveAdapter.py used.
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

    def close(self) -> None:
        if self._sm:
            try:
                self._sm.Close()
            except Exception:
                pass
