"""Session CRUD."""
import os
import subprocess
from uuid import UUID

from fastapi import APIRouter, HTTPException

from .. import db
from ..config import settings
from ..schemas import Session, SessionCreate, SessionUpdate

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", response_model=Session, status_code=201)
async def create_session(body: SessionCreate):
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """
            insert into sessions (name, pilot_name, aircraft, sim_source, notes)
            values ($1, $2, $3, $4, $5)
            returning *
            """,
            body.name, body.pilot_name, body.aircraft, body.sim_source, body.notes,
        )
    return dict(row)


@router.get("", response_model=list[Session])
async def list_sessions():
    async with db.acquire() as conn:
        rows = await conn.fetch("select * from sessions order by created_at desc")
    return [dict(r) for r in rows]


@router.get("/{session_id}", response_model=Session)
async def get_session(session_id: UUID):
    async with db.acquire() as conn:
        row = await conn.fetchrow("select * from sessions where id = $1", session_id)
    if row is None:
        raise HTTPException(404, "Session not found")
    return dict(row)


@router.patch("/{session_id}", response_model=Session)
async def update_session(session_id: UUID, body: SessionUpdate):
    """Fills in session details after the fact -- used by the End Session
    flow, where the session was created upfront (with a placeholder name)
    when "Go Live" was clicked, and the real name/pilot/aircraft/notes are
    only known once the flight is over."""
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        row = await get_session(session_id)
        return row

    set_clause = ", ".join(f"{col} = ${i}" for i, col in enumerate(fields, start=2))
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            f"update sessions set {set_clause} where id = $1 returning *",
            session_id, *fields.values(),
        )
    if row is None:
        raise HTTPException(404, "Session not found")
    return dict(row)


@router.delete("/{session_id}", status_code=204)
async def delete_session(session_id: UUID):
    async with db.acquire() as conn:
        result = await conn.execute("delete from sessions where id = $1", session_id)
    if result.endswith("0"):
        raise HTTPException(404, "Session not found")


@router.post("/{session_id}/launch-adapter", status_code=202)
async def launch_adapter(session_id: UUID):
    """Spawns LiveAdapter.py with this session's id already filled in, so
    the Live page's "Go Live" button doesn't need anyone to hand-copy the
    id into the script and run it themselves. Only works when the backend
    runs on the same machine as MSFS/OBS/SEP -- if the script isn't found
    here, the caller falls back to telling the user to run it by hand."""
    async with db.acquire() as conn:
        exists = await conn.fetchval("select 1 from sessions where id = $1", session_id)
    if not exists:
        raise HTTPException(404, "Session not found")

    script = settings.adapter_script_path
    if not os.path.isfile(script):
        raise HTTPException(500, f"Adapter script not found at {script}")
    script_dir = os.path.dirname(script)

    if os.name == "nt":
        # A python.exe launched directly into CREATE_NEW_CONSOLE takes that
        # console with it when it exits -- if the script dies immediately
        # (e.g. a missing dependency in whichever "python" ADAPTER_PYTHON_PATH
        # points at, or SimConnect/OBS not reachable yet), the window flashes
        # and closes before anyone can read why. Routing through a small
        # generated .bat that pauses after the script exits keeps the window
        # up so the error is actually visible.
        log_dir = os.path.join(script_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)
        bat_path = os.path.join(log_dir, "_launch_adapter.bat")
        with open(bat_path, "w") as f:
            f.write(
                "@echo off\r\n"
                f'"{settings.adapter_python_path}" "{script}" "{session_id}"\r\n'
                "echo.\r\n"
                "echo ==== LiveAdapter.py exited (exit code %errorlevel%) -- press any key to close ====\r\n"
                "pause >nul\r\n"
            )
        args = ["cmd", "/c", bat_path]
        creationflags = subprocess.CREATE_NEW_CONSOLE
    else:
        args = [settings.adapter_python_path, script, str(session_id)]
        creationflags = 0

    try:
        subprocess.Popen(args, cwd=script_dir, creationflags=creationflags)
    except OSError as e:
        raise HTTPException(500, f"Couldn't launch adapter script: {e}")
    return {"status": "launched"}
