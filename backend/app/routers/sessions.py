"""Session CRUD."""
import os
import subprocess
from uuid import UUID

from fastapi import APIRouter, HTTPException

from .. import db
from ..config import settings
from ..schemas import Session, SessionCreate

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

    # New console window so the script's own status output and Ctrl+C still
    # work as if launched by hand; only meaningful on Windows.
    creationflags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
    try:
        subprocess.Popen(
            [settings.adapter_python_path, script, str(session_id)],
            cwd=os.path.dirname(script),
            creationflags=creationflags,
        )
    except OSError as e:
        raise HTTPException(500, f"Couldn't launch adapter script: {e}")
    return {"status": "launched"}
