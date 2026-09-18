"""
MSFS2020 SimConnect data logger
Corrected to use pysimconnect's actual documented API:
    sc.subscribe_simdata([...])  -> datadef
    sc.receive()                 -> pumps the SDK message queue
    datadef.simdata[name]        -> latest cached value

INSTALL:
    pip install pysimconnect obsws-python requests websocket-client

OBS SETUP (two instances, one mp4 each — instrument view + OTW view):
    Run two separate OBS Studio processes  In each instance:
        Tools > WebSocket Server Settings > Enable WebSocket server

LIVE STREAMING (flight telemetry only -- eye tracking + video are separate):
    1. Open the app's Live page and click "Go Live" -- this creates a new
       session and shows its id.
    2. Paste that id into SESSION_ID below.
    3. Run this script; the Live page starts showing data as it streams in.
    Leave SESSION_ID as None to skip streaming and just log CSV as before.

    While the flight is in progress, samples go straight to the backend's
    in-memory buffer over a WebSocket -- never the DB -- so there's no
    per-sample network/DB round trip to keep up with. When this script
    exits (Ctrl+C or the sim closes), it uploads the full CSV log to
    Supabase in one batch via the same endpoint the Upload page uses, so
    the flight becomes a normal reviewable session afterward.

Run this AFTER MSFS2020 is running with a flight loaded, and after both
OBS instances are open with the WebSocket server enabled.
"""

import csv
import json
import os
import queue
import threading
import time
from datetime import datetime, timezone

import requests
import websocket
from simconnect import SimConnect, PERIOD_VISUAL_FRAME
import obsws_python as obsws

# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------
#POLL_INTERVAL_SEC = 0.1 #10Hz
#POLL_INTERVAL_SEC = 0.033 #30Hz
POLL_INTERVAL_SEC = 0.01665 #60hz

LOG_DIR = os.path.join(os.getcwd(), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
OUTPUT_CSV = os.path.join(LOG_DIR, "simconnect_log.csv")

# Set False to log flight data only, without touching OBS.
ENABLE_OBS_RECORDING = True

# Live streaming -- see "LIVE STREAMING" note above. CSV logging (the
# durable record) happens either way; this is purely additive.
API_BASE_URL = "http://localhost:8000"
WS_BASE_URL = API_BASE_URL.replace("http://", "ws://").replace("https://", "wss://")
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
# can never stall the actual data-capture loop. The backend only holds
# these in memory (see stream.py) and relays them to the Live page; it
# never touches the DB per-sample, so there's no per-row round trip to
# keep up with and no need to throttle -- every row streams at full rate.
# A dropped or failed sample is silently skipped rather than interrupting
# logging; the CSV (written regardless) stays the source of truth and
# gets batch-uploaded in _flush_full_log_to_supabase() once the flight ends.
# ----------------------------------------------------------------------
_stream_queue = queue.Queue(maxsize=1000)


def _stream_worker():
    # WebSocketApp is websocket-client's supported pattern for a long-lived
    # connection that needs both a background receive/ping-pong loop and
    # sends from another thread -- its .send() takes an internal lock
    # specifically so this combination is safe. (An earlier version of this
    # hand-rolled the same idea with a raw create_connection() socket plus a
    # second thread calling .recv() directly -- that pattern isn't actually
    # synchronized by the library and could corrupt frame parsing under
    # concurrent send/recv, which looks exactly like what was happening:
    # the connection just silently closing every so often for no visible
    # reason on either side.)
    url = f"{WS_BASE_URL}/sessions/{SESSION_ID}/stream/ws"

    while True:
        stop_sender = threading.Event()

        def sender(wsapp):
            while not stop_sender.is_set():
                try:
                    row = _stream_queue.get(timeout=1)
                except queue.Empty:
                    continue
                try:
                    wsapp.send(json.dumps(row))
                except Exception:
                    pass  # transient -- on_close (below) will stop this loop once the connection is actually dead

        def on_open(wsapp):
            print("[stream] connected to live dashboard")
            # start sending only once the socket is confirmed open -- starting
            # this eagerly races run_forever()'s own connect step, and a send
            # attempted before it wins would kill this thread for good even
            # though the connection goes on to succeed right after
            threading.Thread(target=sender, args=(wsapp,), daemon=True).start()

        def on_error(wsapp, error):
            print(f"[stream] error: {error}")

        def on_close(wsapp, status_code, msg):
            stop_sender.set()

        wsapp = websocket.WebSocketApp(url, on_open=on_open, on_error=on_error, on_close=on_close)

        # blocks until the connection closes; sends its own pings and
        # answers the server's, so nothing extra is needed to stay alive
        wsapp.run_forever(ping_interval=15, ping_timeout=10)
        stop_sender.set()
        print("[stream] connection lost; retrying in 2s")
        time.sleep(2)


def stream_row(row):
    if not SESSION_ID:
        return
    payload = {"ts": row["timestamp_utc"], **{k: v for k, v in row.items() if k != "timestamp_utc"}}
    try:
        _stream_queue.put_nowait(payload)
    except queue.Full:
        pass  # backend/network can't keep up -- drop the sample rather than pile up unbounded


def _flush_full_log_to_supabase():
    """Uploads the full CSV log via the same batch-ingest endpoint the Upload
    page uses, so a live-streamed flight becomes a normal reviewable session."""
    if not SESSION_ID:
        return
    print("Uploading full flight log to the database...")
    try:
        with open(OUTPUT_CSV, "rb") as f:
            resp = requests.post(
                f"{API_BASE_URL}/sessions/{SESSION_ID}/ingest/flight",
                files={"file": (os.path.basename(OUTPUT_CSV), f, "text/csv")},
                timeout=60,
            )
        resp.raise_for_status()
        print(f"Uploaded: {resp.json()}")
    except Exception as e:
        print(
            f"Failed to upload the full log ({e}). The CSV at {OUTPUT_CSV} is "
            "still on disk -- upload it manually via the Upload page."
        )


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
        print(f"Live streaming enabled -> {WS_BASE_URL}/sessions/{SESSION_ID}/stream/ws")

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
            _flush_full_log_to_supabase()


if __name__ == "__main__":
    main()