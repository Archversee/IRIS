"""
Read endpoints powering the review/playback UI.

"""
import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from .. import db
from ..catalog import lesson_title
from ..config import settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions/{session_id}", tags=["data"])


def _time_filters(start: datetime | None, end: datetime | None, params: list):
    clause = ""
    if start is not None:
        params.append(start)
        clause += f" and ts >= ${len(params)}"
    if end is not None:
        params.append(end)
        clause += f" and ts <= ${len(params)}"
    return clause


async def _query_timeseries(table, session_id, start, end, max_points):
    params = [session_id]
    where = "session_id = $1" + _time_filters(start, end, params)

    if max_points and max_points > 0:
        total = await _count(table, where, params)
        stride = max(1, total // max_points)
        params.append(stride)
        sql = f"""
            select * from (
                select *, row_number() over (order by ts) as rn
                  from {table}
                 where {where}
            ) t
            where (rn - 1) % ${len(params)} = 0
            order by ts
        """
    else:
        sql = f"select * from {table} where {where} order by ts"

    async with db.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [{k: v for k, v in dict(r).items() if k != "rn"} for r in rows]


async def _count(table, where, params):
    async with db.acquire() as conn:
        return await conn.fetchval(f"select count(*) from {table} where {where}", *params)


@router.get("/flight")
async def get_flight(
    session_id: UUID,
    start: datetime | None = None,
    end: datetime | None = None,
    max_points: int = Query(2000, ge=0, le=200000),
):
    return await _query_timeseries("flight_data", session_id, start, end, max_points)


@router.get("/eye")
async def get_eye(
    session_id: UUID,
    start: datetime | None = None,
    end: datetime | None = None,
    max_points: int = Query(2000, ge=0, le=200000),
):
    return await _query_timeseries("eye_tracking_data", session_id, start, end, max_points)


# Phases that are not events (the briefing/debrief are not flown)
_NOT_EVENTS = {"", "BRIEFING", "DEBRIEF"}


async def _derive_events(conn, session_id, title: str | None) -> list[dict]:
    """Events rebuilt from flight_data.phase for a session that has none stored (flown
    before events were logged, or whose stored events were lost): every unbroken run of
    one phase is one event, each ending where the next begins. Outcomes and try counts
    from the live log are unknowable here, so outcome is null."""
    runs = await conn.fetch(
        """
        select phase, min(ts) as start_ts, max(ts) as end_ts
          from (select ts, phase,
                       sum(case when phase is distinct from prev then 1 else 0 end)
                           over (order by ts) as grp
                  from (select ts, phase, lag(phase) over (order by ts) as prev
                          from flight_data where session_id = $1) a) b
         group by grp, phase
         order by min(ts)
        """, session_id)
    out, seen = [], {}
    for i, r in enumerate(runs):
        phase = r["phase"] or ""
        if phase in _NOT_EVENTS:
            continue
        nxt = runs[i + 1] if i + 1 < len(runs) else None
        end = nxt["start_ts"] if nxt and (nxt["phase"] or "") not in _NOT_EVENTS else r["end_ts"]
        seen[phase] = seen.get(phase, 0) + 1
        out.append({
            "seq": len(out), "flight": 1, "event_id": phase, "label": phase.replace("_", " ").title(),
            "kind": "derived", "phase": phase, "circuit": None, "attempt": seen[phase],
            "start_ts": r["start_ts"], "end_ts": end, "outcome": None,
            "score": None, "grade": None, "detail": None,
            "flight_title": title, "curriculum": "lesson", "lesson_id": None,
        })
    return out


@router.get("/events")
async def get_events(session_id: UUID):
    """Start/end of each graded event (to-do step / phase) in order, as logged
    by MAVIS. end_ts is null for an event still in progress (live session)."""
    async with db.acquire() as conn:
        try:
            rows = await conn.fetch(
                """
                select e.*, f.title as flight_title, f.curriculum, f.lesson_id
                  from flight_events e
                  left join session_flights f on f.session_id = e.session_id and f.flight = e.flight
                 where e.session_id = $1
                 order by e.seq
                """, session_id)
        except Exception:
            # An older database layout (session_flights / flight_events.flight not created yet --
            # they are made when the backend starts): the events themselves must still show.
            log.exception("events: curriculum join failed, returning the plain events")
            rows = await conn.fetch(
                "select * from flight_events where session_id = $1 order by seq", session_id)
        if rows:
            return [dict(r) for r in rows]
        lesson_id = await conn.fetchval("select lesson_id from sessions where id = $1", session_id)
        return await _derive_events(conn, session_id, lesson_title(lesson_id))


@router.get("/flights")
async def get_flights(session_id: UUID):
    """The session's flights in order -- which curriculum each came from (a lesson, or
    free flight), its mode, start and end."""
    async with db.acquire() as conn:
        rows = await conn.fetch(
            "select * from session_flights where session_id = $1 order by flight", session_id)
    return [dict(r) for r in rows]


@router.get("/summary")
async def get_summary(session_id: UUID):
    """Counts + time bounds, used to bootstrap the review page."""
    async with db.acquire() as conn:
        s = await conn.fetchrow("select * from sessions where id = $1", session_id)
        if s is None:
            raise HTTPException(404, "Session not found")
        flight_n = await conn.fetchval(
            "select count(*) from flight_data where session_id = $1", session_id)
        eye_n = await conn.fetchval(
            "select count(*) from eye_tracking_data where session_id = $1", session_id)
        # which curriculum each flight of the session came from (MAVIS sessions only)
        try:
            flights = await conn.fetch(
                "select flight, title, curriculum, module, mode, outcome from session_flights "
                "where session_id = $1 order by flight", session_id)
        except Exception:      # table not created yet (backend not restarted since the upgrade)
            flights = []
        # the session's score: its latest scorecard (a Restart re-scores the same session)
        try:
            card = await conn.fetchrow(
                "select total, grade from scorecards where session_id = $1 "
                "order by created_at desc, id desc limit 1", session_id)
        except Exception:      # MAVIS-integration tables not created
            card = None
        # pilot: as saved on the Save Session page, else the name the run was posted under
        pilot = s["pilot_name"]
        if not pilot:
            try:
                pilot = await conn.fetchval(
                    "select coalesce(nullif(trim(concat_ws(' ', given_name, family_name)), ''), display_name) "
                    "from leaderboard_entries where session_id = $1 and display_name <> 'Anonymous' "
                    "order by scored_at desc limit 1", session_id)
            except Exception:
                pilot = None
        if not flights and s["lesson_id"] and lesson_title(s["lesson_id"]):
            # flown before flights were logged: the session's own lesson says what it was
            flights = [{"flight": 1, "title": lesson_title(s["lesson_id"]), "curriculum": "lesson",
                        "module": None, "mode": s["mode"], "outcome": None}]
    session = dict(s)
    for screen in ("instrument", "otw"):
        filename = session.get(f"{screen}_video_filename")
        session[f"{screen}_video_url"] = (
            f"{settings.public_base_url}/videos/{filename}" if filename else None
        )
    return {
        "session": session,
        "flight_rows": flight_n,
        "eye_rows": eye_n,
        "flights": [dict(f) for f in flights],
        "pilot": pilot,
        "score": dict(card) if card else None,     # {total, grade}
    }
