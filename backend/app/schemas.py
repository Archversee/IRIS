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


# ---- events ---------------------------------------------------------
class EventCreate(BaseModel):
    ts: datetime
    event_type: str
    label: str
    payload: dict | None = None


class Event(EventCreate):
    id: int
    session_id: UUID


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
