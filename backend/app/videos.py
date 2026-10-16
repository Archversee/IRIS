import logging
import os
import threading
import time
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from . import db
from .config import settings

router = APIRouter(tags=["videos"])
VIDEO_EXTS = {".mp4", ".webm", ".m4v", ".mov"}
SCREENS = {"instrument", "otw"}  # each session links one recording per screen

@router.get("/videos-available")
async def available():
    found = []
    for root, _dirs, files in os.walk(settings.video_dir):
        for f in files:
            if os.path.splitext(f)[1].lower() in VIDEO_EXTS:
                rel = os.path.relpath(os.path.join(root, f), settings.video_dir)
                found.append(rel.replace(os.sep, "/"))
    return sorted(found)

class VideoLink(BaseModel):
    filename: str | None = None
    offset_sec: float = 0

@router.put("/sessions/{session_id}/video/{screen}")
async def set_video(session_id: str, screen: str, body: VideoLink):
    if screen not in SCREENS:
        raise HTTPException(404, "Unknown screen — use 'instrument' or 'otw'")
    if body.filename and not os.path.isfile(os.path.join(settings.video_dir, body.filename)):
        raise HTTPException(404, "File not in video dir")
    async with db.acquire() as conn:
        # screen is whitelisted against SCREENS above, so this is safe to interpolate
        r = await conn.execute(
            f"update sessions set {screen}_video_filename=$1, {screen}_video_offset_sec=$2 where id=$3",
            body.filename, body.offset_sec, session_id)
    if r.endswith("0"):
        raise HTTPException(404, "Session not found")
    return {"ok": True}

# ---------------------------------------------------------------------
# Deleting a session's video files
# ---------------------------------------------------------------------
# Two kinds of files belong to a session:
#  * linked videos -- {instrument,otw}_video_filename, relative to video_dir
#    (picked on the Save Session / Upload pages);
#  * OBS recordings -- absolute paths LiveAdapter.py reports after it stops
#    recording (POST /sessions/{id}/recordings), kept in recording_paths.
# Both are removed when the session is deleted (Sessions page, Finish &
# discard, ...), unless another session still points at the same file.
log = logging.getLogger(__name__)

# OBS can also write .mkv/.flv/.ts (and remux to .mp4 after stopping), so
# recordings accept a wider set than the playable VIDEO_EXTS above.
RECORDING_EXTS = VIDEO_EXTS | {".mkv", ".flv", ".ts"}


async def ensure_schema() -> None:
    """Additive column for recording paths (also in db/schema_mavis_integration.sql)."""
    try:
        async with db.acquire() as conn:
            await conn.execute("alter table sessions add column if not exists recording_paths text[]")
    except Exception as e:  # sessions table not created yet -- run db/schema.sql first
        log.warning("videos: couldn't add sessions.recording_paths: %s", e)


class RecordingPaths(BaseModel):
    paths: list[str]


@router.post("/sessions/{session_id}/recordings")
async def add_recordings(session_id: UUID, body: RecordingPaths):
    """LiveAdapter.py reports the files OBS wrote for this session, so they
    can be deleted along with it later. Only absolute paths to video files."""
    paths = [os.path.abspath(p) for p in body.paths
             if p and os.path.isabs(p) and os.path.splitext(p)[1].lower() in RECORDING_EXTS]
    if not paths:
        return {"ok": True, "paths": []}
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "update sessions set recording_paths = "
            "(select array_agg(distinct p) from unnest(coalesce(recording_paths, '{}') || $2::text[]) p) "
            "where id = $1 returning recording_paths", session_id, paths)
    if row is None:
        raise HTTPException(404, "Session not found")
    return {"ok": True, "paths": row["recording_paths"]}


def _inside_video_dir(path: str) -> bool:
    root = os.path.realpath(settings.video_dir)
    return os.path.commonpath([root, os.path.realpath(path)]) == root


async def session_files(conn, session_id) -> list[str]:
    """Absolute paths of this session's video files that no OTHER session
    also uses -- call before deleting the session row."""
    row = await conn.fetchrow(
        "select instrument_video_filename, otw_video_filename, recording_paths from sessions where id = $1",
        session_id)
    if row is None:
        return []
    files = []
    for col in ("instrument_video_filename", "otw_video_filename"):
        name = row[col]
        if not name:
            continue
        shared = await conn.fetchval(
            "select 1 from sessions where id <> $1 and (instrument_video_filename = $2 or otw_video_filename = $2)",
            session_id, name)
        path = os.path.join(settings.video_dir, name)
        if not shared and _inside_video_dir(path):
            files.append(os.path.abspath(path))
    for p in row["recording_paths"] or []:
        shared = await conn.fetchval(
            "select 1 from sessions where id <> $1 and $2 = any(recording_paths)", session_id, p)
        if not shared:
            # OBS's remuxed copy (same name, .mp4) appears after StopRecord
            stem = os.path.splitext(p)[0]
            files.extend(stem + ext for ext in sorted(RECORDING_EXTS))
    return sorted(set(files))


# A file can't go straight away when OBS still has it open (deleted mid-recording,
# or while it remuxes .mkv -> .mp4, which also creates the .mp4 only afterwards):
# keep trying for this long in the background.
_RETRY_S = 120


def _delete_pass(paths: list[str], last_error: dict) -> list[str]:
    removed = []
    for p in paths:
        if not os.path.isfile(p):
            continue
        try:
            os.remove(p)
            removed.append(p)
            last_error.pop(p, None)
            log.info("deleted video file %s", p)
        except OSError as e:  # e.g. still open in OBS
            last_error[p] = e
    return removed


def _retry_worker(paths: list[str], last_error: dict) -> None:
    deadline = time.time() + _RETRY_S
    while time.time() < deadline:
        time.sleep(2)
        _delete_pass(paths, last_error)
    for p, e in last_error.items():
        if os.path.isfile(p):
            log.warning("couldn't delete video file %s: %s", p, e)


def delete_files(paths: list[str]) -> list[str]:
    """Best-effort delete; returns the files removed straight away. Anything
    still locked or not written yet is retried in the background."""
    paths = [p for p in paths if os.path.splitext(p)[1].lower() in RECORDING_EXTS]
    last_error: dict[str, OSError] = {}
    removed = _delete_pass(paths, last_error)
    rest = [p for p in paths if p not in removed]
    if rest:
        threading.Thread(target=_retry_worker, args=(rest, last_error), daemon=True).start()
    return removed
