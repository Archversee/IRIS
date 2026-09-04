"""Session CRUD."""
from uuid import UUID

from fastapi import APIRouter, HTTPException

from .. import db
from ..schemas import Session, SessionCreate

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", response_model=Session, status_code=201)
async def create_session(body: SessionCreate):
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """
            insert into sessions (name, pilot_name, aircraft, sim_source, notes)
            values ($1, $2, $3, $4, $5)
            returning *
            """,
            body.name, body.pilot_name, body.aircraft, body.sim_source, body.notes,
        )
    return dict(row)


@router.get("", response_model=list[Session])
async def list_sessions():
    async with db.acquire() as conn:
        rows = await conn.fetch("select * from sessions order by created_at desc")
    return [dict(r) for r in rows]


@router.get("/{session_id}", response_model=Session)
async def get_session(session_id: UUID):
    async with db.acquire() as conn:
        row = await conn.fetchrow("select * from sessions where id = $1", session_id)
    if row is None:
        raise HTTPException(404, "Session not found")
    return dict(row)


@router.delete("/{session_id}", status_code=204)
async def delete_session(session_id: UUID):
    async with db.acquire() as conn:
        result = await conn.execute("delete from sessions where id = $1", session_id)
    if result.endswith("0"):
        raise HTTPException(404, "Session not found")
