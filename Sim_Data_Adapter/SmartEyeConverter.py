"""
Converts Smart Eye "Output Data" export into a CSV shape /sessions/{id}/ingest/eye endpoint expects (see
backend/app/routers/ingest.py and EYE_COLUMNS in backend/app/db.py).

ClosestWorldIntersection.objectName is used as the AOI label ( e.g. "OTW", or an instrument panel
region name if your Smart Eye scene/calibration defines one).
"""
import csv
import math
import sys
from datetime import datetime, timedelta, timezone

FILETIME_EPOCH_DELTA = 116444736000000000  


def filetime_to_iso(ticks: str) -> str:
    unix_100ns = int(ticks) - FILETIME_EPOCH_DELTA
    dt = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=unix_100ns / 10)
    return dt.isoformat()


# output column -> source column
DIRECT_MAP = {
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

# output column -> source column, meters -> millimeters
MM_MAP = {
    "pupil_diam_left_mm": "FilteredLeftPupilDiameter",
    "pupil_diam_right_mm": "FilteredRightPupilDiameter",
    "eyelid_opening_mm": "EyelidOpening",
}

# output column -> source column, radians -> degrees
DEG_MAP = {
    "head_heading_deg": "HeadHeading",
    "head_pitch_deg": "HeadPitch",
    "head_roll_deg": "HeadRoll",
}

OUT_FIELDS = ["ts"] + list(DIRECT_MAP) + list(MM_MAP) + list(DEG_MAP) + ["blink"]


def convert(in_path: str, out_path: str) -> int:
    with open(in_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter="\t")
        with open(out_path, "w", newline="") as out:
            writer = csv.DictWriter(out, fieldnames=OUT_FIELDS)
            writer.writeheader()
            n = 0
            for row in reader:
                out_row = {"ts": filetime_to_iso(row["RealTimeClock"])}
                for out_col, src_col in DIRECT_MAP.items():
                    out_row[out_col] = row.get(src_col)
                for out_col, src_col in MM_MAP.items():
                    v = row.get(src_col)
                    out_row[out_col] = float(v) * 1000 if v not in (None, "") else None
                for out_col, src_col in DEG_MAP.items():
                    v = row.get(src_col)
                    out_row[out_col] = math.degrees(float(v)) if v not in (None, "") else None
                # Blink is a blink-event id counter
                blink_raw = row.get("Blink")
                out_row["blink"] = 0 if blink_raw in (None, "", "0") else 1
                writer.writerow(out_row)
                n += 1
    return n


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python SmartEyeConverter.py input.log output.csv")
        sys.exit(1)
    count = convert(sys.argv[1], sys.argv[2])
    print(f"Wrote {count} rows to {sys.argv[2]}")
