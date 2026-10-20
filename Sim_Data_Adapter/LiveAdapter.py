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
session_id on every Start (Basic Flight, Free Flight, whichever), which
session_watcher() below notices and uses to start a new OBS recording and
retarget the eye bridge's forward socket, without needing this process
restarted between flights. Separately, session_end_watcher() polls IRIS's
own session record for ended_at (set the moment MAVIS's End Session/Main-
menu flow actually quits -- see frontend/src/live/LiveCoach.jsx) and stops
that recording right away, since MAVIS's own WS state never nulls
session_id on quit (only the *next* Start mints a new one), so
session_watcher() alone can't tell "this flight just ended" from "nothing
has happened yet". It's launched once per boot now (by the launcher
script, or automatically by IRIS backend's app/mavis_bridge.py the first
time it sees a session), not once per flight.

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
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import websocket
import obsws_python as obsws

# Python only line-buffers stdout when it's a real console; redirected to a
# file or pipe (e.g. launch_all.ps1's -RedirectStandardOutput, or IRIS
# backend's own Popen capturing this process's output), it silently
# switches to block buffering instead. This script now runs indefinitely
# and prints only a handful of short lines across an entire flight, so
# that buffer would otherwise never fill -- meaning the log file stays
# empty no matter how long it's been running or what it's actually seen.
# Force line buffering unconditionally so every print() lands immediately
# regardless of how this process was launched.
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

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

# One ("start" | "end" | "resume" | "deleted", session_id) event per session
# lifecycle change, from session_watcher/session_end_watcher,
# in order -- main() consumes these to know when to start a new OBS
# recording, stop the current one, or both. The session_id travels with
# every event (rather than a bare sentinel) so an "end" that was queued
# just before a new "start" can't be mistaken, once main() gets around to
# it, for ending the NEW session instead of the one it actually applies to.
_session_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()


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
                    _session_queue.put(("start", sid))
        except Exception as e:
            print(f"Lost connection to MAVIS ({e}); retrying")
        finally:
            try:
                ws.close()
            except Exception:
                pass
        time.sleep(2)


# ========================================================================
# End-of-session watcher -- session_watcher() above only ever learns about
# a NEW session starting; MAVIS deliberately never nulls its own
# session_id on quit (a fresh one is only minted on the next Start), so
# there's no way to notice "this flight just ended" from MAVIS's own WS
# feed alone. Without this, OBS just kept recording until the NEXT flight
# started, ignoring End Session entirely. IRIS's own sessions.ended_at is
# what LiveCoach.jsx's End Session/Main-menu flow sets the moment the
# trainee actually quits (see backend/app/schemas.py's SessionUpdate) --
# polling that here is what makes End Session actually stop the recording.
# ========================================================================
def _session_status(session_id: str) -> str | None:
    """'running' | 'ended' | 'deleted' (404 -- the trainee chose Finish &
    discard), or None when IRIS can't be reached (don't act on a guess)."""
    url = f"{API_BASE_URL}/sessions/{session_id}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return "deleted" if e.code == 404 else None
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None  # IRIS backend down/unreachable -- assume still running, don't stop on a guess
    return "ended" if data.get("ended_at") else "running"


# Sessions whose recordings have stopped but might still be discarded
# (session deleted) from the scorecard afterwards -- main() adds to this
# when it stops a recording; the watcher keeps checking them for deletion.
_recent_sids: "list[str]" = []
_recent_lock = threading.Lock()
_RECENT_MAX = 5


def session_end_watcher() -> None:
    signaled_end = None
    signaled_deleted: set[str] = set()
    while True:
        sid = SESSION_ID
        with _recent_lock:
            watch = list(_recent_sids)
        if sid and sid not in watch:
            watch.append(sid)
        for s in watch:
            if s in signaled_deleted:
                continue
            status = _session_status(s)
            if status == "deleted":
                signaled_deleted.add(s)
                _session_queue.put(("deleted", s))
            elif status == "ended" and s == sid and s != signaled_end:
                signaled_end = s
                _session_queue.put(("end", s))
            elif status == "running" and s == sid and s == signaled_end:
                # ended_at cleared again: "Fly again" after a lesson that
                # finished on its own -- same session, record the new flight
                signaled_end = None
                _session_queue.put(("resume", s))
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


def stop_obs_recordings(clients) -> list[str]:
    """Stops every OBS instance; returns the saved file paths OBS reported."""
    paths = []
    for name, client in clients:
        try:
            resp = client.stop_record()
            print(f"OBS ({name}) saved recording to {resp.output_path}")
            if resp.output_path:
                paths.append(resp.output_path)
        except Exception as e:
            print(f"Failed to stop OBS ({name}) recording cleanly: {e}")
    return paths


# Same-named siblings to remove too: with OBS's "Automatically remux to mp4"
# on, it records .mkv and writes the .mp4 only AFTER StopRecord returns.
_RECORDING_EXTS = (".mkv", ".mp4", ".mov", ".flv", ".ts", ".m4v", ".webm")
_DISCARD_WATCH_S = 90  # how long to keep catching a late remux / released file lock


def _discard_worker(sid: str, paths: list[str]) -> None:
    targets = sorted({os.path.splitext(p)[0] + ext for p in paths for ext in _RECORDING_EXTS})
    deleted: set[str] = set()
    last_error: dict[str, OSError] = {}
    deadline = time.time() + _DISCARD_WATCH_S
    while time.time() < deadline:
        for t in targets:
            if t in deleted or not os.path.exists(t):
                continue
            try:
                os.remove(t)
                deleted.add(t)
                last_error.pop(t, None)
                print(f"Discarded session {sid}: deleted {t}")
            except OSError as e:  # still open in OBS (writing/remuxing) -- try again shortly
                last_error[t] = e
        time.sleep(1)
    for t, e in last_error.items():
        if os.path.exists(t):
            print(f"Couldn't delete discarded recording {t}: {e}")
    if not deleted:
        print(f"Discarded session {sid}: no recording files found at {paths}")


_REGISTER_RETRY_S = 30 * 60   # keep trying this long if IRIS is down / erroring


def _post_recordings(sid: str, paths: list[str]) -> bool:
    """True when IRIS has the paths, or the session is already gone (404)."""
    req = urllib.request.Request(
        f"{API_BASE_URL}/sessions/{sid}/recordings",
        data=json.dumps({"paths": [os.path.abspath(p) for p in paths]}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5):
            print(f"Registered {len(paths)} recording(s) with IRIS for session {sid}")
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return True
        print(f"Couldn't register recordings for {sid} with IRIS: HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"Couldn't register recordings for {sid} with IRIS: {e}")
    return False


def _register_retry_worker(sid: str, paths: list[str]) -> None:
    deadline = time.time() + _REGISTER_RETRY_S
    while time.time() < deadline:
        time.sleep(15)
        if _post_recordings(sid, paths):
            return
    print(f"Gave up registering recordings for {sid}: deleting the session won't remove {paths}")


def register_recordings(sid: str, paths: list[str], retry: bool = True) -> None:
    """Tell IRIS which files OBS wrote for this session, so deleting the
    session later (Sessions page, Finish & discard, any time) removes them
    -- not just a discard this process happens to still remember. If IRIS
    can't take them right now, keep trying in the background: an unregistered
    recording is never deleted with its session."""
    if not _post_recordings(sid, paths) and retry:
        threading.Thread(target=_register_retry_worker, args=(sid, paths), daemon=True).start()


def delete_recordings(sid: str, paths: list[str]) -> None:
    """Finish & discard: remove the files OBS wrote for this session (plus
    any remuxed copy), on a background thread so the main loop isn't held
    up while OBS finishes writing."""
    if not paths:
        print(f"Discarded session {sid}: no recordings were saved for it (OBS off, or this adapter didn't record it).")
        return
    threading.Thread(target=_discard_worker, args=(sid, paths), daemon=True).start()


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


# ----------------------------------------------------------------------
# Eye <-> flight clock sync. Smart Eye stamps each sample with ITS PC's
# clock (RealTimeClock); IRIS's flight data is stamped by MAVIS, on THIS PC
# (launch_all.ps1 runs MAVIS, IRIS and this adapter together). On a LAN a
# packet arrives within a few ms, so (arrival time here - Smart Eye's
# stamp) measures how far the two clocks disagree. A low percentile of the
# recent values ignores packets that were delayed. Samples are re-stamped
# onto this PC's clock before they're stored, so a +/-30 s window around a
# rule breach lines eye and flight data up; the measured offset is also
# reported to IRIS (sessions.eye_clock_*) so Review can show it.
# ----------------------------------------------------------------------
CORRECT_EYE_CLOCK = True          # False: store Smart Eye's own timestamps unchanged
_CLOCK_WINDOW = 300               # recent packets the estimate uses
_CLOCK_MIN_SAMPLES = 5            # correct from the 5th packet on (the estimate sharpens as more arrive)
_CLOCK_REPORT_EVERY_S = 30.0
_CLOCK_WARN_S = 0.1               # say so when the clocks differ by more than this


class ClockSync:
    def __init__(self):
        from collections import deque
        self._d = deque(maxlen=_CLOCK_WINDOW)
        self.offset: float | None = None      # seconds to ADD to Smart Eye's time
        self.spread: float | None = None
        self._last_report = 0.0
        self._last_warned: float | None = None

    def observe(self, sep_epoch: float, arrival: float) -> None:
        self._d.append(arrival - sep_epoch)
        if len(self._d) < _CLOCK_MIN_SAMPLES:
            return
        vals = sorted(self._d)
        self.offset = vals[len(vals) // 10]                       # 10th percentile: least-delayed packets
        self.spread = vals[(len(vals) * 9) // 10] - vals[len(vals) // 10]
        if abs(self.offset) > _CLOCK_WARN_S and (self._last_warned is None
                                                 or abs(self.offset - self._last_warned) > _CLOCK_WARN_S):
            self._last_warned = self.offset
            print(f"[eye] Smart Eye's clock is {abs(self.offset):.2f} s "
                  f"{'behind' if self.offset > 0 else 'ahead of'} this PC's -- "
                  f"{'correcting the eye timestamps' if CORRECT_EYE_CLOCK else 'NOT correcting (CORRECT_EYE_CLOCK=False)'}. "
                  f"Turn on 'Set time automatically' on both PCs to fix it at the source.")

    def correct(self, sep_epoch: float) -> float:
        if CORRECT_EYE_CLOCK and self.offset is not None:
            return sep_epoch + self.offset
        return sep_epoch

    def maybe_report(self, session_id: str | None) -> None:
        now = time.time()
        if self.offset is None or not session_id or now - self._last_report < _CLOCK_REPORT_EVERY_S:
            return
        self._last_report = now
        body = {"offset_s": round(self.offset, 4), "spread_s": round(self.spread or 0.0, 4),
                "samples": len(self._d), "corrected": CORRECT_EYE_CLOCK}
        threading.Thread(target=_post_clock, args=(session_id, body), daemon=True).start()


def _post_clock(session_id: str, body: dict) -> None:
    req = urllib.request.Request(f"{API_BASE_URL}/sessions/{session_id}/eye-clock",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5):
            pass
    except Exception as e:      # older IRIS without the endpoint, or IRIS down: the data still flows
        print(f"[eye] couldn't report the clock offset to IRIS ({e})")


_clock = ClockSync()


def _sep_epoch(ticks: int) -> float:
    return (ticks - _FILETIME_EPOCH_DELTA) / 1e7


def _build_sample(fields: dict, epoch_s: float | None = None):
    """epoch_s: the sample's time on THIS PC's clock (ClockSync); default:
    Smart Eye's own RealTimeClock, unchanged."""
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
        "ts": (datetime.fromtimestamp(epoch_s, tz=timezone.utc).isoformat() if epoch_s is not None
               else _filetime_to_iso(fields["RealTimeClock"])),
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
                    _eye_stats["sent"] += 1
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


# What IRIS needs from each Smart Eye field -- checked on the first packet,
# so a field missing from SEP's Output Data selection is reported once,
# loudly, instead of silently producing empty screens in IRIS.
_NEEDED_FOR = {
    "RealTimeClock": "EVERY sample is dropped without it (it's the timestamp)",
    "FilteredClosestWorldIntersection": "no gaze on the OTW / Instruments screens, no AOIs or scan path",
    "FilteredLeftPupilDiameter": "no Workload line on the timeline (needs a pupil diameter)",
    "FilteredRightPupilDiameter": "no Workload line on the timeline (needs a pupil diameter)",
}
# IRIS's Review / Live Monitor draw the gaze dot only for these world-model
# object names (frontend useSessionData.js lookingAtOtw / lookingAtInstruments)
_SCREEN_AOIS = ("OTW", "Instruments")
_EYE_STATUS_EVERY_S = 30.0
_NO_PACKET_HINT_AFTER_S = 30.0

_eye_stats = {"packets": 0, "no_clock": 0, "parse_errors": 0, "queued": 0, "queue_full": 0, "sent": 0}


def _report_first_packet(fields: dict) -> None:
    have = sorted(fields)
    print(f"[eye] fields in Smart Eye's packets: {', '.join(have) or 'NONE of the ones we decode'}")
    missing = [f for f in _NEEDED_FOR if f not in fields]
    for f in missing:
        print(f"[eye] WARNING: {f} is missing from SEP's output -> {_NEEDED_FOR[f]}. "
              f"Add it to the UDP stream's Output Data in SEP's Communication Settings.")
    if not missing:
        print("[eye] all the fields IRIS needs are present")


def _no_packet_watchdog(stop: threading.Event) -> None:
    if stop.wait(_NO_PACKET_HINT_AFTER_S):
        return
    print(f"[eye] WARNING: no UDP packets from Smart Eye after {_NO_PACKET_HINT_AFTER_S:.0f}s on port "
          f"{UDP_LISTEN_PORT}. Check: SEP is tracking; SEP's Communication Settings send UDP to THIS PC's "
          f"LAN IP, port {UDP_LISTEN_PORT}; Windows Firewall allows inbound UDP {UDP_LISTEN_PORT} for python.")


def eye_main():
    """Best-effort eye-tracking bridge -- runs on its own daemon thread from
    main() below, and just stops with the process rather than getting its
    own clean shutdown (see module docstring)."""
    if USE_RPC_TO_OPEN_STREAM:
        open_smarteye_stream()
    threading.Thread(target=_forward_worker, daemon=True).start()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("0.0.0.0", UDP_LISTEN_PORT))
    except OSError as e:
        print(f"[eye] ERROR: can't listen on UDP {UDP_LISTEN_PORT} ({e}) -- another program (an older adapter, "
              f"or SEP's own receiver?) already has it. No eye data will reach IRIS until it's free.")
        return
    print(f"[eye] listening for Smart Eye UDP packets on :{UDP_LISTEN_PORT}")
    got_packet = threading.Event()
    threading.Thread(target=_no_packet_watchdog, args=(got_packet,), daemon=True).start()

    got_first_packet = False
    seen_aois: set[str] = set()
    last_status = time.time()
    try:
        while True:
            data, _addr = sock.recvfrom(65536)
            _eye_stats["packets"] += 1
            if time.time() - last_status >= _EYE_STATUS_EVERY_S:
                last_status = time.time()
                s = _eye_stats
                print(f"[eye] status: {s['packets']} packets, {s['queued']} samples queued, {s['sent']} sent to IRIS, "
                      f"{s['no_clock']} dropped (no RealTimeClock), {s['parse_errors']} unparseable, "
                      f"{s['queue_full']} dropped (IRIS not keeping up / not connected)")
            if not got_first_packet:
                got_first_packet = True
                got_packet.set()
                last_status = time.time()       # first status line 30 s after data starts flowing
                print(f"[eye] first UDP packet received from {_addr[0]} ({len(data)} bytes)")
                try:
                    _report_first_packet(_parse_packet(data))
                except Exception as e:
                    print(f"[eye] first packet couldn't be decoded: {e}")
            # Parsing/decoding errors used to be uncaught here -- one
            # unexpected packet shape would raise something other than
            # OSError, which the except below doesn't catch, silently
            # killing this whole listener thread with no output at all
            # (looks exactly like "logged nothing after 'listening'").
            # Drop just that packet and keep going instead.
            try:
                fields = _parse_packet(data)
                epoch = None
                if "RealTimeClock" in fields:
                    sep = _sep_epoch(fields["RealTimeClock"])
                    _clock.observe(sep, time.time())
                    epoch = _clock.correct(sep)
                    _clock.maybe_report(SESSION_ID)
                sample = _build_sample(fields, epoch)
            except Exception as e:
                _eye_stats["parse_errors"] += 1
                print(f"[eye] dropped a packet we couldn't parse: {e}")
                continue
            if sample is None:
                _eye_stats["no_clock"] += 1
                continue
            aoi = sample.get("aoi")
            if aoi and aoi not in seen_aois and len(seen_aois) < 20:
                seen_aois.add(aoi)
                note = "" if aoi in _SCREEN_AOIS else \
                    f" (IRIS only draws gaze on the screens for objects named {' / '.join(_SCREEN_AOIS)})"
                print(f"[eye] gaze on Smart Eye object '{aoi}'{note}")
            try:
                _forward_queue.put_nowait(sample)
                _eye_stats["queued"] += 1
            except queue.Full:
                _eye_stats["queue_full"] += 1
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
# Entry point -- runs for the whole boot, not just one flight. Consumes
# _session_queue one event at a time, from either watcher thread: a new
# session_id (start recording, or roll over from whichever one was
# active), or the end sentinel (stop recording, wait for the next one).
# Ctrl+C stops cleanly and exits.
# ========================================================================
def main():
    if not _acquire_lock():
        return
    global SESSION_ID
    obs_clients: list = []
    eye_bridge_started = False
    recording = False  # whether OBS is currently recording for SESSION_ID
    saved: dict[str, list[str]] = {}  # session_id -> files OBS wrote for it (kept for discard)

    def stop_and_remember(sid: str, retry: bool = True) -> None:
        paths = stop_obs_recordings(obs_clients)
        if paths:
            register_recordings(sid, paths, retry)
            saved[sid] = saved.get(sid, []) + paths   # Fly again: one session, several recordings
            with _recent_lock:
                if sid not in _recent_sids:
                    _recent_sids.append(sid)
                while len(_recent_sids) > _RECENT_MAX:
                    saved.pop(_recent_sids.pop(0), None)

    try:
        threading.Thread(target=session_watcher, daemon=True).start()
        threading.Thread(target=session_end_watcher, daemon=True).start()
        while True:
            kind, sid = _session_queue.get()

            if kind == "deleted":
                # Finish & discard: stop if it's still the one recording,
                # then delete whatever OBS wrote for it.
                if sid == SESSION_ID and recording and obs_clients:
                    print(f"Session {sid} discarded -- stopping recording.")
                    stop_and_remember(sid)
                    recording = False
                delete_recordings(sid, saved.pop(sid, []))
                with _recent_lock:
                    if sid in _recent_sids:
                        _recent_sids.remove(sid)
                continue

            if kind == "resume":
                if sid != SESSION_ID or recording:
                    continue
                if ENABLE_OBS_RECORDING and not obs_clients:
                    try:
                        obs_clients = connect_obs_clients()
                    except Exception as e:
                        print(f"[obs] couldn't connect ({e}); continuing without video recording.")
                        obs_clients = []
                if obs_clients:
                    try:
                        start_obs_recordings(obs_clients)
                        recording = True
                        print(f"Session {sid} flying again -- recording again.")
                    except Exception as e:
                        print(f"[obs] couldn't start recording ({e}); continuing without video recording.")
                        obs_clients = []
                continue

            if kind == "end":
                # Only act if this "end" still applies to whichever session
                # is actually current -- if a newer "start" already arrived
                # and was processed first (both watchers poll/push
                # independently, so ordering isn't guaranteed under a race),
                # this is a stale event for a session that was already
                # rolled over, and must NOT stop the new one's recording.
                if sid != SESSION_ID:
                    continue
                print(f"Session {sid} ended -- stopping recording.")
                if recording and obs_clients:
                    stop_and_remember(sid)
                recording = False
                continue

            # kind == "start" -- if the previous session is still recording
            # (e.g. this adapter was launched mid-flight and never saw that
            # session's own "end" event), stop it before starting the new one.
            if recording and obs_clients:
                print(f"New session started before {SESSION_ID} was seen as ended -- stopping its recording.")
                stop_and_remember(SESSION_ID)
                recording = False

            SESSION_ID = sid
            print(f"MAVIS session started: {SESSION_ID}")

            # Eye tracking first and unconditionally -- it must not depend on
            # OBS being reachable at all. A previous version called
            # connect_obs_clients() first with no try/except: if OBS wasn't
            # running or refused the connection, that exception propagated
            # all the way out of main() and killed the whole adapter process
            # before eye_main() ever started, silently taking Smart Eye
            # logging down with it.
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

            if ENABLE_OBS_RECORDING and not obs_clients:
                try:
                    obs_clients = connect_obs_clients()
                except Exception as e:
                    print(f"[obs] couldn't connect ({e}); continuing without video recording this session.")
                    obs_clients = []
            if obs_clients:
                try:
                    start_obs_recordings(obs_clients)
                    recording = True
                except Exception as e:
                    print(f"[obs] couldn't start recording ({e}); continuing without video recording this session.")
                    obs_clients = []

            print("Recording. Ctrl+C to stop the adapter entirely (fine to leave running between flights).")
    except KeyboardInterrupt:
        print("\nStopping...")
        if recording and obs_clients and SESSION_ID:
            # register them too: else deleting this session later leaves the files
            stop_and_remember(SESSION_ID, retry=False)   # exiting: one try, no background retry
    finally:
        _release_lock()


if __name__ == "__main__":
    main()
