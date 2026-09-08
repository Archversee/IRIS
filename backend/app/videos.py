import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from . import db
from .config import settings

router = APIRouter(tags=["videos"])
VIDEO_EXTS = {".mp4", ".webm", ".m4v", ".mov"}

@router.get("/videos-available")
async def available():
    return [f for f in sorted(os.listdir(settings.video_dir))
            if os.path.splitext(f)[1].lower() in VIDEO_EXTS]

class VideoLink(BaseModel):
    filename: str | None = None
    offset_sec: float = 0

@router.put("/sessions/{session_id}/video")
async def set_video(session_id: str, body: VideoLink):
    if body.filename and not os.path.isfile(os.path.join(settings.video_dir, body.filename)):
        raise HTTPException(404, "File not in video dir")
    async with db.acquire() as conn:
        r = await conn.execute(
            "update sessions set video_filename=$1, video_offset_sec=$2 where id=$3",
            body.filename, body.offset_sec, session_id)
    if r.endswith("0"):
        raise HTTPException(404, "Session not found")
    return {"ok": True}