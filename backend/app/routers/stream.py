"""
Real-time streaming.

Live samples never touch the DB while a flight is in progress -- they're
held in memory here and relayed straight to whoever's watching. The
durable copy happens separately, after the fact: LiveAdapter.py POSTs the
full CSV log to /ingest/flight once the flight ends (see its LIVE
STREAMING docstring), and Smart Eye's own file export goes through
/ingest/eye the normal way (Upload page) -- the live eye feed below is a
preview only, it doesn't replace that.

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
from collections import deque
from uuid import UUID

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from .. import db
from ..schemas import EyeSample, FlightSample

router = APIRouter(prefix="/sessions/{session_id}/stream", tags=["stream"])

# In-memory only, bounded so a very long flight can't grow this forever --
# a dashboard that joins after the buffer has wrapped just misses the
# earliest samples of a long flight. Review (after the end-of-flight batch
# upload) always has the full log regardless.
_BACKLOG_SIZE = 20_000


class _LiveBuffer:
    __slots__ = ("flight_samples", "eye_samples", "subscribers", "flight_connected", "eye_connected", "flight_ws")

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


_live: dict[str, _LiveBuffer] = {}


def _buffer(session_id: UUID) -> _LiveBuffer:
    return _live.setdefault(str(session_id), _LiveBuffer())


def _drop_if_idle(session_id: UUID, buf: _LiveBuffer):
    # nothing left producing or watching -- free the memory
    if not buf.subscribers and not buf.flight_connected and not buf.eye_connected:
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
    """LiveAdapter.py's eye-side live feed -- same pattern as stream_producer above, for gaze data."""
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
            await _broadcast(buf, {"type": "eye_sample", "sample": record})
    except WebSocketDisconnect:
        pass
    finally:
        buf.eye_connected = False
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
