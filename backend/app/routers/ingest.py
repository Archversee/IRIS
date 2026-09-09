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
import math
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, UploadFile

from .. import db
from ..schemas import IngestResult

router = APIRouter(prefix="/sessions/{session_id}/ingest", tags=["ingest"])

FLIGHT_BOOL = {"gear_handle_position", "sim_running", "sim_paused"}
EYE_BOOL = {"blink"}
EYE_INT = {"fixation_id"}
EYE_TEXT = {"aoi"}


# ---- Smart Eye raw export auto-conversion ----------------------------
# Smart Eye's own "Output Data" logger exports a wide, tab-separated file
# rather than the flat CSV shape below. Detected by sniffing for its
# RealTimeClock column (a Windows FILETIME -- 100ns ticks since 1601-01-01
# UTC, i.e. an absolute wall-clock time already, no sync offset needed)
# on a tab-delimited first line, so users can drop the raw .log straight
# into the Upload page instead of running a converter by hand first.
_FILETIME_EPOCH_DELTA = 116444736000000000  # 100ns ticks between 1601-01-01 and 1970-01-01

_SMARTEYE_DIRECT = {
    "gaze_origin_x": "FilteredGazeOrigin.x",
    "gaze_origin_y": "FilteredGazeOrigin.y",
    "gaze_origin_z": "FilteredGazeOrigin.z",
    "gaze_dir_x": "FilteredGazeDirection.x",
    "gaze_dir_y": "FilteredGazeDirection.y",
    "gaze_dir_z": "FilteredGazeDirection.z",
    "gaze_point_x": "FilteredClosestWorldIntersection.objectPoint.x",
    "gaze_point_y": "FilteredClosestWorldIntersection.objectPoint.y",
    "head_pos_x": "HeadPosition.x",
    "head_pos_y": "HeadPosition.y",
    "head_pos_z": "HeadPosition.z",
    "gaze_quality": "FilteredGazeDirectionQ",
    "aoi": "FilteredClosestWorldIntersection.objectName",
    "fixation_id": "Fixation",
}
_SMARTEYE_MM = {
    "pupil_diam_left_mm": "FilteredLeftPupilDiameter",
    "pupil_diam_right_mm": "FilteredRightPupilDiameter",
    "eyelid_opening_mm": "EyelidOpening",
}
_SMARTEYE_DEG = {
    "head_heading_deg": "HeadHeading",
    "head_pitch_deg": "HeadPitch",
    "head_roll_deg": "HeadRoll",
}


def _is_smarteye_raw(raw: str) -> bool:
    first_line = raw.splitlines()[0] if raw else ""
    return "RealTimeClock" in first_line and "\t" in first_line


def _smarteye_filetime_to_iso(ticks: str) -> str:
    unix_100ns = int(ticks) - _FILETIME_EPOCH_DELTA
    dt = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=unix_100ns / 10)
    return dt.isoformat()


def _smarteye_rows(raw: str):
    """Reshapes a raw Smart Eye export into EYE_COLUMNS-keyed string dicts,
    so the rest of ingest_eye's coercion logic runs unchanged."""
    for row in csv.DictReader(io.StringIO(raw), delimiter="\t"):
        out = {"ts": _smarteye_filetime_to_iso(row["RealTimeClock"])}
        for out_col, src_col in _SMARTEYE_DIRECT.items():
            out[out_col] = row.get(src_col)
        for out_col, src_col in _SMARTEYE_MM.items():
            v = row.get(src_col)
            out[out_col] = str(float(v) * 1000) if v not in (None, "") else None
        for out_col, src_col in _SMARTEYE_DEG.items():
            v = row.get(src_col)
            out[out_col] = str(math.degrees(float(v))) if v not in (None, "") else None
        # Blink is a blink-event id counter (0 = not blinking), not a 0/1 flag
        blink_raw = row.get("Blink")
        out["blink"] = "0" if blink_raw in (None, "", "0") else "1"
        yield out


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


def _decode_upload(raw_bytes: bytes) -> str:
    # Windows export tools (Smart Eye included) often save as UTF-16 rather
    # than UTF-8 -- decoding those bytes as UTF-8 doesn't raise, it just
    # silently interleaves NUL bytes through the text, breaking any string
    # match against the content. Detect the BOM and decode accordingly.
    if raw_bytes.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw_bytes.decode("utf-16")
    return raw_bytes.decode("utf-8-sig")


async def _read_csv(file: UploadFile) -> csv.DictReader:
    raw = _decode_upload(await file.read())
    return csv.DictReader(io.StringIO(raw))


async def _read_eye_rows(file: UploadFile):
    raw = _decode_upload(await file.read())
    if _is_smarteye_raw(raw):
        return _smarteye_rows(raw)
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

    reader = await _read_eye_rows(file)
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
