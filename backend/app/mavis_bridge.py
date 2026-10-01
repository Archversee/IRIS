"""Background bridge: MAVIS's `/ws` state feed -> Postgres + the existing
in-memory live relay (routers/stream.py), so IRIS never opens SimConnect
itself and its DB/Review/Live pages pick up MAVIS-sourced sessions the
same way they already pick up LiveAdapter.py-sourced ones. MAVIS mints
`session_id`; this is the only place that id gets attached to IRIS's
`sessions` table. See docs/INTEGRATION_PLAN.md §1b/§1c.

Not runnable/testable end-to-end from this checkout (no local Postgres on
this dev machine yet) -- the pure `map_*` functions below have no DB/WS
dependency and are covered by a standalone script; the connect/write glue
(MavisBridge) is reviewed but unverified until exercised against a real
Postgres + running MAVIS, on the sim PC.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from uuid import UUID

import websockets

from . import db
from .config import settings
from .routers import adapter
from .routers.stream import ingest_external_sample

log = logging.getLogger(__name__)

_RECONNECT_DELAY_S = 2.0
_MAX_RECONNECT_DELAY_S = 30.0
_FLUSH_INTERVAL_S = 1.0  # batch flight_data inserts instead of one per 5Hz WS tick
_FLAPS_FULL_DEG = 30.0   # C172 full flap -- only for deriving handle % from flap angle

# Extra columns this bridge populates beyond the original SimConnect-sourced
# set in db.FLIGHT_COLUMNS -- see db/schema_mavis_integration.sql. Kept as a
# bridge-local list (not merged into db.FLIGHT_COLUMNS/schemas.FlightSample)
# since only MAVIS populates these; the CSV-ingest and POST /stream/flight
# paths are untouched.
MAVIS_FLIGHT_EXTRA_COLUMNS = [
    "phase", "along_ft", "cross_ft", "rpm", "aileron_position", "flaps_deg", "on_ground",
]
FLIGHT_COLUMNS = db.FLIGHT_COLUMNS + MAVIS_FLIGHT_EXTRA_COLUMNS

COACHING_EVENT_COLUMNS = ["session_id", "ts", "code", "severity", "who", "phase", "message", "data"]
SCORECARD_COLUMNS = [
    "session_id", "lesson_id", "mode",
    "track_score", "speed_score", "altitude_score", "landing_score", "smoothness_score",
    "total", "grade", "retries", "capped",
]


def _to_utc(epoch_s: float | None) -> datetime | None:
    if epoch_s is None:
        return None
    return datetime.fromtimestamp(epoch_s, tz=timezone.utc)


def map_telemetry_fields(state: dict) -> dict | None:
    """Pure mapping: one MAVIS WS state push -> a dict keyed by FLIGHT_COLUMNS
    names (ts as a datetime), or None if there's no usable telemetry yet
    (e.g. before Start). See MAVIS's adapters/sim_base.py::Telemetry for the
    source field names/units and docs/INTEGRATION_PLAN.md for the mapping
    rationale (units MAVIS doesn't read -- yaw rate, fuel, ambient, gear --
    are left null, not guessed). Flap handle % / elevator trim come from
    MAVIS's own sim reads; if an adapter doesn't provide the handle %
    (mock sim), it's derived from the flap surface angle."""
    tel = state.get("tel")
    if not tel or tel.get("t") is None:
        return None
    geom = state.get("geom") or {}
    throttle = tel.get("throttle")
    flaps_pct = tel.get("flaps_handle_pct")
    if flaps_pct is None and tel.get("flaps_deg") is not None:
        flaps_pct = max(0.0, min(100.0, tel["flaps_deg"] / _FLAPS_FULL_DEG * 100.0))
    return {
        "ts": _to_utc(tel["t"]),
        "altitude_ft": tel.get("alt_msl"),
        "airspeed_kt": tel.get("ias"),
        "vertical_speed_fpm": tel.get("vs"),
        "heading_true_deg": tel.get("hdg"),
        "pitch_deg": tel.get("pitch"),
        "bank_deg": tel.get("bank"),
        "yaw_rate_radps": None,
        "fuel_total_qty_gal": None,
        "throttle_pct": (throttle * 100) if throttle is not None else None,
        "elevator_trim_pct": tel.get("elevator_trim_pct"),
        "elevator_position": tel.get("elevator"),
        "rudder_position": tel.get("rudder"),
        "flaps_handle_pct": flaps_pct,
        "gear_handle_position": None,
        "latitude": tel.get("lat"),
        "longitude": tel.get("lon"),
        "ambient_visibility_m": None,
        "ambient_wind_kt": None,
        "ambient_temp_c": None,
        "sim_running": True,
        "sim_paused": None,
        "phase": state.get("phase"),
        "along_ft": geom.get("along"),
        "cross_ft": geom.get("cross"),
        "rpm": tel.get("rpm"),
        "aileron_position": tel.get("aileron"),
        "flaps_deg": tel.get("flaps_deg"),
        "on_ground": tel.get("on_ground"),
    }


def map_coaching_events(session_id: UUID, transcript: list[dict], start_idx: int) -> list[tuple]:
    """Pure mapping: transcript entries beyond `start_idx` -> coaching_events
    rows. MAVIS's WS feed carries only {who, text, t, phase} per transcript
    entry (not the underlying CoachEvent's code/severity/data), so those
    three columns are left null here -- see coaching_events' comment in
    db/schema_mavis_integration.sql."""
    rows = []
    for entry in transcript[start_idx:]:
        ts = _to_utc(entry.get("t")) or datetime.now(timezone.utc)
        text = entry.get("text") or ""
        if not text:
            continue
        rows.append((session_id, ts, None, None, entry.get("who"), entry.get("phase"), text, None))
    return rows


def map_scorecard_row(session_id: UUID, state: dict, sc: dict) -> tuple:
    scores = sc.get("scores") or {}
    return (
        session_id, state.get("lesson_id"), sc.get("mode"),
        scores.get("track"), scores.get("speed"), scores.get("altitude"),
        scores.get("landing"), scores.get("smoothness"),
        sc.get("total"), sc.get("grade"), sc.get("retries"), sc.get("capped"),
    )


class _SessionCursor:
    __slots__ = ("seen", "ended", "transcript_len", "last_scorecard_seq", "row_buffer")

    def __init__(self):
        self.seen = False
        # Set by mark_ended() the instant routers/sessions.py's PATCH
        # handler sees ended_at get set (LiveCoach.jsx's End Session/Main-
        # menu flow) -- stops buffering/relaying any MORE telemetry for
        # this session. MAVIS's own tick loop keeps polling and
        # broadcasting telemetry regardless of whether a lesson was quit
        # (a lower-level, always-on loop this integration doesn't touch --
        # see main.py's ticker()), so without this, flight data would keep
        # streaming in and dragging out Live Monitor/Upload until the NEXT
        # session started.
        self.ended = False
        self.transcript_len = 0
        self.last_scorecard_seq = None
        self.row_buffer: list[tuple] = []


class MavisBridge:
    def __init__(self, ws_url: str):
        self.ws_url = ws_url
        self._sessions: dict[str, _SessionCursor] = {}
        self._last_leaderboard_count = -1
        self._last_flush = 0.0
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run_forever(), name="mavis_bridge")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run_forever(self) -> None:
        delay = _RECONNECT_DELAY_S
        while True:
            try:
                async with websockets.connect(self.ws_url, open_timeout=5) as ws:
                    log.info("mavis_bridge: connected to %s", self.ws_url)
                    delay = _RECONNECT_DELAY_S
                    async for raw in ws:
                        try:
                            state = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        try:
                            await self._handle_state(state)
                        except Exception:
                            log.exception("mavis_bridge: error handling state push")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("mavis_bridge: connection to %s lost/unavailable (%s) -- retrying in %.0fs",
                             self.ws_url, e, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, _MAX_RECONNECT_DELAY_S)

    async def _handle_state(self, state: dict) -> None:
        raw_sid = state.get("session_id")
        if not raw_sid:
            return  # nothing minted yet (pre-Start) -- nothing to attach data to
        session_id = UUID(raw_sid)
        cur = self._sessions.setdefault(raw_sid, _SessionCursor())
        if not cur.seen:
            await self._ensure_session_row(session_id, state)
            cur.seen = True
            # Every MAVIS session (Basic Flight, Free Flight, any mode) gets
            # OBS + Smart Eye recording automatically now -- MAVIS is the
            # only thing that starts a session, so this is the one place
            # that's true for all of them. Best-effort: LiveAdapter.py's own
            # lock file makes a duplicate launch a safe no-op, and if OBS/
            # the adapter script isn't set up on this machine at all, a
            # session should still proceed without it.
            try:
                await adapter.launch_adapter()
            except Exception as e:
                log.warning("mavis_bridge: couldn't auto-launch the OBS/Smart-Eye adapter: %s", e)

        fields = map_telemetry_fields(state)
        if fields is not None and not cur.ended:
            # fields has every FLIGHT_COLUMNS key except session_id (index 0);
            # FLIGHT_COLUMNS[1:] (starting at "ts") lines up with its keys exactly.
            cur.row_buffer.append((session_id, *(fields[c] for c in FLIGHT_COLUMNS[1:])))
            fields["session_id"] = session_id
            # Live-relay path -- Review/Live's existing WS consumer gets
            # MAVIS-sourced samples the same way it gets LiveAdapter.py's,
            # independent of the batched DB write below.
            await ingest_external_sample(session_id, _json_safe(fields))

        transcript = state.get("transcript") or []
        if len(transcript) > cur.transcript_len:
            events = map_coaching_events(session_id, transcript, cur.transcript_len)
            cur.transcript_len = len(transcript)
            if events:
                await db.copy_rows("coaching_events", COACHING_EVENT_COLUMNS, events)

        sc = state.get("scorecard")
        if sc and sc.get("seq") is not None and sc.get("seq") != cur.last_scorecard_seq:
            cur.last_scorecard_seq = sc.get("seq")
            await db.copy_rows("scorecards", SCORECARD_COLUMNS, [map_scorecard_row(session_id, state, sc)])

        now = time.monotonic()
        if now - self._last_flush >= _FLUSH_INTERVAL_S:
            self._last_flush = now
            await self._flush_flight_rows()
            await self._sync_leaderboard(state)

    async def _ensure_session_row(self, session_id: UUID, state: dict) -> None:
        sess = state.get("session") or {}
        name = (sess.get("name") or "").strip() or f"MAVIS {state.get('lesson_id') or 'session'}"
        async with db.acquire() as conn:
            await conn.execute(
                """
                insert into sessions (id, name, lesson_id, mode, sim_source, source, started_at)
                values ($1, $2, $3, $4, 'MSFS', 'mavis', now())
                on conflict (id) do nothing
                """,
                session_id, name, state.get("lesson_id"), sess.get("mode"),
            )

    async def _flush_flight_rows(self) -> None:
        all_rows: list[tuple] = []
        for cur in self._sessions.values():
            if cur.row_buffer:
                all_rows.extend(cur.row_buffer)
                cur.row_buffer.clear()
        if all_rows:
            await db.copy_rows("flight_data", FLIGHT_COLUMNS, all_rows)

    async def _sync_leaderboard(self, state: dict) -> None:
        lb = state.get("leaderboard") or {}
        rows: list[dict] = []
        for mode_bucket in lb.values():
            rows.extend((mode_bucket or {}).get("all") or [])
        if len(rows) == self._last_leaderboard_count:
            return
        self._last_leaderboard_count = len(rows)
        for e in rows:
            await self._upsert_leaderboard_entry(e)

    async def _upsert_leaderboard_entry(self, e: dict) -> None:
        scores = e.get("scores") or {}
        scored_at = _to_utc(e.get("ts")) or datetime.now(timezone.utc)
        async with db.acquire() as conn:
            await conn.execute(
                """
                insert into leaderboard_entries
                    (display_name, given_name, family_name, mode, lesson_id, scored_at,
                     track_score, speed_score, altitude_score, landing_score, smoothness_score,
                     total, grade, retries, capped)
                select $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15
                where not exists (
                    select 1 from leaderboard_entries
                    where display_name = $1 and scored_at = $6 and lesson_id is not distinct from $5
                )
                """,
                e.get("name"), e.get("given"), e.get("family"), e.get("mode"), e.get("lesson"),
                scored_at, scores.get("track"), scores.get("speed"), scores.get("altitude"),
                scores.get("landing"), scores.get("smoothness"), e.get("total"), e.get("grade"),
                e.get("retries"), e.get("capped"),
            )


def _json_safe(fields: dict) -> dict:
    out = dict(fields)
    out["session_id"] = str(out["session_id"])
    if isinstance(out.get("ts"), datetime):
        out["ts"] = out["ts"].isoformat()
    return out


_bridge: MavisBridge | None = None


def start() -> None:
    global _bridge
    if _bridge is None:
        _bridge = MavisBridge(settings.mavis_ws_url)
        _bridge.start()


async def stop() -> None:
    global _bridge
    if _bridge is not None:
        await _bridge.stop()
        _bridge = None


def mark_ended(session_id) -> None:
    """Called synchronously from routers/sessions.py's PATCH handler the
    moment a session's ended_at gets set. In-process, no polling -- takes
    effect on this bridge's very next state push from MAVIS. A session
    this bridge was never tracking (e.g. ended_at set on a non-MAVIS
    session) is just a no-op."""
    if _bridge is not None:
        cur = _bridge._sessions.get(str(session_id))
        if cur is not None:
            cur.ended = True
