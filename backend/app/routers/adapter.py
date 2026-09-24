"""Launches Sim_Data_Adapter/LiveAdapter.py (OBS + Smart Eye bridge).

The adapter no longer takes a session id -- it self-discovers whichever
session MAVIS is currently running via MAVIS's own WS feed (see
LiveAdapter.py's session_watcher()), and keeps running across many flights,
not just one. Three things can call launch_adapter(): the launcher script
at boot, app/mavis_bridge.py automatically the first time it sees a MAVIS
session (so OBS/Smart Eye recording never depends on anyone clicking
anything in IRIS), and this POST endpoint for manual use if the process
died. All three are safe to use together since the script's own lock file
(see LiveAdapter.py's _acquire_lock) refuses a second instance.
"""
import os
import subprocess

from fastapi import APIRouter, HTTPException

from ..config import settings

router = APIRouter(prefix="/adapter", tags=["adapter"])

_lock_path = os.path.join(os.path.dirname(settings.adapter_script_path), "logs", "adapter.lock")


def _adapter_already_running() -> bool:
    if not os.path.exists(_lock_path):
        return False
    try:
        with open(_lock_path) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return False
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True)
        return str(pid) in out.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


@router.post("/launch", status_code=202)
async def launch_adapter():
    if _adapter_already_running():
        return {"status": "already_running"}

    script = settings.adapter_script_path
    if not os.path.isfile(script):
        raise HTTPException(500, f"Adapter script not found at {script}")
    script_dir = os.path.dirname(script)

    if os.name == "nt":
        # Same "keep the console open on a crash" trick as the old
        # per-session launch -- see git history for the original rationale.
        log_dir = os.path.join(script_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)
        bat_path = os.path.join(log_dir, "_launch_adapter.bat")
        with open(bat_path, "w") as f:
            f.write(
                "@echo off\r\n"
                f'"{settings.adapter_python_path}" "{script}"\r\n'
                "echo.\r\n"
                "echo ==== adapter exited (exit code %errorlevel%) -- press any key to close ====\r\n"
                "pause >nul\r\n"
            )
        args = ["cmd", "/c", bat_path]
        creationflags = subprocess.CREATE_NEW_CONSOLE
    else:
        args = [settings.adapter_python_path, script]
        creationflags = 0

    try:
        subprocess.Popen(args, cwd=script_dir, creationflags=creationflags)
    except OSError as e:
        raise HTTPException(500, f"Couldn't launch adapter script: {e}")
    return {"status": "launched"}


@router.get("/status")
async def adapter_status():
    return {"running": _adapter_already_running()}
