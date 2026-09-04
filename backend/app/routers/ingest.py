"""
Batch ingestion of CSV files produced by the sim logger (MSFSAdapter.py)
and the Smart Eye export.

Endpoints:
  POST /sessions/{id}/ingest/flight   (multipart file=<csv>)
  POST /sessions/{id}/ingest/eye      (multipart file=<csv>)
  POST /sessions/{id}/ingest/events   (multipart file=<csv>)

After a flight upload we also backfill sessions.started_at / ended_at.
"""
import csv
import io
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, UploadFile

from .. import db
from ..schemas import IngestResult

router = APIRouter(prefix="/sessions/{session_id}/ingest", tags=["ingest"])

FLIGHT_BOOL = {"gear_handle_position", "sim_running", "sim_paused"}
EYE_BOOL = {"blink"}
EYE_INT = {"fixation_id"}
EYE_TEXT = {"aoi"}


# ---- value coercion -------------------------------------------------
def _to_bool(v: str | None):
    if v is None or v == "":
        return None
    return str(v).strip().lower() in ("1", "1.0", "true", "t", "yes")


def _to_float(v: str | None):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v: str | None):
    f = _to_float(v)
    return int(f) if f is not None else None


def _to_ts(v: str) -> datetime:
    # MSFSAdapter writes datetime.now(timezone.utc).isoformat()
    return datetime.fromisoformat(v)


async def _session_exists(session_id: UUID) -> bool:
    async with db.acquire() as conn:
        return await conn.fetchval("select 1 from sessions where id = $1", session_id) is not None


async def _read_csv(file: UploadFile) -> csv.DictReader:
    raw = (await file.read()).decode("utf-8-sig")
    return csv.DictReader(io.StringIO(raw))


# ---- flight ---------------------------------------------------------
@router.post("/flight", response_model=IngestResult)
async def ingest_flight(session_id: UUID, file: UploadFile):
    if not await _session_exists(session_id):
        raise HTTPException(404, "Session not found")

    reader = await _read_csv(file)
    records, skipped = [], 0
    for r in reader:
        ts_raw = r.get("timestamp_utc") or r.get("ts")
        if not ts_raw:
            skipped += 1
            continue
        rec = [session_id, _to_ts(ts_raw)]
        for col in db.FLIGHT_COLUMNS[2:]:  # skip session_id, ts
            val = r.get(col)
            rec.append(_to_bool(val) if col in FLIGHT_BOOL else _to_float(val))
        records.append(tuple(rec))

    inserted = await db.copy_rows("flight_data", db.FLIGHT_COLUMNS, records)
    await _refresh_session_bounds(session_id)
    return IngestResult(inserted=inserted, skipped=skipped)


# ---- eye tracking ---------------------------------------------------
@router.post("/eye", response_model=IngestResult)
async def ingest_eye(session_id: UUID, file: UploadFile):
    if not await _session_exists(session_id):
        raise HTTPException(404, "Session not found")

    reader = await _read_csv(file)
    records, skipped = [], 0
    for r in reader:
        ts_raw = r.get("ts") or r.get("timestamp_utc")
        if not ts_raw:
            skipped += 1
            continue
        rec = [session_id, _to_ts(ts_raw)]
        for col in db.EYE_COLUMNS[2:]:
            val = r.get(col)
            if col in EYE_BOOL:
                rec.append(_to_bool(val))
            elif col in EYE_INT:
                rec.append(_to_int(val))
            elif col in EYE_TEXT:
                rec.append(val or None)
            else:
                rec.append(_to_float(val))
        records.append(tuple(rec))

    inserted = await db.copy_rows("eye_tracking_data", db.EYE_COLUMNS, records)
    return IngestResult(inserted=inserted, skipped=skipped)


# ---- events ---------------------------------------------------------
@router.post("/events", response_model=IngestResult)
async def ingest_events(session_id: UUID, file: UploadFile):
    """
    Accepts either:
      - MSFSAdapter events CSV: two columns [timestamp, event_name], no header
      - a headered CSV with columns: ts,event_type,label
    """
    if not await _session_exists(session_id):
        raise HTTPException(404, "Session not found")

    raw = (await file.read()).decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(raw)))
    if not rows:
        return IngestResult(inserted=0)

    inserted, skipped = 0, 0
    header = [c.strip().lower() for c in rows[0]]
    headered = "ts" in header or "timestamp" in header

    async with db.acquire() as conn:
        data_rows = rows[1:] if headered else rows
        for cols in data_rows:
            if len(cols) < 2:
                skipped += 1
                continue
            ts_raw, label = cols[0], cols[-1]
            event_type = cols[1] if len(cols) >= 3 else "system"
            try:
                ts = _to_ts(ts_raw)
            except ValueError:
                skipped += 1
                continue
            await conn.execute(
                "insert into events (session_id, ts, event_type, label) values ($1,$2,$3,$4)",
                session_id, ts, event_type, label,
            )
            inserted += 1

    return IngestResult(inserted=inserted, skipped=skipped)


async def _refresh_session_bounds(session_id: UUID):
    async with db.acquire() as conn:
        await conn.execute(
            """
            update sessions s
               set started_at = b.min_ts, ended_at = b.max_ts
              from (select min(ts) min_ts, max(ts) max_ts
                      from flight_data where session_id = $1) b
             where s.id = $1
            """,
            session_id,
        )
