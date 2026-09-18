"""
Real-time streaming.

Live samples never touch the DB while a flight is in progress -- they're
held in memory here and relayed straight to whoever's watching. The
durable copy happens separately: MSFSAdapter.py POSTs the full CSV log to
the existing /ingest/flight endpoint once the flight ends (see its LIVE
STREAMING docstring), the same batch path the Upload page uses.

  WS   /sessions/{id}/stream/ws         producer -- MSFSAdapter.py connects
                                         once and sends one JSON sample per
                                         message (shape: FlightSample).
  WS   /sessions/{id}/stream/subscribe  consumer -- the Live page connects,
                                         gets the buffered backlog once,
                                         then a live push per new sample.
  POST /sessions/{id}/stream/flight     one-off sample straight to the DB,
                                         handy for curl testing -- unrelated
                                         to the in-memory paths above.
"""
from collections import deque
from uuid import UUID

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from .. import db
from ..schemas import FlightSample

router = APIRouter(prefix="/sessions/{session_id}/stream", tags=["stream"])

# In-memory only, bounded so a very long flight can't grow this forever --
# a dashboard that joins after the buffer has wrapped just misses the
# earliest samples of a long flight. Review (after the end-of-flight batch
# upload) always has the full log regardless.
_BACKLOG_SIZE = 20_000


class _LiveBuffer:
    __slots__ = ("samples", "subscribers", "producer_connected")

    def __init__(self):
        self.samples: deque[dict] = deque(maxlen=_BACKLOG_SIZE)
        self.subscribers: set[WebSocket] = set()
        self.producer_connected = False


_live: dict[str, _LiveBuffer] = {}


def _buffer(session_id: UUID) -> _LiveBuffer:
    return _live.setdefault(str(session_id), _LiveBuffer())


def _drop_if_idle(session_id: UUID, buf: _LiveBuffer):
    # nothing left producing or watching -- free the memory
    if not buf.subscribers and not buf.producer_connected:
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
    """MSFSAdapter.py's live feed. Held in memory and relayed to subscribers -- not persisted here."""
    await websocket.accept()
    buf = _buffer(session_id)
    buf.producer_connected = True
    try:
        while True:
            try:
                data = await websocket.receive_json()
                sample = FlightSample(**data)
            except WebSocketDisconnect:
                raise
            except Exception as e:
                print(f"[stream] dropped malformed sample: {e}")
                continue
            record = sample.model_dump(mode="json")
            buf.samples.append(record)
            await _broadcast(buf, {"type": "sample", "sample": record})
    except WebSocketDisconnect:
        pass
    finally:
        buf.producer_connected = False
        _drop_if_idle(session_id, buf)


@router.websocket("/subscribe")
async def stream_subscriber(websocket: WebSocket, session_id: UUID):
    """Live page's feed: buffered backlog once, then a live push per new sample."""
    await websocket.accept()
    buf = _buffer(session_id)
    buf.subscribers.add(websocket)
    try:
        await websocket.send_json({"type": "backlog", "samples": list(buf.samples)})
        while True:
            await websocket.receive_text()  # keep the socket open; client has nothing to say
    except WebSocketDisconnect:
        pass
    finally:
        buf.subscribers.discard(websocket)
        _drop_if_idle(session_id, buf)


@router.post("/flight", status_code=202)
async def push_flight_sample(session_id: UUID, sample: FlightSample):
    """One-off sample straight to the DB -- handy for curl testing, unrelated to the live dashboard path above."""
    async with db.acquire() as conn:
        exists = await conn.fetchval("select 1 from sessions where id = $1", session_id)
    if not exists:
        raise HTTPException(404, "Session not found")
    await db.copy_rows("flight_data", db.FLIGHT_COLUMNS, [_sample_to_record(session_id, sample)])
    return {"status": "accepted"}
