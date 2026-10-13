"""Pydantic models for the API surface."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


# ---- sessions -------------------------------------------------------
class SessionCreate(BaseModel):
    name: str
    pilot_name: str | None = None
    aircraft: str | None = None
    sim_source: str | None = None
    notes: str | None = None


class Session(SessionCreate):
    id: UUID
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime
    # MAVIS-sourced sessions only (db/schema_mavis_integration.sql); null otherwise
    lesson_id: str | None = None
    mode: str | None = None
    # What each flight in the session was ("Flight Familiarization", "Free Flight", ...),
    # in order, repeats removed; null when no flight was logged by MAVIS
    curricula: list[str] | None = None


class SessionUpdate(BaseModel):
    # Same fields as SessionCreate, all optional -- lets the End Session
    # flow (or anyone else) fill in details after the session already
    # exists without having to resend the ones that aren't changing.
    name: str | None = None
    pilot_name: str | None = None
    aircraft: str | None = None
    sim_source: str | None = None
    notes: str | None = None
    # Set by MAVIS's End Session flow the moment the trainee quits, and when a
    # lesson finishes on its own (its scorecard appears) -- so OBS stops
    # either way. "Fly again" keeps the same session_id going and sends null
    # to clear it (LiveAdapter.py then starts recording again). This is what
    # Live Monitor's /sessions/running filters on to know a session is in
    # progress.
    ended_at: datetime | None = None


# ---- AOI zones -------------------------------------------------------
class AoiZoneCreate(BaseModel):
    name: str
    x1: float
    y1: float
    x2: float
    y2: float


class AoiZone(AoiZoneCreate):
    id: int


# ---- ingest result --------------------------------------------------
class IngestResult(BaseModel):
    inserted: int
    skipped: int = 0
    message: str | None = None


# ---- streaming row (single-sample push) -----------------------------
class FlightSample(BaseModel):
    ts: datetime
    # every flight field optional so partial samples still stream
    altitude_ft: float | None = None
    airspeed_kt: float | None = None
    vertical_speed_fpm: float | None = None
    heading_true_deg: float | None = None
    pitch_deg: float | None = None
    bank_deg: float | None = None
    yaw_rate_radps: float | None = None
    fuel_total_qty_gal: float | None = None
    throttle_pct: float | None = None
    elevator_trim_pct: float | None = None
    elevator_position: float | None = None
    rudder_position: float | None = None
    flaps_handle_pct: float | None = None
    gear_handle_position: bool | None = None
    latitude: float | None = None
    longitude: float | None = None
    ambient_visibility_m: float | None = None
    ambient_wind_kt: float | None = None
    ambient_temp_c: float | None = None
    sim_running: bool | None = None
    sim_paused: bool | None = None


class EyeSample(BaseModel):
    ts: datetime
    # every field optional so partial samples still stream; names/shape
    # match db.EYE_COLUMNS and the SmartEye* field mapping in ingest.py
    gaze_origin_x: float | None = None
    gaze_origin_y: float | None = None
    gaze_origin_z: float | None = None
    gaze_dir_x: float | None = None
    gaze_dir_y: float | None = None
    gaze_dir_z: float | None = None
    gaze_point_x: float | None = None
    gaze_point_y: float | None = None
    pupil_diam_left_mm: float | None = None
    pupil_diam_right_mm: float | None = None
    eyelid_opening_mm: float | None = None
    blink: bool | None = None
    head_pos_x: float | None = None
    head_pos_y: float | None = None
    head_pos_z: float | None = None
    head_heading_deg: float | None = None
    head_pitch_deg: float | None = None
    head_roll_deg: float | None = None
    gaze_quality: float | None = None
    aoi: str | None = None
    fixation_id: int | None = None
