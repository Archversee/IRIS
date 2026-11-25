"""Session CRUD."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from .. import access, db, deleted_sessions, mavis_bridge, videos
from ..auth import current_user, optional_user
from ..catalog import lesson_title
from ..schemas import Session, SessionCreate, SessionUpdate
from . import stream

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("/running", response_model=list[Session])
async def list_running_sessions():
    """MAVIS-sourced sessions still in progress -- Live Monitor's picker
    lists these instead of owning any start/end lifecycle itself (MAVIS is
    the only thing that logs anything; IRIS only views). A session counts
    as "still running" until it is ended: End Session / Main menu, or a
    lesson finishing on its own, PATCHes ended_at (see LiveCoach.jsx). Fly
    again then starts a new session; Restart stays in the same one.

    Only the session MAVIS is on right now counts (mavis_bridge): one left
    without an ended_at by a crash or a closed tab is stale, and would
    otherwise sit here -- and get opened by the Live Monitor -- forever."""
    current = mavis_bridge.current_session_id()
    if not current:
        return []
    async with db.acquire() as conn:
        rows = await conn.fetch(
            "select * from sessions where source = 'mavis' and ended_at is null and id::text = $1",
            current)
    return [dict(r) for r in rows]


@router.post("", response_model=Session, status_code=201)
async def create_session(body: SessionCreate, user: dict = Depends(current_user)):
    fields = body.model_dump()
    async with db.acquire() as conn:
        await access.set_pilot(conn, fields, user, None)
        row = await conn.fetchrow(
            """
            insert into sessions (name, pilot_name, pilot_user_id, aircraft, sim_source, notes)
            values ($1, $2, $3, $4, $5, $6)
            returning *
            """,
            fields["name"], fields["pilot_name"], fields["pilot_user_id"],
            fields["aircraft"], fields["sim_source"], fields["notes"],
        )
    return dict(row)


@router.get("", response_model=list[Session])
async def list_sessions(user: dict = Depends(current_user)):
    """The flights this user may see (app/access.py)."""
    async with db.acquire() as conn:
        rows = await conn.fetch(
            f"""
            select s.*, {access.ACCOUNT_NAME.format(u='u')} as account_name,
                   (select array_agg(t.title order by t.first_flight)
                      from (select title, min(flight) as first_flight
                              from session_flights f where f.session_id = s.id
                             group by title) t) as curricula
              from sessions s left join users u on u.id = s.pilot_user_id
             where {access.visible_sql(user, '$1')}
             order by s.created_at desc
            """, user["id"])
    out = [dict(r) for r in rows]
    for r in out:      # the account's name as it is now
        r["pilot_name"] = r.pop("account_name") or r["pilot_name"]
    for r in out:      # flown before flights were logged: the session's own lesson says what it was
        if not r.get("curricula") and lesson_title(r.get("lesson_id")):
            r["curricula"] = [lesson_title(r["lesson_id"])]
    return out


@router.get("/{session_id}", response_model=Session)
async def get_session(session_id: UUID, user: dict | None = Depends(optional_user)):
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            f"select s.*, {access.ACCOUNT_NAME.format(u='u')} as account_name "
            "from sessions s left join users u on u.id = s.pilot_user_id where s.id = $1", session_id)
    if row is None:
        raise HTTPException(404, "Session not found")
    if user is None:
        # LiveAdapter.py (no account) polls this to know when to stop OBS: that it
        # exists and whether it has ended, nothing about the flight
        return {"id": row["id"], "name": "", "created_at": row["created_at"], "ended_at": row["ended_at"]}
    await access.check(session_id, user, live_ok=True)
    d = dict(row)
    d["pilot_name"] = d.pop("account_name") or d["pilot_name"]
    return d


@router.patch("/{session_id}", response_model=Session)
async def update_session(session_id: UUID, body: SessionUpdate, user: dict = Depends(current_user)):
    """Fills in session details after the fact -- Upload's own form uses
    this for name/pilot/aircraft/notes, and MAVIS's End Session/Main-menu
    flow (LiveCoach.jsx) uses it to set ended_at the moment the trainee
    actually quits."""
    fields = body.model_dump(exclude_unset=True)
    # whichever screen it's pressed on can end the flight MAVIS is on (OBS stops on it)
    if not (set(fields) == {"ended_at"} and access.is_current(session_id)):
        await access.check(session_id, user)
    async with db.acquire() as conn:
        current = await conn.fetchrow("select * from sessions where id = $1", session_id)
        if current is None:
            raise HTTPException(404, "Session not found")
        await access.set_pilot(conn, fields, user, dict(current))
        if not fields:
            return dict(current)
        set_clause = ", ".join(f"{col} = ${i}" for i, col in enumerate(fields, start=2))
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
        await stream.set_ended(session_id, True)     # and the gaze
        # the flight is over: write the AI debrief (summary + a note per broken rule) in the
        # background, so it's ready when Review is opened (routers/debrief.py)
        from .debrief import on_session_ended   # here: debrief imports half the routers
        on_session_ended(session_id)
    elif "ended_at" in fields:
        # cleared (null): "Fly again" after a lesson ended on its own --
        # flying again under the same session, LiveAdapter.py records again
        mavis_bridge.mark_reopened(session_id)
        await stream.set_ended(session_id, False)
    return dict(row)


@router.delete("/{session_id}", status_code=204)
async def delete_session(session_id: UUID, user: dict = Depends(current_user)):
    """Deletes the session, all its data (cascade), and its video files:
    linked videos + OBS recordings no other session uses (see videos.py)."""
    if not access.is_current(session_id):     # Finish & discard works from whichever screen it's pressed on
        await access.check(session_id, user)
    # before the delete: MAVIS and the eye bridge keep sending data for this session
    deleted_sessions.add(session_id)
    try:
        async with db.acquire() as conn:
            files = await videos.session_files(conn, session_id)
            result = await conn.execute("delete from sessions where id = $1", session_id)
    except BaseException:
        deleted_sessions.discard(session_id)     # still there: keep saving its data
        raise
    if result.endswith("0"):
        raise HTTPException(404, "Session not found")
    await deleted_sessions.remember(session_id)
    videos.delete_files(files)
