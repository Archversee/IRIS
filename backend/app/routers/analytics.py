"""
Analytics endpoints. These are deliberately simple, dependency-free SQL
aggregations you can extend. Each maps to a pilot-performance question
from the project's flight-data schema rationale.
"""
from uuid import UUID

from fastapi import APIRouter, HTTPException

from .. import db

router = APIRouter(prefix="/sessions/{session_id}/analytics", tags=["analytics"])


@router.get("")
async def analytics_overview(session_id: UUID):
    async with db.acquire() as conn:
        exists = await conn.fetchval("select 1 from sessions where id = $1", session_id)
        if not exists:
            raise HTTPException(404, "Session not found")

        flight = await conn.fetchrow(
            """
            select
                count(*)                         as samples,
                min(ts)                          as start_ts,
                max(ts)                          as end_ts,
                avg(airspeed_kt)                 as avg_airspeed_kt,
                max(altitude_ft)                 as max_altitude_ft,
                -- control smoothness: std-dev of control positions
                stddev_samp(elevator_position)   as elevator_activity,
                stddev_samp(rudder_position)     as rudder_activity,
                -- coordination proxy: mean absolute yaw rate
                avg(abs(yaw_rate_radps))         as mean_abs_yaw_rate,
                -- stall-risk proxy: fraction of low-airspeed samples
                avg((airspeed_kt < 55)::int)::float as low_airspeed_fraction,
                -- steep-bank exposure
                avg((abs(bank_deg) > 45)::int)::float as steep_bank_fraction
            from flight_data
            where session_id = $1
            """,
            session_id,
        )

        eye = await conn.fetchrow(
            """
            select
                count(*)                       as samples,
                avg(gaze_quality)              as avg_quality,
                avg(blink::int)::float         as blink_fraction,
                avg(pupil_diam_left_mm)        as avg_pupil_left_mm
            from eye_tracking_data
            where session_id = $1
            """,
            session_id,
        )

        # gaze dwell time per area of interest (seconds), assuming ~10 Hz
        aoi = await conn.fetch(
            """
            select coalesce(aoi, 'unlabelled') as aoi, count(*) as samples
            from eye_tracking_data
            where session_id = $1
            group by aoi
            order by samples desc
            """,
            session_id,
        )

    return {
        "flight": dict(flight) if flight else None,
        "eye": dict(eye) if eye else None,
        "aoi_dwell": [dict(a) for a in aoi],
    }
