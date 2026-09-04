"""
Read endpoints powering the review/playback UI.

Downsampling: charts don't need all 10 Hz samples. Pass ?max_points=N and
the server returns roughly N evenly-spaced rows using a row_number stride,
so a full flight renders fast. Omit it (or set 0) to get everything.
"""
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from .. import db

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


@router.get("/events")
async def get_events(session_id: UUID):
    async with db.acquire() as conn:
        rows = await conn.fetch(
            "select * from events where session_id = $1 order by ts", session_id
        )
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
        event_n = await conn.fetchval(
            "select count(*) from events where session_id = $1", session_id)
    return {
        "session": dict(s),
        "flight_rows": flight_n,
        "eye_rows": eye_n,
        "event_rows": event_n,
    }
