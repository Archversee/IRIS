"""
Smart Eye Pro (SEP) live gaze bridge.

Run this ON THE SIM/BACKEND DEVICE (the same one running MSFSAdapter.py,
the backend and the React app) -- NOT on the Smart Eye machine. SEP pushes
its raw UDP feed straight across the network to this device; nothing needs
installing on the Smart Eye machine besides SEP itself. It:
    1. Optionally talks to SEP's JSON-RPC server (Remote Control module,
       TCP port 8100 by default, on the Smart Eye machine) to ask it to
       open a UDP data stream -- OFF by default (USE_RPC_TO_OPEN_STREAM
       below), because SEP's own UI has a Communication Settings panel
       that configures the exact same UDP destination + field selection
       directly and keeps it on persistently. If that's already set up
       (destination host/port visible in SEP's settings), you don't need
       the RPC path at all -- just make sure UDP_LISTEN_PORT below
       matches the port shown there, and that the field selection next
       to it (its own Output Data checklist for network streaming)
       includes the fields this script parses (see FIELD_SELECTION
       below). Flip the flag on only if you'd rather have this script
       itself turn the stream on remotely.
    2. Receives and parses that raw binary UDP feed (see "Smart Eye Data
       Communication" in the Programmer's Guide) into the same field
       shape already used by the file-based importer (see the SMARTEYE_*
       mappings in backend/app/routers/ingest.py) -- so a live sample and
       a post-session-imported row mean the same thing.
    3. Forwards each parsed sample to the backend over a WebSocket. Since
       this script now runs alongside the backend, that's just localhost
       -- see stream.py for the same in-memory-only, no-DB-during-flight
       design MSFSAdapter.py's flight stream already uses.

This script is a *preview* feed only. It does not replace the normal
Smart Eye file export + Upload-page import -- that's still how the
durable, full-resolution eye log gets into the database after the
flight. If this script never runs, or drops out mid-flight, nothing
about post-session review is affected.

SETUP:
    On the Smart Eye machine:
    1. SEP needs the "Remote Control" optional module licensed only if
       you plan to flip USE_RPC_TO_OPEN_STREAM on below -- otherwise
       skip it, the Communication Settings UDP push doesn't need it.
    2. In SEP's Communication Settings, set the UDP destination host to
       THIS device's LAN IP (ipconfig on this machine -> IPv4 Address)
       and a port of your choice -- match that port to UDP_LISTEN_PORT
       below. Check that its field/Output Data selection for the network
       stream includes (at least) the names in FIELD_SELECTION below;
       anything requested here but not actually sent just comes through
       as None, it won't error.
    3. Make sure Windows' own time sync is on (Settings > Time & Language
       > Date & Time > "Set time automatically") on BOTH machines. Smart
       Eye's RealTimeClock output is real UTC wall-clock time, the exact
       same timestamp source the file importer already uses -- there is
       no extra sync step to build, this only works if both machines'
       OS clocks already agree.

    On this device (sim/backend):
    4. pip install websocket-client   (same dep as MSFSAdapter.py)
    5. Allow inbound UDP on UDP_LISTEN_PORT through Windows Firewall --
       these packets are now genuinely arriving from another machine on
       the network, not loopback.
    6. Open the app's Live page, click "Go Live", copy the session id it
       shows into SESSION_ID below (the same id you also paste into
       MSFSAdapter.py -- both scripts feed the same live session).
    7. Start SEP and get it tracking on the Smart Eye machine, THEN run
       this script here.
"""

import json
import math
import queue
import socket
import struct
import threading
import time
from datetime import datetime, timedelta, timezone

import websocket

# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------
API_BASE_URL = "http://localhost:8000"  # this script now runs alongside the backend
WS_BASE_URL = API_BASE_URL.replace("http://", "ws://").replace("https://", "wss://")
SESSION_ID = None  # paste the id from the Live page's "Go Live" here

USE_RPC_TO_OPEN_STREAM = False  # see note above -- leave False if SEP's own Communication Settings already has a UDP destination configured
SEP_RPC_HOST = "<smarteye-device-lan-ip>"  # only used if USE_RPC_TO_OPEN_STREAM is True -- SEP's Remote Control server lives on the OTHER (Smart Eye) machine now
SEP_RPC_PORT = 8100

UDP_LISTEN_PORT = 5001  # must match the port set as the destination in SEP's Communication Settings

# ----------------------------------------------------------------------
# Smart Eye output data ids we ask for (Programmer's Guide, Appendix A) --
# chosen to match the existing SMARTEYE_* mapping in ingest.py exactly,
# with one deliberate substitution: the file exporter's "FilteredGazeDirectionQ"
# column has no equivalent id in the network catalogue, so we request the
# unfiltered "GazeDirectionQ" (id 0x0022) for the live gaze_quality field
# instead -- same underlying quality metric, just not smoothed.
# ----------------------------------------------------------------------
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


# ----------------------------------------------------------------------
# JSON-RPC client (netstring-framed, per "Transportation layer" in the
# Programmer's Guide) -- only ever used once at startup to open the
# stream, so no need for a persistent connection or a library.
# ----------------------------------------------------------------------
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
    # "" tells SEP to default the destination to whatever IP it sees this
    # RPC connection coming from -- since we're the ones dialing in to the
    # Smart Eye machine, that's automatically this device's own LAN IP, no
    # need to hardcode/guess it.
    print(f"Asking SEP to open a UDP stream -> this device:{UDP_LISTEN_PORT}")
    _rpc_call("openDataStreamUDP", ["", UDP_LISTEN_PORT, FIELD_SELECTION])


def close_smarteye_stream():
    try:
        _rpc_call("closeDataStreamUDP", ["", UDP_LISTEN_PORT])
    except Exception as e:
        print(f"Couldn't cleanly close the SEP UDP stream (harmless): {e}")


# ----------------------------------------------------------------------
# Packet parsing -- Programmer's Guide Table 3.1 (structure) and 3.2
# (types). Everything is big-endian ("network byte order").
# ----------------------------------------------------------------------
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


# ----------------------------------------------------------------------
# Forwarding to the backend -- identical reconnect/keepalive pattern to
# MSFSAdapter.py's _stream_worker (WebSocketApp's run_forever() handles
# ping/pong; sender only starts once the connection is actually open).
# ----------------------------------------------------------------------
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


def main():
    if not SESSION_ID:
        raise SystemExit("Set SESSION_ID before running (copy it from the Live page's 'Go Live' step).")

    if USE_RPC_TO_OPEN_STREAM:
        open_smarteye_stream()
    threading.Thread(target=_forward_worker, daemon=True).start()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", UDP_LISTEN_PORT))
    print(f"Listening for Smart Eye UDP packets on :{UDP_LISTEN_PORT}. Ctrl+C to stop.")

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
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        sock.close()
        if USE_RPC_TO_OPEN_STREAM:
            close_smarteye_stream()


if __name__ == "__main__":
    main()
