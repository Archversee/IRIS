"""Eye-based workload index of a flight (app/workload.py does the analysis).

  GET /sessions/{id}/workload                 one value per second over the flight (cached)
      ?baseline_from=&baseline_to=            compare against this calm stretch instead of
                                              the whole flight (ISO times; not cached)

MAVIS's own demonstration flying (flight event kind "demo") is left out, as
the Review page leaves it out. The cache is redone when the eye data, the
AOI zones or WORKLOAD_VERSION change.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException

from .. import db
from ..workload import WORKLOAD_VERSION, analyse

log = logging.getLogger(__name__)
router = APIRouter(prefix="/sessions/{session_id}", tags=["workload"])

SCHEMA = """
create table if not exists session_workload (
    session_id   uuid primary key references sessions(id) on delete cascade,
    version      text not null,          -- WORKLOAD_VERSION + AOI zones + eye data fingerprint
    result       jsonb not null,
    computed_at  timestamptz not null default now()
)
"""

_EYE_COLS = ("ts, aoi, gaze_point_x, gaze_point_y, blink, gaze_quality, fixation_id, "
             "gaze_dir_x, gaze_dir_y, gaze_dir_z")
_NOT_DEMO = """
    and not exists (select 1 from flight_events e
                    where e.session_id = $1 and e.kind = 'demo'
                      and d.ts >= e.start_ts and d.ts < coalesce(e.end_ts, 'infinity'))"""


async def ensure_schema() -> None:
    try:
        async with db.acquire() as conn:
            await conn.execute(SCHEMA)
    except Exception as e:      # sessions not created yet
        log.warning("session_workload not created: %s", e)


def _j(v):
    return json.loads(v) if isinstance(v, str) else v


async def _version(conn, session_id: UUID) -> tuple[str, list[dict]]:
    zones = [dict(z) for z in await conn.fetch("select name, x1, y1, x2, y2 from aoi_zones order by id")]
    eye = await conn.fetchrow("select count(*) as n, max(ts) as last from eye_tracking_data where session_id=$1",
                              session_id)
    demo = await conn.fetchval("select count(*) from flight_events where session_id=$1 and kind='demo'", session_id)
    key = json.dumps([zones, eye["n"], eye["last"], demo], sort_keys=True, default=str)
    return f"{WORKLOAD_VERSION}:{hashlib.sha1(key.encode()).hexdigest()[:12]}", zones


@router.get("/workload")
async def get_workload(session_id: UUID, baseline_from: datetime | None = None, baseline_to: datetime | None = None):
    if (baseline_from is None) != (baseline_to is None):
        raise HTTPException(422, "Give both baseline_from and baseline_to, or neither")
    if baseline_from is not None and baseline_to <= baseline_from:
        raise HTTPException(422, "baseline_to must be after baseline_from")
    async with db.acquire() as conn:
        if not await conn.fetchval("select 1 from sessions where id=$1", session_id):
            raise HTTPException(404, "Session not found")
        version, zones = await _version(conn, session_id)
        cached = baseline_from is None
        if cached:
            row = await conn.fetchrow("select version, result from session_workload where session_id=$1", session_id)
            if row and row["version"] == version:
                return _j(row["result"])
        eye = await conn.fetch(f"select {_EYE_COLS} from eye_tracking_data d where session_id=$1"
                               f"{_NOT_DEMO} order by ts", session_id)
    baseline = (baseline_from.timestamp(), baseline_to.timestamp()) if baseline_from is not None else None
    result = analyse([dict(e) for e in eye], zones, baseline=baseline)
    if cached:
        async with db.acquire() as conn:
            await conn.execute("insert into session_workload (session_id, version, result) values ($1,$2,$3) "
                               "on conflict (session_id) do update set version=$2, result=$3, computed_at=now()",
                               session_id, version, json.dumps(result))
    return result
