"""Session CRUD."""
from uuid import UUID

from fastapi import APIRouter, HTTPException

from .. import db, mavis_bridge, videos
from ..catalog import lesson_title
from ..schemas import Session, SessionCreate, SessionUpdate

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("/running", response_model=list[Session])
async def list_running_sessions():
    """MAVIS-sourced sessions still in progress -- Live Monitor's picker
    lists these instead of owning any start/end lifecycle itself (MAVIS is
    the only thing that logs anything; IRIS only views). A session counts
    as "still running" until it is ended: End Session / Main menu, or a
    lesson finishing on its own, PATCHes ended_at (see LiveCoach.jsx). Fly
    again then starts a new session; Restart stays in the same one."""
    async with db.acquire() as conn:
        rows = await conn.fetch(
            "select * from sessions where source = 'mavis' and ended_at is null "
            "order by started_at desc nulls last, created_at desc"
        )
    return [dict(r) for r in rows]


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
        rows = await conn.fetch(
            """
            select s.*,
                   (select array_agg(t.title order by t.first_flight)
                      from (select title, min(flight) as first_flight
                              from session_flights f where f.session_id = s.id
                             group by title) t) as curricula
              from sessions s
             order by s.created_at desc
            """)
    out = [dict(r) for r in rows]
    for r in out:      # flown before flights were logged: the session's own lesson says what it was
        if not r.get("curricula") and lesson_title(r.get("lesson_id")):
            r["curricula"] = [lesson_title(r["lesson_id"])]
    return out


@router.get("/{session_id}", response_model=Session)
async def get_session(session_id: UUID):
    async with db.acquire() as conn:
        row = await conn.fetchrow("select * from sessions where id = $1", session_id)
    if row is None:
        raise HTTPException(404, "Session not found")
    return dict(row)


@router.patch("/{session_id}", response_model=Session)
async def update_session(session_id: UUID, body: SessionUpdate):
    """Fills in session details after the fact -- Upload's own form uses
    this for name/pilot/aircraft/notes, and MAVIS's End Session/Main-menu
    flow (LiveCoach.jsx) uses it to set ended_at the moment the trainee
    actually quits."""
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
    if fields.get("ended_at") is not None:
        # In-process, no polling -- stops mavis_bridge from buffering/
        # relaying any more flight_data for this session on its very next
        # state push from MAVIS (which otherwise keeps polling/broadcasting
        # telemetry regardless of whether a lesson was quit).
        mavis_bridge.mark_ended(session_id)
        # the flight is over: write the AI debrief (summary + a note per broken rule) in the
        # background, so it's ready when Review is opened (routers/debrief.py)
        from .debrief import on_session_ended   # here: debrief imports half the routers
        on_session_ended(session_id)
    elif "ended_at" in fields:
        # cleared (null): "Fly again" after a lesson ended on its own --
        # flying again under the same session, LiveAdapter.py records again
        mavis_bridge.mark_reopened(session_id)
    return dict(row)


@router.delete("/{session_id}", status_code=204)
async def delete_session(session_id: UUID):
    """Deletes the session, all its data (cascade), and its video files:
    linked videos + OBS recordings no other session uses (see videos.py)."""
    async with db.acquire() as conn:
        files = await videos.session_files(conn, session_id)
        result = await conn.execute("delete from sessions where id = $1", session_id)
    if result.endswith("0"):
        raise HTTPException(404, "Session not found")
    videos.delete_files(files)
