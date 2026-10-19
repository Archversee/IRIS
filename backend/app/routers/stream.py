"""
Real-time streaming.

Flight samples never touch the DB while a flight is in progress here --
they're held in memory and relayed to whoever's watching; the durable
copy happens separately (LiveAdapter.py's end-of-flight CSV POST to
/ingest/flight for a manual run, or app/mavis_bridge.py's own batched
writes for a MAVIS-driven one).

Eye samples are different: every live sample is *also* persisted to
eye_tracking_data as it arrives (see eye_stream_producer's flush below),
batched every EYE_FLUSH_INTERVAL_S, `ON CONFLICT DO NOTHING` so a
reconnect resending an overlapping timestamp can't crash the batch. This
runs for every session automatically, whether or not the UI is showing a
live gaze overlay for it. Smart Eye's own file export + /ingest/eye (the
Upload page) remains the durable, full-resolution path -- uploading it
after the flight *replaces* whatever partial live capture already landed
here (ingest.py's replace-on-upload semantics), so live capture is a
safety net, not a competing source of truth.

  WS   /sessions/{id}/stream/ws         flight producer -- LiveAdapter.py's
                                         flight side connects once and sends
                                         one JSON sample per message (FlightSample).
  WS   /sessions/{id}/stream/eye/ws     eye producer -- LiveAdapter.py's eye
                                         side, same pattern (EyeSample).
  WS   /sessions/{id}/stream/subscribe  consumer -- the Live page connects,
                                         gets the buffered backlog once,
                                         then a live push per new sample of
                                         either kind.
  POST /sessions/{id}/stream/flight     one-off sample straight to the DB,
                                         handy for curl testing -- unrelated
                                         to the in-memory paths above.
  POST /sessions/{id}/stream/stop-adapter
                                         End Session button -- pushes a stop
                                         command down the flight producer's
                                         own socket so LiveAdapter.py shuts
                                         down the same clean way Ctrl+C does
                                         (SimConnect close, OBS stop, CSV
                                         batch-upload) instead of being killed.
"""
import logging
import time
from collections import deque
from uuid import UUID

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from .. import db
from ..schemas import EyeSample, FlightSample

log = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions/{session_id}/stream", tags=["stream"])

# In-memory only, bounded so a very long flight can't grow this forever --
# a dashboard that joins after the buffer has wrapped just misses the
# earliest samples of a long flight. Review (after the end-of-flight batch
# upload) always has the full log regardless.
_BACKLOG_SIZE = 20_000

# Eye samples DO get batch-persisted live (unlike flight samples -- see
# module docstring); matches mavis_bridge.py's own flush cadence.
_EYE_FLUSH_INTERVAL_S = 1.0

_EYE_INSERT_SQL = (
    f"insert into eye_tracking_data ({', '.join(db.EYE_COLUMNS)}) "
    f"values ({', '.join(f'${i}' for i in range(1, len(db.EYE_COLUMNS) + 1))}) "
    "on conflict (session_id, ts) do nothing"
)


class _LiveBuffer:
    __slots__ = (
        "flight_samples", "eye_samples", "subscribers", "flight_connected", "eye_connected", "flight_ws",
        "eye_row_buffer", "last_eye_flush", "last_external",
    )

    def __init__(self):
        self.flight_samples: deque[dict] = deque(maxlen=_BACKLOG_SIZE)
        self.eye_samples: deque[dict] = deque(maxlen=_BACKLOG_SIZE)
        self.subscribers: set[WebSocket] = set()
        self.flight_connected = False
        self.eye_connected = False
        # The producer socket itself (not just the `flight_connected` flag) --
        # kept so stop_adapter() below can push a message to LiveAdapter.py
        # the same way _broadcast() pushes to subscribers.
        self.flight_ws: WebSocket | None = None
        # Rows waiting to be flushed to eye_tracking_data -- see
        # eye_stream_producer/_flush_eye_rows below.
        self.eye_row_buffer: list[tuple] = []
        self.last_eye_flush: float = 0.0
        # When ingest_external_sample() (mavis_bridge) last fed this buffer.
        # That producer holds no socket here, so it's tracked by time instead.
        self.last_external: float = 0.0


_live: dict[str, _LiveBuffer] = {}


def _buffer(session_id: UUID) -> _LiveBuffer:
    return _live.setdefault(str(session_id), _LiveBuffer())


# A buffer mavis_bridge has fed within this long still has a producer: the
# flight is going on, so its backlog must survive nobody watching for a while
# (leaving the Live page and coming back would otherwise show an empty
# timeline / ground track).
_EXTERNAL_IDLE_S = 120.0


def _idle(buf: _LiveBuffer) -> bool:
    return (not buf.subscribers and not buf.flight_connected and not buf.eye_connected
            and time.monotonic() - buf.last_external > _EXTERNAL_IDLE_S)


def _drop_if_idle(session_id: UUID, buf: _LiveBuffer):
    # nothing left producing or watching -- free the memory
    if _idle(buf):
        _live.pop(str(session_id), None)


async def _broadcast(buf: _LiveBuffer, message: dict):
    dead = []
    for ws in buf.subscribers:
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        buf.subscribers.discard(ws)


def _sample_to_record(session_id: UUID, s: FlightSample) -> tuple:
    d = s.model_dump()
    return tuple([session_id, d["ts"]] + [d[c] for c in db.FLIGHT_COLUMNS[2:]])


def _eye_sample_to_row(session_id: UUID, s: EyeSample) -> tuple:
    d = s.model_dump()
    return tuple([session_id, d["ts"]] + [d[c] for c in db.EYE_COLUMNS[2:]])


async def _flush_eye_rows(buf: _LiveBuffer) -> None:
    """Best-effort persist of whatever's buffered. Not fatal if it fails --
    e.g. a brief race where this session's `sessions` row hasn't been
    created yet by mavis_bridge (both it and LiveAdapter.py start watching
    MAVIS's feed at roughly the same time) would raise a FK violation here;
    losing a second of live-preview persistence to that race is fine, the
    Upload page's full-file replace is still the durable source of truth."""
    if not buf.eye_row_buffer:
        return
    rows, buf.eye_row_buffer = buf.eye_row_buffer, []
    try:
        async with db.acquire() as conn:
            await conn.executemany(_EYE_INSERT_SQL, rows)
    except Exception as e:
        log.warning("[stream] eye sample flush failed (%d rows dropped): %s", len(rows), e)


@router.websocket("/ws")
async def stream_producer(websocket: WebSocket, session_id: UUID):
    """LiveAdapter.py's flight-side live feed. Held in memory and relayed to subscribers -- not persisted here."""
    await websocket.accept()
    buf = _buffer(session_id)
    buf.flight_connected = True
    buf.flight_ws = websocket
    try:
        while True:
            try:
                data = await websocket.receive_json()
                sample = FlightSample(**data)
            except WebSocketDisconnect:
                raise
            except Exception as e:
                print(f"[stream] dropped malformed flight sample: {e}")
                continue
            record = sample.model_dump(mode="json")
            buf.flight_samples.append(record)
            await _broadcast(buf, {"type": "sample", "sample": record})
    except WebSocketDisconnect:
        pass
    finally:
        buf.flight_connected = False
        if buf.flight_ws is websocket:
            buf.flight_ws = None
        _drop_if_idle(session_id, buf)


@router.websocket("/eye/ws")
async def eye_stream_producer(websocket: WebSocket, session_id: UUID):
    """LiveAdapter.py's eye-side live feed -- same pattern as stream_producer
    above, for gaze data, plus a live batched persist to eye_tracking_data
    (see _flush_eye_rows) so every session's Smart Eye data lands in the DB
    even if nothing ever views the live overlay for it."""
    await websocket.accept()
    buf = _buffer(session_id)
    buf.eye_connected = True
    try:
        while True:
            try:
                data = await websocket.receive_json()
                sample = EyeSample(**data)
            except WebSocketDisconnect:
                raise
            except Exception as e:
                print(f"[stream] dropped malformed eye sample: {e}")
                continue
            record = sample.model_dump(mode="json")
            buf.eye_samples.append(record)
            buf.eye_row_buffer.append(_eye_sample_to_row(session_id, sample))
            await _broadcast(buf, {"type": "eye_sample", "sample": record})
            now = time.monotonic()
            if now - buf.last_eye_flush >= _EYE_FLUSH_INTERVAL_S:
                buf.last_eye_flush = now
                await _flush_eye_rows(buf)
    except WebSocketDisconnect:
        pass
    finally:
        buf.eye_connected = False
        await _flush_eye_rows(buf)
        _drop_if_idle(session_id, buf)


@router.websocket("/subscribe")
async def stream_subscriber(websocket: WebSocket, session_id: UUID):
    """Live page's feed: buffered backlog once, then a live push per new sample."""
    await websocket.accept()
    buf = _buffer(session_id)
    buf.subscribers.add(websocket)
    try:
        await websocket.send_json({
            "type": "backlog",
            "samples": list(buf.flight_samples),
            "eye_samples": list(buf.eye_samples),
        })
        while True:
            await websocket.receive_text()  # keep the socket open; client has nothing to say
    except WebSocketDisconnect:
        pass
    finally:
        buf.subscribers.discard(websocket)
        _drop_if_idle(session_id, buf)


async def ingest_external_sample(session_id: UUID, record: dict) -> None:
    """Feeds a sample from a producer other than the WS endpoints above
    (currently: mavis_bridge, reading MAVIS's own WS feed instead of
    SimConnect) into the same in-memory backlog/broadcast used by
    stream_producer, so the Live page's subscriber feed sees MAVIS-sourced
    telemetry the same way it sees LiveAdapter.py's. Does not touch the
    DB -- the caller is responsible for persistence (mavis_bridge batches
    its own writes to flight_data)."""
    buf = _buffer(session_id)
    now = time.monotonic()
    if now - buf.last_external > _EXTERNAL_IDLE_S:
        # (at most every couple of minutes per session) free the buffers of
        # flights that have finished and that nobody is watching any more
        for sid, other in list(_live.items()):
            if other is not buf and _idle(other):
                _live.pop(sid, None)
    buf.last_external = now
    buf.flight_samples.append(record)
    await _broadcast(buf, {"type": "sample", "sample": record})


@router.post("/flight", status_code=202)
async def push_flight_sample(session_id: UUID, sample: FlightSample):
    """One-off sample straight to the DB -- handy for curl testing, unrelated to the live dashboard path above."""
    async with db.acquire() as conn:
        exists = await conn.fetchval("select 1 from sessions where id = $1", session_id)
    if not exists:
        raise HTTPException(404, "Session not found")
    await db.copy_rows("flight_data", db.FLIGHT_COLUMNS, [_sample_to_record(session_id, sample)])
    return {"status": "accepted"}


@router.post("/stop-adapter", status_code=202)
async def stop_adapter(session_id: UUID):
    """End Session button. Pushes {"type": "stop"} down the flight producer's
    own socket -- LiveAdapter.py's on_message handler sets an Event that
    breaks its capture loop, so it exits through the same cleanup path as
    Ctrl+C (SimConnect close, OBS stop, CSV batch-upload) rather than being
    killed and leaving OBS recording / the CSV never uploaded."""
    buf = _live.get(str(session_id))
    if buf is None or buf.flight_ws is None:
        raise HTTPException(409, "No live adapter connected for this session")
    await buf.flight_ws.send_json({"type": "stop"})
    return {"status": "stop-requested"}
