"""
Combined live data collector: MSFS flight telemetry + Smart Eye Pro (SEP)
gaze tracking + OBS recording, all in one process/one SESSION_ID, run on
the sim/backend device.

INSTALL:
    pip install pysimconnect obsws-python requests websocket-client

SESSION ID:
    Normally you don't set this by hand -- clicking "Go Live" on the app's
    Live page creates the session AND launches this script for you with the
    session id already attached (see backend's POST /sessions/{id}/launch-adapter).
    If you're running this by hand instead (script and app on different
    machines, or the auto-launch didn't fire), pass the id as an argument:
        python LiveAdapter.py <session-id>
    or paste it into SESSION_ID below. Leave both empty to just log CSV
    locally with no live streaming.

OBS RECORDING (two instances, one mp4 each -- instrument view + OTW view):
    Run two separate OBS Studio processes. In each instance:
        Tools > WebSocket Server Settings > Enable WebSocket server
        Leave "Enable Authentication" UNCHECKED -- this script connects
        with no password, so OBS must not be asking for one.
    Set OBS_INSTANCES below to match each instance's host/port.

SMART EYE (on the Smart Eye machine, separate from this one):
    1. In SEP's Communication Settings, set the UDP destination host to
       THIS device's LAN IP and a port matching UDP_LISTEN_PORT below.
       Its Output Data selection for the network stream must include (at
       least) the names in FIELD_SELECTION below.
    2. Windows time sync must be on (Settings > Time & Language > Date &
       Time > "Set time automatically") on BOTH machines -- SEP's
       RealTimeClock output is real UTC wall-clock time.
    On this device: allow inbound UDP on UDP_LISTEN_PORT through Windows
    Firewall, since these packets now arrive from another machine.
    Eye tracking is a best-effort live preview only -- it does not replace
    the normal SEP file export + Upload-page import, which is still how
    the durable, full-resolution eye log gets into the database. If SEP
    isn't running or the feed drops, nothing about post-session review is
    affected.

Run this AFTER MSFS2020 is running with a flight loaded, both OBS
instances are open with their WebSocket servers enabled, and (if using
Smart Eye) SEP is running and tracking.

SHUTDOWN: Ctrl+C stops the flight side the normal clean way -- SimConnect
closes, OBS recordings stop, the full CSV log gets batch-uploaded to
Supabase. The eye-tracking side is best-effort and just stops with the
process.
"""

import csv
import json
import math
import os
import queue
import socket
import struct
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import requests
import websocket
from simconnect import SimConnect, PERIOD_VISUAL_FRAME
import obsws_python as obsws

# ----------------------------------------------------------------------
# Shared config
# ----------------------------------------------------------------------
API_BASE_URL = "http://localhost:8000"
WS_BASE_URL = API_BASE_URL.replace("http://", "ws://").replace("https://", "wss://")

# Auto-filled by the backend when launched from the Live page's "Go Live"
# button (python LiveAdapter.py <session-id>). Set manually only for a
# standalone run -- leave both this and the CLI arg empty to skip live
# streaming and just log CSV.
SESSION_ID = sys.argv[1] if len(sys.argv) > 1 else None


# ========================================================================
# Flight telemetry (MSFS / SimConnect)
# ========================================================================
POLL_INTERVAL_SEC = 0.01665  # 60hz

LOG_DIR = os.path.join(os.getcwd(), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
OUTPUT_CSV = os.path.join(LOG_DIR, "simconnect_log.csv")

# Set False to log flight data only, without touching OBS.
ENABLE_OBS_RECORDING = True

# One entry per OBS instance, it doesn't need to match anything in OBS.
# No password -- make sure each instance has "Enable Authentication"
# unchecked in Tools > WebSocket Server Settings.
OBS_INSTANCES = [
    dict(name="instrument", host="localhost", port=4456),
    dict(name="otw",        host="localhost", port=4455),
]
# OBS vidoe file sizes: 15 min:1gb @720p

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


# Live streaming -- runs on its own thread so a slow/unreachable backend
# can never stall the actual data-capture loop. The backend only holds
# these in memory (see stream.py) and relays them to the Live page; it
# never touches the DB per-sample, so there's no per-row round trip to
# keep up with and no need to throttle -- every row streams at full rate.
# A dropped or failed sample is silently skipped rather than interrupting
# logging; the CSV (written regardless) stays the source of truth and
# gets batch-uploaded in _flush_full_log_to_supabase() once the flight ends.
_stream_queue = queue.Queue(maxsize=1000)


def _stream_worker():
    # WebSocketApp is websocket-client's supported pattern for a long-lived
    # connection that needs both a background receive/ping-pong loop and
    # sends from another thread -- its .send() takes an internal lock
    # specifically so this combination is safe.
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
            threading.Thread(target=sender, args=(wsapp,), daemon=True).start()

        def on_error(wsapp, error):
            print(f"[stream] error: {error}")

        def on_close(wsapp, status_code, msg):
            stop_sender.set()

        wsapp = websocket.WebSocketApp(url, on_open=on_open, on_error=on_error, on_close=on_close)
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
        client = obsws.ReqClient(host=cfg["host"], port=cfg["port"], timeout=5)
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


def flight_main():
    obs_clients = connect_obs_clients() if ENABLE_OBS_RECORDING else []

    if SESSION_ID:
        threading.Thread(target=_stream_worker, daemon=True).start()
        print(f"Live streaming enabled -> {WS_BASE_URL}/sessions/{SESSION_ID}/stream/ws")

    print("Connecting to SimConnect (make sure MSFS2020 is running with a flight loaded)...")
    sc = SimConnect()

    subscribe_list = [
        dict(name=simvar, units=unit) for simvar, unit in
        [SIMVARS[f] for f in SIMVARS]
    ]
    datadef = sc.subscribe_simdata(subscribe_list, period=PERIOD_VISUAL_FRAME)

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


# ========================================================================
# Eye tracking (Smart Eye Pro live gaze bridge)
# ========================================================================
USE_RPC_TO_OPEN_STREAM = False  # leave False if SEP's own Communication Settings already has a UDP destination configured
SEP_RPC_HOST = "<smarteye-device-lan-ip>"  # only used if USE_RPC_TO_OPEN_STREAM is True
SEP_RPC_PORT = 8100

UDP_LISTEN_PORT = 5001  # must match the port set as the destination in SEP's Communication Settings

# Smart Eye output data ids we ask for (Programmer's Guide, Appendix A) --
# chosen to match the existing SMARTEYE_* mapping in ingest.py exactly,
# with one deliberate substitution: the file exporter's "FilteredGazeDirectionQ"
# column has no equivalent id in the network catalogue, so we request the
# unfiltered "GazeDirectionQ" (id 0x0022) for the live gaze_quality field
# instead -- same underlying quality metric, just not smoothed.
FIELD_SELECTION = (
    "RealTimeClock;HeadPosition;HeadHeading;HeadPitch;HeadRoll;"
    "FilteredGazeDirection;FilteredGazeOrigin;Fixation;Blink;"
    "FilteredClosestWorldIntersection;EyelidOpening;"
    "FilteredLeftPupilDiameter;FilteredRightPupilDiameter;GazeDirectionQ"
)

# id -> (field kind, our name) -- field kind drives how the subpacket's
# raw bytes get decoded once isolated by its own declared length
_IDS = {
    0x0009: ("u64", "RealTimeClock"),
    0x0010: ("point3d", "HeadPosition"),
    0x0016: ("f64", "HeadHeading"),
    0x0017: ("f64", "HeadPitch"),
    0x0018: ("f64", "HeadRoll"),
    0x0030: ("vect3d", "FilteredGazeDirection"),
    0x0500: ("point3d", "FilteredGazeOrigin"),
    0x003e: ("u32", "Fixation"),
    0x003f: ("u32", "Blink"),
    0x0041: ("world_intersection", "FilteredClosestWorldIntersection"),
    0x0050: ("f64", "EyelidOpening"),
    0x0068: ("f64", "FilteredLeftPupilDiameter"),
    0x006a: ("f64", "FilteredRightPupilDiameter"),
    0x0022: ("f64", "GazeDirectionQ"),
}

_FILETIME_EPOCH_DELTA = 116444736000000000  # 100ns ticks between 1601-01-01 and 1970-01-01 -- same as ingest.py


def _filetime_to_iso(ticks: int) -> str:
    unix_100ns = ticks - _FILETIME_EPOCH_DELTA
    dt = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=unix_100ns / 10)
    return dt.isoformat()


# JSON-RPC client (netstring-framed, per "Transportation layer" in the
# Programmer's Guide) -- only ever used once at startup to open the
# stream, so no need for a persistent connection or a library.
def _netstring_read(sock: socket.socket) -> bytes:
    length_digits = b""
    while True:
        b = sock.recv(1)
        if not b:
            raise ConnectionError("SEP RPC connection closed while reading response")
        if b == b":":
            break
        length_digits += b
    n = int(length_digits)
    payload = b""
    while len(payload) < n:
        chunk = sock.recv(n - len(payload))
        if not chunk:
            raise ConnectionError("SEP RPC connection closed mid-response")
        payload += chunk
    sock.recv(1)  # trailing ","
    return payload


def _rpc_call(method: str, params: list):
    with socket.create_connection((SEP_RPC_HOST, SEP_RPC_PORT), timeout=5) as sock:
        req = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 0}).encode()
        sock.sendall(f"{len(req)}:".encode() + req + b",")
        resp = json.loads(_netstring_read(sock))
    if "error" in resp:
        raise RuntimeError(f"SEP RPC error calling {method}: {resp['error']}")
    return resp.get("result")


def open_smarteye_stream():
    print(f"Asking SEP to open a UDP stream -> this device:{UDP_LISTEN_PORT}")
    _rpc_call("openDataStreamUDP", ["", UDP_LISTEN_PORT, FIELD_SELECTION])


def close_smarteye_stream():
    try:
        _rpc_call("closeDataStreamUDP", ["", UDP_LISTEN_PORT])
    except Exception as e:
        print(f"Couldn't cleanly close the SEP UDP stream (harmless): {e}")


# Packet parsing -- Programmer's Guide Table 3.1 (structure) and 3.2
# (types). Everything is big-endian ("network byte order").
def _parse_packet(data: bytes) -> dict:
    """Returns {our_field_name: raw_value} for every field in _IDS present in this datagram."""
    fields = {}
    pos = 8  # 8-byte packet header: u32 syncId, u16 packetType, u16 length
    while pos + 4 <= len(data):
        sub_id, sub_len = struct.unpack_from(">HH", data, pos)
        pos += 4
        payload = data[pos:pos + sub_len]
        pos += sub_len

        kind_name = _IDS.get(sub_id)
        if kind_name is None:
            continue  # a field we didn't ask to decode -- skip via sub_len, already advanced past it
        kind, name = kind_name

        if kind == "u32":
            fields[name] = struct.unpack(">I", payload)[0]
        elif kind == "u64":
            fields[name] = struct.unpack(">Q", payload)[0]
        elif kind == "f64":
            fields[name] = struct.unpack(">d", payload)[0]
        elif kind in ("point3d", "vect3d"):
            fields[name] = struct.unpack(">ddd", payload)  # (x, y, z)
        elif kind == "world_intersection":
            exists = struct.unpack_from(">H", payload, 0)[0]
            if not exists:
                fields[name] = None
                continue
            object_point = struct.unpack_from(">ddd", payload, 26)  # skip exists(2) + worldPoint(24)
            name_len = struct.unpack_from(">H", payload, 50)[0]
            object_name = payload[52:52 + name_len].decode("ascii", errors="replace")
            fields[name] = {"objectPoint": object_point, "objectName": object_name}

    return fields


def _build_sample(fields: dict):
    if "RealTimeClock" not in fields:
        return None  # can't timestamp it -- drop rather than guess

    world_ix = fields.get("FilteredClosestWorldIntersection")
    gaze_origin = fields.get("FilteredGazeOrigin")
    gaze_dir = fields.get("FilteredGazeDirection")
    head_pos = fields.get("HeadPosition")

    def deg(v):
        return math.degrees(v) if v is not None else None

    def mm(v):
        return v * 1000 if v is not None else None

    return {
        "ts": _filetime_to_iso(fields["RealTimeClock"]),
        "gaze_origin_x": gaze_origin[0] if gaze_origin else None,
        "gaze_origin_y": gaze_origin[1] if gaze_origin else None,
        "gaze_origin_z": gaze_origin[2] if gaze_origin else None,
        "gaze_dir_x": gaze_dir[0] if gaze_dir else None,
        "gaze_dir_y": gaze_dir[1] if gaze_dir else None,
        "gaze_dir_z": gaze_dir[2] if gaze_dir else None,
        "gaze_point_x": world_ix["objectPoint"][0] if world_ix else None,
        "gaze_point_y": world_ix["objectPoint"][1] if world_ix else None,
        "aoi": world_ix["objectName"] if world_ix else None,
        "pupil_diam_left_mm": mm(fields.get("FilteredLeftPupilDiameter")),
        "pupil_diam_right_mm": mm(fields.get("FilteredRightPupilDiameter")),
        "eyelid_opening_mm": mm(fields.get("EyelidOpening")),
        "blink": bool(fields.get("Blink")),
        "head_pos_x": head_pos[0] if head_pos else None,
        "head_pos_y": head_pos[1] if head_pos else None,
        "head_pos_z": head_pos[2] if head_pos else None,
        "head_heading_deg": deg(fields.get("HeadHeading")),
        "head_pitch_deg": deg(fields.get("HeadPitch")),
        "head_roll_deg": deg(fields.get("HeadRoll")),
        "gaze_quality": fields.get("GazeDirectionQ"),
        "fixation_id": fields.get("Fixation"),
    }


# Forwarding to the backend -- identical reconnect/keepalive pattern to
# _stream_worker above (WebSocketApp's run_forever() handles ping/pong;
# sender only starts once the connection is actually open).
_forward_queue = queue.Queue(maxsize=2000)


def _forward_worker():
    url = f"{WS_BASE_URL}/sessions/{SESSION_ID}/stream/eye/ws"
    while True:
        stop_sender = threading.Event()

        def sender(wsapp):
            while not stop_sender.is_set():
                try:
                    sample = _forward_queue.get(timeout=1)
                except queue.Empty:
                    continue
                try:
                    wsapp.send(json.dumps(sample))
                except Exception:
                    pass  # transient -- on_close will stop this loop once the connection is actually dead

        def on_open(wsapp):
            print("[eye-stream] connected to live dashboard")
            threading.Thread(target=sender, args=(wsapp,), daemon=True).start()

        def on_error(wsapp, error):
            print(f"[eye-stream] error: {error}")

        def on_close(wsapp, status_code, msg):
            stop_sender.set()

        wsapp = websocket.WebSocketApp(url, on_open=on_open, on_error=on_error, on_close=on_close)
        wsapp.run_forever(ping_interval=15, ping_timeout=10)
        stop_sender.set()
        print("[eye-stream] connection lost; retrying in 2s")
        time.sleep(2)


def eye_main():
    """Best-effort eye-tracking bridge -- runs on its own daemon thread from
    main() below, and just stops with the process rather than getting its
    own clean shutdown (see module docstring)."""
    if not SESSION_ID:
        return  # no session to stream to -- eye tracking is live-preview only, nothing to do

    if USE_RPC_TO_OPEN_STREAM:
        open_smarteye_stream()
    threading.Thread(target=_forward_worker, daemon=True).start()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", UDP_LISTEN_PORT))
    print(f"[eye] listening for Smart Eye UDP packets on :{UDP_LISTEN_PORT}")

    try:
        while True:
            data, _addr = sock.recvfrom(65536)
            fields = _parse_packet(data)
            sample = _build_sample(fields)
            if sample is None:
                continue
            try:
                _forward_queue.put_nowait(sample)
            except queue.Full:
                pass  # backend/network can't keep up -- drop rather than pile up unbounded
    except OSError:
        pass  # socket closed out from under us at process exit -- fine, daemon thread
    finally:
        sock.close()
        if USE_RPC_TO_OPEN_STREAM:
            close_smarteye_stream()


# ========================================================================
# Entry point -- flight telemetry drives the main loop and owns the clean
# shutdown (SimConnect close, OBS stop, CSV batch-upload); eye tracking
# runs alongside on a background thread, best-effort (see eye_main()).
# ========================================================================
def main():
    if SESSION_ID:
        threading.Thread(target=eye_main, daemon=True).start()
    else:
        print("No SESSION_ID -- logging flight CSV only, no live streaming, no eye tracking.")

    flight_main()


if __name__ == "__main__":
    main()
