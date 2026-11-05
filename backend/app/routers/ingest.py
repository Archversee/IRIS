"""
Batch ingestion of CSV files produced by the sim logger (Sim_Data_Adapter/LiveAdapter.py) and the Smart Eye export.

Endpoints:
  POST /sessions/{id}/ingest/flight
  POST /sessions/{id}/ingest/eye
"""
import csv
import io
import json
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
EYE_JSON = {"extra"}


# ---- Smart Eye raw export auto-conversion ----------------------------
# Smart Eye's own "Output Data" logger exports a wide, tab-separated file
# rather than the flat CSV shape below. Detected by sniffing for its
# RealTimeClock column
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
# Other Smart Eye columns kept in eye_tracking_data.extra, under the same
# names the live feed uses (Sim_Data_Adapter/LiveAdapter.py _IDS), as
# exported (pupil / eyelid in metres). Only those present in the file.
_SMARTEYE_EXTRA = (
    "Saccade", "HeadPositionQ", "HeadRotationQ", "LeftGazeDirectionQ", "RightGazeDirectionQ",
    "FilteredGazeDirectionQ", "FilteredLeftGazeDirectionQ", "FilteredRightGazeDirectionQ",
    "EyelidOpeningQ", "LeftEyelidOpening", "LeftEyelidOpeningQ", "RightEyelidOpening", "RightEyelidOpeningQ",
    "LeftEyelidState", "LeftEyelidStateQ", "RightEyelidState", "RightEyelidStateQ",
    "PupilDiameter", "PupilDiameterQ", "LeftPupilDiameter", "LeftPupilDiameterQ",
    "RightPupilDiameter", "RightPupilDiameterQ", "FilteredPupilDiameter", "FilteredPupilDiameterQ",
    "FilteredLeftPupilDiameterQ", "FilteredRightPupilDiameterQ",
    "LeftEyeOccluded", "LeftEyeOccludedQ", "RightEyeOccluded", "RightEyeOccludedQ", "Glasses", "GlassesQ",
    "FrameRate",
)
_SMARTEYE_DEG = {
    "head_heading_deg": "HeadHeading",
    "head_pitch_deg": "HeadPitch",
    "head_roll_deg": "HeadRoll",
}


# Last known Smart Eye field-selection order, used ONLY as a fallback when
# an export is missing its header. it silently misaligns data if the field selection
# ever changes without headers to catch it.
_SMARTEYE_KNOWN_HEADER = [
    "FrameNumber", "TimeStamp", "RealTimeClock",
    "HeadPosition.x", "HeadPosition.y", "HeadPosition.z",
    "HeadHeading", "HeadPitch", "HeadRoll",
    "FilteredGazeDirection.x", "FilteredGazeDirection.y", "FilteredGazeDirection.z", "FilteredGazeDirectionQ",
    "FilteredGazeOrigin.x", "FilteredGazeOrigin.y", "FilteredGazeOrigin.z",
    "Saccade", "Fixation", "Blink",
    "LeftBlinkClosingMidTime", "LeftBlinkOpeningMidTime", "LeftBlinkClosingAmplitude",
    "LeftBlinkOpeningAmplitude", "LeftBlinkClosingSpeed", "LeftBlinkOpeningSpeed",
    "RightBlinkClosingMidTime", "RightBlinkOpeningMidTime", "RightBlinkClosingAmplitude",
    "RightBlinkOpeningAmplitude", "RightBlinkClosingSpeed", "RightBlinkOpeningSpeed",
    "FilteredClosestWorldIntersection.worldPoint.x", "FilteredClosestWorldIntersection.worldPoint.y",
    "FilteredClosestWorldIntersection.worldPoint.z", "FilteredClosestWorldIntersection.objectPoint.x",
    "FilteredClosestWorldIntersection.objectPoint.y", "FilteredClosestWorldIntersection.objectPoint.z",
    "FilteredClosestWorldIntersection.objectName",
    "EyelidOpening", "LeftEyelidOpening", "RightEyelidOpening",
    "FilteredPupilDiameter", "FilteredLeftPupilDiameter", "FilteredRightPupilDiameter",
]


def _smarteye_has_header(first_line: str) -> bool:
    first_field = first_line.split("\t", 1)[0].strip()
    return not first_field.lstrip("-").isdigit()


def _is_smarteye_raw(raw: str) -> bool:
    if not raw:
        return False
    first_line = raw.splitlines()[0]
    if "\t" not in first_line:
        return False
    if "RealTimeClock" in first_line:
        return True
    # headerless export: only trust this if the column count matches the last known field selection exactly       
    fields = first_line.split("\t")
    return not _smarteye_has_header(first_line) and len(fields) == len(_SMARTEYE_KNOWN_HEADER)


def _smarteye_filetime_to_iso(ticks: str) -> str:
    unix_100ns = int(ticks) - _FILETIME_EPOCH_DELTA
    dt = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=unix_100ns / 10)
    return dt.isoformat()


def _smarteye_reader(raw: str) -> csv.DictReader:
    lines = raw.splitlines()
    first_line = lines[0] if lines else ""
    if first_line and not _smarteye_has_header(first_line):
        fields = first_line.split("\t")
        if len(fields) != len(_SMARTEYE_KNOWN_HEADER):
            raise ValueError(
                f"Smart Eye export has no header row and {len(fields)} columns, "
                f"which doesn't match the last known field selection "
                f"({len(_SMARTEYE_KNOWN_HEADER)} columns) -- can't safely guess "
                f"column order. Re-export with 'write header row' enabled."
            )
        return csv.DictReader(io.StringIO(raw), fieldnames=_SMARTEYE_KNOWN_HEADER, delimiter="\t")
    return csv.DictReader(io.StringIO(raw), delimiter="\t")


def _smarteye_rows(raw: str):
    """Reshapes a raw Smart Eye export into EYE_COLUMNS-keyed string dicts,
    so the rest of ingest_eye's coercion logic runs unchanged."""
    for row in _smarteye_reader(raw):
        out = {"ts": _smarteye_filetime_to_iso(row["RealTimeClock"])}
        for out_col, src_col in _SMARTEYE_DIRECT.items():
            out[out_col] = row.get(src_col)
        for out_col, src_col in _SMARTEYE_MM.items():
            v = row.get(src_col)
            out[out_col] = str(float(v) * 1000) if v not in (None, "") else None
        for out_col, src_col in _SMARTEYE_DEG.items():
            v = row.get(src_col)
            out[out_col] = str(math.degrees(float(v))) if v not in (None, "") else None
        # Blink is a blink-event id counter (0 = not blinking)
        blink_raw = row.get("Blink")
        out["blink"] = "0" if blink_raw in (None, "", "0") else "1"
        extra = {k: v for k in _SMARTEYE_EXTRA
                 if (v := _to_float(row.get(k))) is not None and math.isfinite(v)}   # NaN isn't valid jsonb
        out["extra"] = json.dumps(extra) if extra else None
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


def _to_json(v: str | None):
    """A JSON object as text (jsonb takes text), or None; anything else is dropped."""
    if not v:
        return None
    try:
        return v if isinstance(json.loads(v), dict) else None
    except ValueError:
        return None


def _to_int(v: str | None):
    f = _to_float(v)
    return int(f) if f is not None else None


def _to_ts(v: str) -> datetime:
    # LiveAdapter.py writes datetime.now(timezone.utc).isoformat()
    return datetime.fromisoformat(v)


async def _session_exists(session_id: UUID) -> bool:
    async with db.acquire() as conn:
        return await conn.fetchval("select 1 from sessions where id = $1", session_id) is not None


def _decode_upload(raw_bytes: bytes) -> str:
    if raw_bytes.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw_bytes.decode("utf-16")
    return raw_bytes.decode("utf-8-sig")


async def _read_csv(file: UploadFile) -> csv.DictReader:
    raw = _decode_upload(await file.read())
    return csv.DictReader(io.StringIO(raw))


async def _read_eye_rows(file: UploadFile):
    """-> (rows, is_smart_eye_export)."""
    raw = _decode_upload(await file.read())
    if _is_smarteye_raw(raw):
        return _smarteye_rows(raw), True
    return csv.DictReader(io.StringIO(raw)), False


async def _eye_clock_shift(session_id: UUID) -> float:
    """Seconds to add to a Smart Eye export's timestamps: the clock offset the
    live adapter measured for this session (app/eye_clock.py), so the full
    file lines up with the flight data exactly like the corrected live feed.
    0 when it wasn't measured or the live feed wasn't corrected either."""
    try:
        async with db.acquire() as conn:
            row = await conn.fetchrow("select eye_clock_offset_s, eye_clock_corrected from sessions where id=$1",
                                      session_id)
    except Exception:          # eye-clock columns not added yet
        return 0.0
    if row and row["eye_clock_corrected"] and row["eye_clock_offset_s"] is not None:
        return float(row["eye_clock_offset_s"])
    return 0.0


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

    inserted = await db.replace_session_rows("flight_data", session_id, db.FLIGHT_COLUMNS, records)
    await _refresh_session_bounds(session_id)
    return IngestResult(inserted=inserted, skipped=skipped)


# ---- eye tracking ---------------------------------------------------
@router.post("/eye", response_model=IngestResult)
async def ingest_eye(session_id: UUID, file: UploadFile):
    if not await _session_exists(session_id):
        raise HTTPException(404, "Session not found")

    reader, is_smart_eye = await _read_eye_rows(file)
    shift = timedelta(seconds=await _eye_clock_shift(session_id)) if is_smart_eye else timedelta(0)
    records, skipped = [], 0
    for r in reader:
        ts_raw = r.get("ts") or r.get("timestamp_utc")
        if not ts_raw:
            skipped += 1
            continue
        rec = [session_id, _to_ts(ts_raw) + shift]
        for col in db.EYE_COLUMNS[2:]:
            val = r.get(col)
            if col in EYE_BOOL:
                rec.append(_to_bool(val))
            elif col in EYE_INT:
                rec.append(_to_int(val))
            elif col in EYE_TEXT:
                rec.append(val or None)
            elif col in EYE_JSON:
                rec.append(_to_json(val))
            else:
                rec.append(_to_float(val))
        records.append(tuple(rec))

    inserted = await db.replace_session_rows("eye_tracking_data", session_id, db.EYE_COLUMNS, records)
    note = (f"Timestamps shifted by {shift.total_seconds():+.3f} s to match the flight data "
            f"(the eye tracker's clock offset measured live for this session)." if shift else None)
    return IngestResult(inserted=inserted, skipped=skipped, message=note)


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
