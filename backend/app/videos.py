import os
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