"""Replay a logged SimConnect flight as if it were live telemetry.

Plays a CSV of raw SimConnect samples (the export format of the trainee
logger: one row per sample, any rate, ISO `timestamp_utc` or epoch `ts`) back
in wall-clock time through the same Telemetry model the MSFS adapter fills, so
the lesson engine, coach, map and gauges all see a real flight instead of the
mock aircraft. Lives beside the mock and MSFS adapters and is selected with
`sim.adapter: replay` / `--sim replay`; `sim.replay.file` names the CSV.

Playback is parked on the first sample until release() (the briefing plays
first, as with the mock), then runs at 1x and holds the final sample once the
log ends. Checkpoint restore rewinds the log to the saved moment.
"""
from __future__ import annotations
import csv
import logging
import math
import time
from datetime import datetime
from pathlib import Path

from adapters.sim_base import Telemetry

log = logging.getLogger(__name__)

GROUND_AGL_FT = 12.0      # below this the aircraft is treated as on the ground
FLAPS_FULL_DEG = 30.0     # the logger stores the flap handle as 0..100 %
VS_LIMIT_FPM = 6000.0


def row_time(row: dict) -> float:
    if row.get("timestamp_utc"):
        return datetime.fromisoformat(row["timestamp_utc"]).timestamp()
    return float(row["ts"])


def load_log(path: Path, session: str | None = None) -> list[dict]:
    with Path(path).open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if session:
        rows = [r for r in rows if r.get("session_id") == session]
    rows.sort(key=row_time)
    if not rows:
        raise SystemExit(f"replay log has no samples: {path}")
    return rows


def reciprocal_runway(lesson: dict) -> dict:
    """The same lesson flown from the other end of the runway: threshold moved
    to the far end, heading reversed, pattern side unchanged."""
    rw = lesson["runway"]
    h = math.radians(rw["heading_true"])
    lat0, lon0, L = rw["threshold"]["lat"], rw["threshold"]["lon"], rw["length_m"]
    ident = rw.get("ident", "")
    try:
        ident = f"{(int(ident) + 18 - 1) % 36 + 1:02d}"
    except ValueError:
        pass
    return {**lesson, "runway": {
        **rw, "ident": ident,
        "heading_true": (rw["heading_true"] + 180.0) % 360.0,
        "threshold": {"lat": lat0 + L * math.cos(h) / 111320.0,
                      "lon": lon0 + L * math.sin(h) / (111320.0 * math.cos(math.radians(lat0)))},
    }}


def flown_from_reciprocal(rows: list[dict], lesson: dict) -> bool:
    """True when the log's initial heading is closer to the opposite runway."""
    hdg = float(rows[0]["heading_true_deg"])
    diff = abs((hdg - lesson["runway"]["heading_true"] + 180.0) % 360.0 - 180.0)
    return diff > 90.0


def to_telemetry(row: dict, elev_ft: float, vs: float, local_h: float) -> Telemetry:
    """One log row as live telemetry, with the MSFS adapter's conventions:
    bank/pitch signs flipped, flaps as degrees, throttle 0..1."""
    alt = float(row["altitude_ft"])
    agl = alt - elev_ft
    bank = -float(row["bank_deg"])
    aileron = row.get("aileron_position")
    if aileron in (None, ""):
        # the logger has no aileron channel: show the roll input the bank implies
        aileron = max(-1.0, min(1.0, bank / 30.0))
    return Telemetry(
        t=row_time(row), lat=float(row["latitude"]), lon=float(row["longitude"]),
        alt_msl=alt, alt_agl=agl, ias=float(row["airspeed_kt"]), vs=vs,
        hdg=float(row["heading_true_deg"]) % 360.0,
        bank=bank, pitch=-float(row["pitch_deg"]),
        flaps_deg=float(row.get("flaps_handle_pct") or 0) / 100.0 * FLAPS_FULL_DEG,
        on_ground=agl < GROUND_AGL_FT,
        aileron=float(aileron),
        elevator=float(row.get("elevator_position") or 0),
        rudder=float(row.get("rudder_position") or 0),
        throttle=float(row.get("throttle_pct") or 0) / 100.0,
        local_time_h=local_h,
        flaps_handle_pct=float(row["flaps_handle_pct"]) if row.get("flaps_handle_pct") not in (None, "") else None,
        elevator_trim_pct=float(row["elevator_trim_pct"]) if row.get("elevator_trim_pct") not in (None, "") else None,
        rudder_trim_pct=float(row["rudder_trim_pct"]) if row.get("rudder_trim_pct") not in (None, "") else None,
        aileron_trim_pct=float(row["aileron_trim_pct"]) if row.get("aileron_trim_pct") not in (None, "") else None,
    )


class ReplayAdapter:
    def __init__(self, path: str | Path, elevation_ft: float, session: str | None = None):
        self.path = Path(path)
        self.elev = float(elevation_ft)
        self.session = session
        self.rows: list[dict] = []
        self._t_log0 = 0.0
        self.reset()

    # ---- SimAdapter ----
    def connect(self) -> None:
        self.rows = load_log(self.path, self.session)
        self._t_log0 = row_time(self.rows[0])
        log.info("replay: %s, %d samples, %.0f s", self.path.name, len(self.rows),
                 row_time(self.rows[-1]) - self._t_log0)

    def reset(self) -> None:
        self._idx = 0
        self._t0: float | None = None
        self._vs = 0.0
        self._prev: tuple[float, float] | None = None   # (log time, altitude)
        self.local_h = 10.0

    def release(self) -> None:
        self._t0 = time.time()

    @property
    def first_heading(self) -> float:
        return float(self.rows[0]["heading_true_deg"])

    def _current_row(self) -> dict:
        if self._t0 is None:
            return self.rows[0]
        t = self._t_log0 + (time.time() - self._t0)
        while self._idx + 1 < len(self.rows) and row_time(self.rows[self._idx + 1]) <= t:
            self._idx += 1
        return self.rows[self._idx]

    def get_telemetry(self) -> Telemetry:
        row = self._current_row()
        t, alt = row_time(row), float(row["altitude_ft"])
        if self._prev is not None and t > self._prev[0]:
            raw = (alt - self._prev[1]) / (t - self._prev[0]) * 60.0
            raw = max(-VS_LIMIT_FPM, min(VS_LIMIT_FPM, raw))
            self._vs += 0.25 * (raw - self._vs)
        if self._prev is None or t != self._prev[0]:
            self._prev = (t, alt)
        return to_telemetry(row, self.elev, self._vs, self.local_h)

    def set_local_time(self, hour: int, minute: int) -> bool:
        self.local_h = hour + minute / 60.0
        return True

    # ---- checkpoints: a restore rewinds the log to the saved moment ----
    def save_state(self) -> dict:
        elapsed = 0.0 if self._t0 is None else time.time() - self._t0
        return {"elapsed": elapsed, "local_h": self.local_h}

    def restore_state(self, s: dict) -> None:
        self._t0 = time.time() - float(s.get("elapsed", 0.0))
        self._idx = 0
        self._prev = None
        self._vs = 0.0
        self.local_h = float(s.get("local_h", self.local_h))

    def close(self) -> None:
        pass
