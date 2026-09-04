"""
Thin async data-access layer over asyncpg.

We use asyncpg directly (no ORM) because the hot path is bulk-loading
time-series rows, and asyncpg's copy_records_to_table is the fastest way
to do that from Python.
"""
from contextlib import asynccontextmanager

import asyncpg

from .config import settings

_pool: asyncpg.Pool | None = None


async def connect() -> None:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=settings.database_url,
            min_size=1,
            max_size=10,
        )


async def disconnect() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialised. Call connect() on startup.")
    return _pool


@asynccontextmanager
async def acquire():
    async with pool().acquire() as conn:
        yield conn


# ---------------------------------------------------------------------
# Bulk ingest helpers
# ---------------------------------------------------------------------

# Column order MUST match the CREATE TABLE order in db/schema.sql.
FLIGHT_COLUMNS = [
    "session_id", "ts",
    "altitude_ft", "airspeed_kt", "vertical_speed_fpm", "heading_true_deg",
    "pitch_deg", "bank_deg", "yaw_rate_radps", "fuel_total_qty_gal",
    "throttle_pct", "elevator_trim_pct", "elevator_position", "rudder_position",
    "flaps_handle_pct", "gear_handle_position",
    "latitude", "longitude", "ambient_visibility_m", "ambient_wind_kt",
    "ambient_temp_c",
    "sim_running", "sim_paused",
]

EYE_COLUMNS = [
    "session_id", "ts",
    "gaze_origin_x", "gaze_origin_y", "gaze_origin_z",
    "gaze_dir_x", "gaze_dir_y", "gaze_dir_z",
    "gaze_point_x", "gaze_point_y",
    "pupil_diam_left_mm", "pupil_diam_right_mm", "eyelid_opening_mm", "blink",
    "head_pos_x", "head_pos_y", "head_pos_z",
    "head_heading_deg", "head_pitch_deg", "head_roll_deg",
    "gaze_quality", "aoi", "fixation_id",
]


async def copy_rows(table: str, columns: list[str], records: list[tuple]) -> int:
    """Bulk insert already-typed tuples. Returns row count."""
    if not records:
        return 0
    async with acquire() as conn:
        await conn.copy_records_to_table(table, records=records, columns=columns)
    return len(records)
