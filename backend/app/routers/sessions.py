"""Session CRUD."""
from uuid import UUID

from fastapi import APIRouter, HTTPException

from .. import db
from ..schemas import Session, SessionCreate, SessionUpdate

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("/live", response_model=Session)
async def get_live_session():
    """The most recently started MAVIS-sourced session -- how the Live page
    finds out which session to attach to now that MAVIS (not IRIS) mints
    session_id. Populated by backend/app/mavis_bridge.py the instant MAVIS
    reports one, independent of whether the OBS/Smart-Eye adapter (see
    routers/adapter.py) is running."""
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "select * from sessions where source = 'mavis' order by created_at desc limit 1"
        )
    if row is None:
        raise HTTPException(404, "No MAVIS session yet -- press Start in MAVIS first")
    return dict(row)


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


@router.patch("/{session_id}", response_model=Session)
async def update_session(session_id: UUID, body: SessionUpdate):
    """Fills in session details after the fact -- used by the End Session
    flow, where the session was created upfront (with a placeholder name)
    when "Go Live" was clicked, and the real name/pilot/aircraft/notes are
    only known once the flight is over."""
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        row = await get_session(session_id)
        return row

    set_clause = ", ".join(f"{col} = ${i}" for i, col in enumerate(fields, start=2))
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            f"update sessions set {set_clause} where id = $1 returning *",
            session_id, *fields.values(),
        )
    if row is None:
        raise HTTPException(404, "Session not found")
    return dict(row)


@router.delete("/{session_id}", status_code=204)
async def delete_session(session_id: UUID):
    async with db.acquire() as conn:
        result = await conn.execute("delete from sessions where id = $1", session_id)
    if result.endswith("0"):
        raise HTTPException(404, "Session not found")
