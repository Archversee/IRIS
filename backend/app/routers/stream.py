"""
Real-time streaming — STUB for the "later" phase.

Two ways in, both wired to the DB but intended as starting points:

  WS   /sessions/{id}/stream/ws     push JSON flight samples, they persist
  POST /sessions/{id}/stream/flight push a single JSON sample (easy to curl)

When you build the live sim bridge, have MSFSAdapter.py POST/stream rows
here instead of (or in addition to) writing CSV. The playback UI can later
subscribe to a broadcast channel for live view; that fan-out isn't built yet.
"""
from uuid import UUID

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from .. import db
from ..schemas import FlightSample

router = APIRouter(prefix="/sessions/{session_id}/stream", tags=["stream"])


def _sample_to_record(session_id: UUID, s: FlightSample) -> tuple:
    d = s.model_dump()
    return tuple([session_id, d["ts"]] + [d[c] for c in db.FLIGHT_COLUMNS[2:]])


@router.post("/flight", status_code=202)
async def push_flight_sample(session_id: UUID, sample: FlightSample):
    async with db.acquire() as conn:
        exists = await conn.fetchval("select 1 from sessions where id = $1", session_id)
    if not exists:
        raise HTTPException(404, "Session not found")
    await db.copy_rows("flight_data", db.FLIGHT_COLUMNS, [_sample_to_record(session_id, sample)])
    return {"status": "accepted"}


@router.websocket("/ws")
async def stream_ws(websocket: WebSocket, session_id: UUID):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            sample = FlightSample(**data)
            await db.copy_rows(
                "flight_data", db.FLIGHT_COLUMNS, [_sample_to_record(session_id, sample)]
            )
            await websocket.send_json({"status": "ok", "ts": sample.ts.isoformat()})
    except WebSocketDisconnect:
        return
