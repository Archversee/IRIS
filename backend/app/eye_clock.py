"""Eye <-> flight clock offset, per session.

LiveAdapter.py measures how far the Smart Eye PC's clock is from this PC's
(arrival time of each UDP packet vs Smart Eye's own timestamp), re-stamps
the eye samples onto this PC's clock, and reports the measurement here every
30 s. Review shows it, and the incident analysis uses it to say whether eye
and flight data can be trusted to line up.

  POST /sessions/{id}/eye-clock   {offset_s, spread_s, samples, corrected}
"""
import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import db

log = logging.getLogger(__name__)
router = APIRouter(tags=["eye-clock"])

_COLUMNS = (
    "eye_clock_offset_s double precision",   # seconds ADDED to Smart Eye's timestamps (0 = clocks agree)
    "eye_clock_spread_s double precision",   # how much the measurement varied (network jitter)
    "eye_clock_samples integer",
    "eye_clock_corrected boolean",           # false: measured but timestamps left as Smart Eye sent them
    "eye_clock_checked_at timestamptz",
)


async def ensure_schema() -> None:
    try:
        async with db.acquire() as conn:
            for col in _COLUMNS:
                await conn.execute(f"alter table sessions add column if not exists {col}")
    except Exception as e:  # sessions table not created yet
        log.warning("eye clock: couldn't add sessions.eye_clock_* columns: %s", e)


class EyeClock(BaseModel):
    offset_s: float
    spread_s: float = 0.0
    samples: int = 0
    corrected: bool = True


@router.post("/sessions/{session_id}/eye-clock")
async def report(session_id: UUID, body: EyeClock):
    async with db.acquire() as conn:
        r = await conn.execute(
            "update sessions set eye_clock_offset_s=$2, eye_clock_spread_s=$3, eye_clock_samples=$4, "
            "eye_clock_corrected=$5, eye_clock_checked_at=now() where id=$1",
            session_id, body.offset_s, body.spread_s, body.samples, body.corrected)
    if r.endswith(" 0"):
        raise HTTPException(404, "Session not found")
    return {"ok": True}
