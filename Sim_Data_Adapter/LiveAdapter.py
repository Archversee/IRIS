"""
Live adapter: OBS recording control + Smart Eye Pro (SEP) live gaze bridge,
synced to whichever session MAVIS is currently running.

MAVIS is the only process that ever talks to SimConnect (hard constraint --
see docs/INTEGRATION_PLAN.md). This script no longer reads flight telemetry
itself; it watches MAVIS's own WS state feed (MAVIS_WS_URL below) purely for
the session_id MAVIS mints when the trainee presses Start, and uses that as
the trigger to start OBS recording and attach the Smart Eye bridge to the
right session. Flight telemetry reaches IRIS via a separate path entirely
(IRIS backend's app/mavis_bridge.py, also watching MAVIS's WS) -- this
script has nothing to do with flight_data anymore.

Runs continuously across MANY flights, not just one: MAVIS mints a fresh
session_id on every Start (Basic Flight, Free Flight, whichever), and
session_watcher() below notices each new one and treats it as "the
previous flight (if any) just ended" -- stopping the old OBS recording and
starting a new one, and retargeting the eye bridge's forward socket at the
new session, all without needing this process restarted between flights.
It's launched once per boot now (by the launcher script, or automatically
by IRIS backend's app/mavis_bridge.py the first time it sees a session),
not once per flight.

INSTALL:
    pip install obsws-python websocket-client

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

Run this AFTER MAVIS is running (it supplies the session_id this script
waits for), and after both OBS instances are open with their WebSocket
servers enabled, and (if using Smart Eye) SEP is running and tracking.

SHUTDOWN: Ctrl+C stops OBS recording cleanly. The eye-tracking bridge is
best-effort and just stops with the process.
"""

import json
import math
import os
import queue
import socket
import struct
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone

import websocket
import obsws_python as obsws

# ----------------------------------------------------------------------
# Shared config
# ----------------------------------------------------------------------
API_BASE_URL = "http://localhost:8000"  # IRIS backend -- eye samples go here
WS_BASE_URL = API_BASE_URL.replace("http://", "ws://").replace("https://", "wss://")
MAVIS_WS_URL = "ws://localhost:6945/ws"  # MAVIS -- source of the session_id this script waits for

# Updated by session_watcher() every time MAVIS mints a new session_id --
# read by _forward_worker() (eye bridge) to know which session to forward
# to; main() also reads it right after pulling a fresh id off _session_queue.
SESSION_ID: str | None = None

# One entry per newly-seen session_id, in order -- main() consumes these to
# know when to stop the previous flight's OBS recording and start the next
# one. Kept small (unbounded but sessions are minted at human speed, not a
# stream) since nothing ever needs to see more than the next one.
_session_queue: "queue.Queue[str]" = queue.Queue()


# ========================================================================
# MAVIS session watcher -- the only thing that decides when to start/roll
# over to a new flight. Runs for the lifetime of the process on its own
# thread (started from main()), reconnecting if MAVIS isn't up yet.
# ========================================================================
def session_watcher() -> None:
    print(f"Waiting for MAVIS to start a session ({MAVIS_WS_URL})...")
    last_sid = None
    while True:
        try:
            ws = websocket.create_connection(MAVIS_WS_URL, timeout=10)
        except Exception as e:
            print(f"Can't reach MAVIS yet ({e}); retrying in 2s")
            time.sleep(2)
            continue
        try:
            while True:
                msg = ws.recv()
                if not msg:
                    break
                try:
                    state = json.loads(msg)
                except (TypeError, ValueError):
                    continue
                sid = state.get("session_id")
                if sid and sid != last_sid:
                    last_sid = sid
                    _session_queue.put(sid)
        except Exception as e:
            print(f"Lost connection to MAVIS ({e}); retrying")
        finally:
            try:
                ws.close()
            except Exception:
                pass
        time.sleep(2)


# ========================================================================
# OBS recording control
# ========================================================================
# Set False to skip OBS entirely and just run the Smart Eye bridge.
ENABLE_OBS_RECORDING = True

# One entry per OBS instance, it doesn't need to match anything in OBS.
# No password -- make sure each instance has "Enable Authentication"
# unchecked in Tools > WebSocket Server Settings.
OBS_INSTANCES = [
    dict(name="instrument", host="localhost", port=4456),
    dict(name="otw",        host="localhost", port=4455),
]


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


# ========================================================================
# Eye tracking (Smart Eye Pro live gaze bridge) -- unchanged from before;
# SESSION_ID is now set by wait_for_mavis_session() instead of argv.
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


# Forwarding to the backend -- WebSocketApp's run_forever() handles
# ping/pong; sender only starts once the connection is actually open.
_forward_queue = queue.Queue(maxsize=2000)

# The live wsapp instance, so main() can force it closed on a session
# transition -- that's what makes _forward_worker's reconnect below pick up
# the new SESSION_ID instead of continuing to stream to the flight that just
# ended.
_current_wsapp = None
_wsapp_lock = threading.Lock()


def _forward_worker():
    global _current_wsapp
    while True:
        sid = SESSION_ID  # snapshot -- may change again while this connection is up
        url = f"{WS_BASE_URL}/sessions/{sid}/stream/eye/ws"
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
            print(f"[eye-stream] connected to live dashboard ({sid})")
            threading.Thread(target=sender, args=(wsapp,), daemon=True).start()

        def on_error(wsapp, error):
            print(f"[eye-stream] error: {error}")

        def on_close(wsapp, status_code, msg):
            stop_sender.set()

        wsapp = websocket.WebSocketApp(url, on_open=on_open, on_error=on_error, on_close=on_close)
        with _wsapp_lock:
            _current_wsapp = wsapp
        wsapp.run_forever(ping_interval=15, ping_timeout=10)
        stop_sender.set()
        if SESSION_ID == sid:
            # Dropped on its own (network blip) -- reconnect to the same session.
            print("[eye-stream] connection lost; retrying in 2s")
            time.sleep(2)
        # else: main() closed this on purpose because a new session started --
        # loop straight back around and pick up the new SESSION_ID, no delay.


def eye_main():
    """Best-effort eye-tracking bridge -- runs on its own daemon thread from
    main() below, and just stops with the process rather than getting its
    own clean shutdown (see module docstring)."""
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
# Single-instance lock -- this script can now be started two ways (the
# launcher script, or IRIS backend's app/mavis_bridge.py auto-launching it
# the first time it sees a MAVIS session) that don't know about each other.
# A stale lock (process no longer alive) is treated as free -- good enough
# for a single sim PC, not a distributed lock. If already running, this
# instance prints why and exits immediately rather than fighting the other
# one for the same OBS/UDP-port resources. One lock for the whole process
# lifetime now, not per flight -- see module docstring.
# ========================================================================
LOCK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "adapter.lock")


def _is_pid_running(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True)
        return str(pid) in out.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _acquire_lock() -> bool:
    os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
    if os.path.exists(LOCK_PATH):
        try:
            with open(LOCK_PATH) as f:
                pid = int(f.read().strip())
            if _is_pid_running(pid):
                print(f"Another adapter instance is already running (pid {pid}). Exiting.")
                return False
        except (OSError, ValueError):
            pass  # unreadable/corrupt lock file -- treat as stale, safe to take over
    with open(LOCK_PATH, "w") as f:
        f.write(str(os.getpid()))
    return True


def _release_lock() -> None:
    try:
        os.remove(LOCK_PATH)
    except OSError:
        pass


# ========================================================================
# Entry point -- runs for the whole boot, not just one flight. Waits for
# MAVIS to mint a session, starts OBS + the eye-tracking bridge for it,
# then waits for either the NEXT session (stop this recording, start the
# next one, retarget the eye bridge) or Ctrl+C (stop cleanly and exit).
# ========================================================================
def main():
    if not _acquire_lock():
        return
    global SESSION_ID
    obs_clients: list = []
    eye_bridge_started = False
    try:
        threading.Thread(target=session_watcher, daemon=True).start()
        pending_sid = _session_queue.get()  # blocks for the first session of this boot
        while True:
            SESSION_ID = pending_sid
            print(f"MAVIS session started: {SESSION_ID}")

            if ENABLE_OBS_RECORDING and not obs_clients:
                obs_clients = connect_obs_clients()
            if obs_clients:
                start_obs_recordings(obs_clients)

            if not eye_bridge_started:
                threading.Thread(target=eye_main, daemon=True).start()
                eye_bridge_started = True
            else:
                # Force the eye forwarder's socket closed so it reconnects
                # against the SESSION_ID just set above, instead of
                # continuing to stream to the flight that just ended.
                with _wsapp_lock:
                    if _current_wsapp is not None:
                        try:
                            _current_wsapp.close()
                        except Exception:
                            pass

            print("Recording. Ctrl+C to stop the adapter entirely (fine to leave running between flights).")
            pending_sid = _session_queue.get()  # blocks until the NEXT session starts
            print(f"New MAVIS session detected -- ending recording for {SESSION_ID}")
            if obs_clients:
                stop_obs_recordings(obs_clients)
    except KeyboardInterrupt:
        print("\nStopping...")
        if obs_clients:
            stop_obs_recordings(obs_clients)
    finally:
        _release_lock()


if __name__ == "__main__":
    main()
