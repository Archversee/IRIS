"""
MSFS2020 SimConnect data logger
Corrected to use pysimconnect's actual documented API:
    sc.subscribe_simdata([...])  -> datadef
    sc.receive()                 -> pumps the SDK message queue
    datadef.simdata[name]        -> latest cached value

INSTALL:
    pip install pysimconnect obsws-python requests

OBS SETUP (two instances, one mp4 each — instrument view + OTW view):
    Run two separate OBS Studio processes  In each instance:
        Tools > WebSocket Server Settings > Enable WebSocket server

LIVE STREAMING (stage 1 of real-time -- flight telemetry only):
    1. Open the app's Live page and click "Go Live" -- this creates a new
       session and shows its id.
    2. Paste that id into SESSION_ID below.
    3. Run this script; the Live page starts showing data as it streams in.
    Leave SESSION_ID as None to skip streaming and just log CSV as before.

Run this AFTER MSFS2020 is running with a flight loaded, and after both
OBS instances are open with the WebSocket server enabled.
"""

import csv
import os
import queue
import threading
import time
from datetime import datetime, timezone

import requests
from simconnect import SimConnect, PERIOD_VISUAL_FRAME
import obsws_python as obsws

# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------
#POLL_INTERVAL_SEC = 0.1 #10Hz
POLL_INTERVAL_SEC = 0.033 #30Hz
#POLL_INTERVAL_SEC = 0.01665 #60hz

LOG_DIR = os.path.join(os.getcwd(), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
OUTPUT_CSV = os.path.join(LOG_DIR, "simconnect_log.csv")

# Set False to log flight data only, without touching OBS.
ENABLE_OBS_RECORDING = True

# Live streaming -- see "LIVE STREAMING" note above. CSV logging (the
# durable record) happens either way; this is purely additive.
API_BASE_URL = "http://localhost:8000"
SESSION_ID = None  # e.g. "3f9c1a2b-4d5e-4f6a-8b9c-0d1e2f3a4b5c"

# One entry per OBS instance, it doesn't need to match anything in OBS.
OBS_INSTANCES = [
    dict(name="instrument", host="localhost", port=4456, password=""),
    dict(name="otw",        host="localhost", port=4455, password=""),
]

# Canonical field -> (simvar name, unit)
SIMVARS = {
    "altitude_ft":            ("PLANE ALTITUDE", "feet"),
    "airspeed_kt":             ("AIRSPEED INDICATED", "knots"),
    "vertical_speed_fpm":      ("VERTICAL SPEED", "feet/minute"),
    "heading_true_deg":        ("PLANE HEADING DEGREES TRUE", "degrees"),
    "pitch_deg":                ("PLANE PITCH DEGREES", "degrees"),
    "bank_deg":                 ("PLANE BANK DEGREES", "degrees"),
    "yaw_rate_radps":          ("ROTATION VELOCITY BODY Z", "Radians per second"),
    "fuel_total_qty_gal":      ("FUEL TOTAL QUANTITY", "gallons"),

    "throttle_pct":             ("GENERAL ENG THROTTLE LEVER POSITION:1", "Percent"),
    "elevator_trim_pct":       ("ELEVATOR TRIM PCT", "Percent"),
    "elevator_position":       ("ELEVATOR POSITION", "Position"),
    "rudder_position":          ("RUDDER POSITION", "Position"),
    "flaps_handle_pct":        ("FLAPS HANDLE PERCENT", "Percent"),
    "gear_handle_position":    ("GEAR HANDLE POSITION", "bool"),

    "latitude":                  ("PLANE LATITUDE", "degrees"),
    "longitude":                 ("PLANE LONGITUDE", "degrees"),
    "ambient_visibility_m":    ("AMBIENT VISIBILITY", "meters"),
    "ambient_wind_kt":          ("AMBIENT WIND VELOCITY", "knots"),
    "ambient_temp_c":           ("AMBIENT TEMPERATURE", "celsius"),
}

# Map simvar name -> canonical field name, for pulling values back out
NAME_TO_FIELD = {simvar: field for field, (simvar, _) in SIMVARS.items()}


# ----------------------------------------------------------------------
# Live streaming -- runs on its own thread so a slow/unreachable backend
# can never stall the actual data-capture loop. Best-effort: a dropped
# or failed sample is silently skipped rather than interrupting logging.
# ----------------------------------------------------------------------
_stream_queue = queue.Queue(maxsize=1000)


def _stream_worker():
    while True:
        row = _stream_queue.get()
        try:
            requests.post(
                f"{API_BASE_URL}/sessions/{SESSION_ID}/stream/flight",
                json=row, timeout=1,
            )
        except Exception:
            pass


def stream_row(row):
    if not SESSION_ID:
        return
    payload = {"ts": row["timestamp_utc"], **{k: v for k, v in row.items() if k != "timestamp_utc"}}
    try:
        _stream_queue.put_nowait(payload)
    except queue.Full:
        pass  # backend can't keep up -- drop the sample rather than pile up unbounded


def connect_obs_clients():
    clients = []
    for cfg in OBS_INSTANCES:
        print(f"Connecting to OBS ({cfg['name']}) at {cfg['host']}:{cfg['port']}...")
        client = obsws.ReqClient(
            host=cfg["host"], port=cfg["port"], password=cfg["password"], timeout=5
        )
        clients.append((cfg["name"], client))
    return clients


def start_obs_recordings(clients):
    for name, client in clients:
        client.start_record()
        print(f"OBS ({name}) recording started")


def stop_obs_recordings(clients):
    for name, client in clients:
        try:
            resp = client.stop_record()
            print(f"OBS ({name}) saved recording to {resp.output_path}")
        except Exception as e:
            print(f"Failed to stop OBS ({name}) recording cleanly: {e}")


def main():
    obs_clients = connect_obs_clients() if ENABLE_OBS_RECORDING else []

    if SESSION_ID:
        threading.Thread(target=_stream_worker, daemon=True).start()
        print(f"Live streaming enabled -> {API_BASE_URL}/sessions/{SESSION_ID}/stream/flight")

    print("Connecting to SimConnect (make sure MSFS2020 is running with a flight loaded)...")
    sc = SimConnect()

    # Subscribe once to all variables
    subscribe_list = [
        dict(name=simvar, units=unit) for simvar, unit in
        [SIMVARS[f] for f in SIMVARS]
    ]
    datadef = sc.subscribe_simdata(subscribe_list, period=PERIOD_VISUAL_FRAME)

    # Wait for the connection handshake + first data batch to land
    print("Waiting for first data batch...")
    while not datadef.simdata:
        sc.receive()
        time.sleep(0.05)
    print("Connected, data flowing.")

    if obs_clients:
        start_obs_recordings(obs_clients)

    fieldnames = ["timestamp_utc"] + list(SIMVARS.keys())

    with open(OUTPUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        print(f"Logging to {OUTPUT_CSV}")
        print(f"Rate: {1 / POLL_INTERVAL_SEC:.0f} Hz. Ctrl+C to stop.")
        try:
            while True:
                # Pump the SDK's message queue so datadef.simdata refreshes
                while sc.receive():
                    pass

                row = {"timestamp_utc": datetime.now(timezone.utc).isoformat()}
                for field, (simvar, _unit) in SIMVARS.items():
                    try:
                        row[field] = datadef.simdata[simvar]
                    except KeyError as e:
                        print(f"FAILED: {field} ({simvar}) -> {e}")
                        row[field] = None

                writer.writerow(row)
                f.flush()
                stream_row(row)
                time.sleep(POLL_INTERVAL_SEC)
        except KeyboardInterrupt:
            print("\nStopped logging.")
        finally:
            sc.Close()
            if obs_clients:
                stop_obs_recordings(obs_clients)


if __name__ == "__main__":
    main()