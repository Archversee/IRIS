"""Check the Smart Eye -> IRIS pipeline, link by link, and say what's wrong.

    Smart Eye Pro --UDP 5001--> Sim_Data_Adapter/LiveAdapter.py --WS--> IRIS backend
        --> eye_tracking_data --> Review / Live Monitor (gaze dots, Workload line)

Run it on the sim PC (standard library only, any Python 3.10+):

    python scripts/check_eye_pipeline.py                 # checks the latest session
    python scripts/check_eye_pipeline.py --session <id>  # a specific session
    python scripts/check_eye_pipeline.py --listen 15     # also listen for Smart Eye
                                                         # packets for 15 s (stop the
                                                         # adapter first: it holds the port)
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ADAPTER_DIR = REPO / "Sim_Data_Adapter"
UDP_PORT = 5001
SCREEN_AOIS = ("OTW", "Instruments")
# Smart Eye output ids LiveAdapter.py decodes (keep in sync with its _IDS)
SEP_IDS = {0x0009: "RealTimeClock", 0x0010: "HeadPosition", 0x0016: "HeadHeading", 0x0017: "HeadPitch",
           0x0018: "HeadRoll", 0x0030: "FilteredGazeDirection", 0x0500: "FilteredGazeOrigin", 0x003e: "Fixation",
           0x003f: "Blink", 0x0041: "FilteredClosestWorldIntersection", 0x0050: "EyelidOpening",
           0x0068: "FilteredLeftPupilDiameter", 0x006a: "FilteredRightPupilDiameter", 0x0022: "GazeDirectionQ"}
NEEDED = {"RealTimeClock": "every sample is dropped (no timestamp)",
          "FilteredClosestWorldIntersection": "no gaze on the screens / no AOIs",
          "FilteredLeftPupilDiameter": "no Workload line", "FilteredRightPupilDiameter": "no Workload line"}

problems: list[str] = []


def ok(msg):
    print(f"  OK    {msg}")


def bad(msg, fix=""):
    print(f"  FAIL  {msg}")
    if fix:
        print(f"        -> {fix}")
    problems.append(msg)


def warn(msg, fix=""):
    print(f"  WARN  {msg}")
    if fix:
        print(f"        -> {fix}")


def get(base, path):
    with urllib.request.urlopen(base + path, timeout=15) as r:
        return json.loads(r.read())


def ts(v):
    return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()


def check_backend(base):
    print("\n1. IRIS backend")
    try:
        get(base, "/health")
        ok(f"reachable at {base}")
        return True
    except Exception as e:
        bad(f"not reachable at {base} ({e})", "start the backend (scripts/launch_all.ps1) or pass --iris <url>")
        return False


def check_adapter(base):
    print("\n2. Adapter (Sim_Data_Adapter/LiveAdapter.py)")
    venv_py = next((p for p in (ADAPTER_DIR / ".venv" / "Scripts" / "python.exe", ADAPTER_DIR / ".venv" / "bin" / "python")
                    if p.is_file()), None)
    py = str(venv_py) if venv_py else "python"
    if venv_py:
        ok(f"adapter venv found: {venv_py}")
    else:
        warn("no Sim_Data_Adapter/.venv -- the adapter runs with whatever `python` is on PATH",
             "cd Sim_Data_Adapter; python -m venv .venv; .venv\\Scripts\\pip install obsws-python websocket-client")
    try:
        r = subprocess.run([py, "-c", "import websocket, obsws_python"], capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            ok(f"{py} has websocket-client and obsws-python")
        else:
            bad(f"{py} is missing the adapter's packages ({r.stderr.strip().splitlines()[-1] if r.stderr else 'import failed'})",
                f'"{py}" -m pip install obsws-python websocket-client')
    except Exception as e:
        bad(f"couldn't run {py} ({e})")
    try:
        running = get(base, "/adapter/status").get("running")
        if running:
            ok("adapter is running (its lock file points at a live process)")
        else:
            bad("adapter is NOT running",
                "it starts with the first MAVIS session, or POST /adapter/launch; if it keeps dying, read "
                "logs/launcher/Adapter.err.log or the adapter's own console window")
    except Exception as e:
        warn(f"couldn't ask IRIS whether the adapter runs ({e})")
    for log in (REPO / "logs" / "launcher" / "Adapter.out.log", REPO / "logs" / "launcher" / "Adapter.err.log"):
        if log.is_file():
            lines = [l for l in log.read_text(errors="replace").splitlines()
                     if "[eye" in l or "Error" in l or "Traceback" in l or "WARNING" in l]
            if lines:
                print(f"        last eye lines in {log.relative_to(REPO)}:")
                for l in lines[-8:]:
                    print(f"          {l}")


def check_session(base, sid):
    print("\n3. Eye data stored in IRIS")
    if not sid:
        try:
            rows = get(base, "/sessions")
        except Exception as e:
            bad(f"couldn't list sessions ({e})")
            return
        if not rows:
            bad("no sessions yet", "fly a session with MAVIS first")
            return
        rows.sort(key=lambda r: str(r.get("started_at") or r.get("created_at") or ""), reverse=True)
        sid = rows[0]["id"]
        print(f"        latest session: {rows[0].get('name')} ({sid})")
    try:
        summ = get(base, f"/sessions/{sid}/summary")
    except Exception as e:
        bad(f"session {sid} not found ({e})")
        return
    n_eye, n_flight = summ.get("eye_rows", 0), summ.get("flight_rows", 0)
    print(f"        flight rows: {n_flight}   eye rows: {n_eye}")
    if not n_eye:
        bad("this session has NO eye rows -> no Workload line and no gaze dots",
            "the break is before the database: see sections 2 and 4 (run with --listen 15 after stopping the adapter)")
        return
    ok(f"{n_eye} eye rows stored")
    eye = get(base, f"/sessions/{sid}/eye?max_points=5000")
    flight = get(base, f"/sessions/{sid}/flight?max_points=2000") if n_flight else []

    pupils = sum(1 for e in eye if e.get("pupil_diam_left_mm") is not None or e.get("pupil_diam_right_mm") is not None)
    if pupils:
        ok(f"pupil diameter in {100 * pupils / len(eye):.0f}% of samples (drives the Workload line)")
    else:
        bad("no pupil diameters -> no Workload line",
            "add FilteredLeftPupilDiameter + FilteredRightPupilDiameter to SEP's UDP Output Data")

    aois: dict[str, int] = {}
    for e in eye:
        aois[e.get("aoi") or "(none)"] = aois.get(e.get("aoi") or "(none)", 0) + 1
    print("        Smart Eye objects looked at: " + ", ".join(f"{k} {100 * v / len(eye):.0f}%"
                                                         for k, v in sorted(aois.items(), key=lambda x: -x[1])[:8]))
    on_screens = sum(v for k, v in aois.items() if k in SCREEN_AOIS)
    if on_screens:
        ok(f"{100 * on_screens / len(eye):.0f}% of samples are on 'OTW' / 'Instruments' (gaze dots can show)")
    else:
        bad("no samples on objects named exactly 'OTW' or 'Instruments' -> no gaze dots on the screens",
            "name the two screen objects in SEP's world model 'OTW' and 'Instruments' (case-sensitive), "
            "or add FilteredClosestWorldIntersection to SEP's Output Data if every object is (none)")

    if flight:
        e0, e1 = ts(eye[0]["ts"]), ts(eye[-1]["ts"])
        f0, f1 = ts(flight[0]["ts"]), ts(flight[-1]["ts"])
        overlap = min(e1, f1) - max(e0, f0)
        if overlap <= 0:
            bad(f"eye and flight times don't overlap (eye starts {e0 - f0:+.0f}s from the flight's start)",
                "the Smart Eye PC's clock is off: turn on 'Set time automatically' on BOTH PCs and sync them")
        elif abs(e0 - f0) > 120:
            warn(f"eye data starts {e0 - f0:+.0f}s from the flight data",
                 "fine if the tracker started late; otherwise check both PCs' clocks are synced")
        else:
            ok(f"eye and flight times line up (eye starts {e0 - f0:+.1f}s from the flight)")


def listen(seconds):
    print(f"\n4. Smart Eye UDP packets on port {UDP_PORT} ({seconds}s)")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("0.0.0.0", UDP_PORT))
    except OSError as e:
        bad(f"can't listen on UDP {UDP_PORT} ({e})", "stop the adapter first (it holds the port), then run this again")
        return
    sock.settimeout(1.0)
    n, fields, src = 0, set(), None
    end = time.time() + seconds
    while time.time() < end:
        try:
            data, addr = sock.recvfrom(65536)
        except socket.timeout:
            continue
        n += 1
        src = addr[0]
        pos = 8
        while pos + 4 <= len(data):
            sub_id, sub_len = struct.unpack_from(">HH", data, pos)
            pos += 4 + sub_len
            if sub_id in SEP_IDS:
                fields.add(SEP_IDS[sub_id])
    sock.close()
    if not n:
        bad("no packets from Smart Eye",
            f"in SEP's Communication Settings send UDP to this PC's LAN IP, port {UDP_PORT}; make sure SEP is "
            f"tracking; allow inbound UDP {UDP_PORT} in Windows Firewall")
        return
    ok(f"{n} packets from {src} ({n / seconds:.0f}/s)")
    for f, why in NEEDED.items():
        (ok if f in fields else bad)(f"{f} {'present' if f in fields else 'MISSING -> ' + why}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iris", default=os.environ.get("IRIS_URL", "http://localhost:8000"))
    ap.add_argument("--session", help="session id (default: the latest)")
    ap.add_argument("--listen", type=float, default=0, help="also listen for Smart Eye packets for N seconds")
    a = ap.parse_args()
    up = check_backend(a.iris)
    if up:
        check_adapter(a.iris)
        check_session(a.iris, a.session)
    if a.listen:
        listen(a.listen)
    print("\n" + ("All checks passed." if not problems else f"{len(problems)} problem(s) found -- fix the first one first."))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
