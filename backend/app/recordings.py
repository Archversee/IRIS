"""OBS recordings -> the session's Review videos, automatically.

When a flight ends (Finish & save, End Session, or the lesson finishing on its
own) LiveAdapter.py stops OBS and reports the files it wrote, which screen each
one shows and when it started (POST /sessions/{id}/recordings, videos.py). Each
is then linked to the session here, as soon as OBS has finished writing it:

  * the link carries the recording's start time, so Review lines it up with the
    flight by itself (no sync offset to type in);
  * OBS recording outside video_dir: the file is linked (or copied) into it;
  * .mkv: the .mp4 OBS remuxes it to is used -- browsers can't play .mkv;
  * a video someone linked by hand (Upload page) is never replaced.

  GET /sessions/{id}/recordings  -> per screen: how that is going (Save Session
  and Review poll it; it also picks up what a backend restart interrupted).
"""
import asyncio
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException

from . import db
from .config import settings
from .videos import SCREENS, VIDEO_EXTS, _inside_video_dir

log = logging.getLogger(__name__)
router = APIRouter(tags=["videos"])

_POLL_S = 2.0
_READY_S = 15 * 60     # longest to wait for OBS to finish writing / remuxing
_REMUX_START_S = 60    # an .mkv with no .mp4 beside it after this long isn't being remuxed
_FOLDERS = {"instrument": "Instruments", "otw": "OTW"}

_jobs: dict[tuple[UUID, str], asyncio.Task] = {}
_failed: dict[tuple[UUID, str], str] = {}       # why a screen's video couldn't be linked


class NotPlayable(Exception):
    pass


# ---------------------------------------------------------------- is it finished?
def _complete(path: str) -> bool:
    """An .mp4/.mov is playable once its index ("moov") is written, which OBS
    does last: walk the file's top-level boxes for it."""
    if os.path.splitext(path)[1].lower() not in (".mp4", ".m4v", ".mov"):
        return True
    end, pos = os.path.getsize(path), 0
    with open(path, "rb") as f:
        while pos + 8 <= end:
            f.seek(pos)
            head = f.read(16)
            size, kind = int.from_bytes(head[:4], "big"), head[4:8]
            if size == 1:
                size = int.from_bytes(head[8:16], "big")
            if size < 8 or pos + size > end:    # runs to the end / still being written
                return False
            if kind == b"moov":
                return True
            pos += size
    return False


async def _wait_playable(path: str) -> str:
    """The finished, browser-playable file for this recording."""
    stem, ext = os.path.splitext(path)
    playable = ext.lower() in VIDEO_EXTS
    names = [path] if playable else [stem + e for e in sorted(VIDEO_EXTS)]
    loop = asyncio.get_running_loop()
    began = loop.time()
    sizes: dict[str, int] = {}
    while True:
        found = [p for p in names if os.path.isfile(p)]
        for p in found:
            try:
                size = os.path.getsize(p)
                # no longer growing, and whole
                if size and sizes.get(p) == size and await asyncio.to_thread(_complete, p):
                    return p
                sizes[p] = size
            except OSError:                     # OBS still has it
                pass
        waited = loop.time() - began
        if not found and waited > _REMUX_START_S:
            raise NotPlayable(
                "The recording file wasn't found." if playable or not os.path.isfile(path) else
                f"OBS saved a {ext} file, which browsers can't play. In OBS turn on "
                "Settings > Advanced > Automatically remux to mp4.")
        if waited > _READY_S:
            raise NotPlayable("OBS never finished writing the recording.")
        await asyncio.sleep(_POLL_S)


def _into_video_dir(path: str, session_id: UUID, screen: str) -> tuple[str, bool]:
    """(filename relative to video_dir, whether a new file was made for it)"""
    made = False
    if not _inside_video_dir(path):
        folder = os.path.join(settings.video_dir, _FOLDERS[screen])
        os.makedirs(folder, exist_ok=True)
        dest = os.path.join(folder, f"{session_id}_{os.path.basename(path)}")
        if not os.path.exists(dest):
            try:
                os.link(path, dest)             # same drive: no copying, no extra space
            except OSError:
                part = dest + ".part"
                try:
                    shutil.copy2(path, part)
                    os.replace(part, dest)
                except OSError:
                    if os.path.exists(part):
                        os.remove(part)
                    raise
            made = True
        path = dest
    rel = os.path.relpath(os.path.realpath(path), os.path.realpath(settings.video_dir))
    return rel.replace(os.sep, "/"), made


# ---------------------------------------------------------------- linking
async def _link(session_id: UUID, screen: str, rec: dict) -> None:
    key = (session_id, screen)
    try:
        path = await _wait_playable(rec["path"])
        name, made = await asyncio.to_thread(_into_video_dir, path, session_id, screen)
        started = rec.get("started_at")
        started = datetime.fromtimestamp(started, tz=timezone.utc) if started else None
        async with db.acquire() as conn:        # screen is one of SCREENS: safe to interpolate
            r = await conn.execute(
                f"update sessions set {screen}_video_filename=$1, {screen}_video_offset_sec=0, "
                f"{screen}_video_started_at=$2 where id=$3 and {screen}_video_filename is null",
                name, started, session_id)
        if r.endswith(" 0"):                    # discarded, or linked by hand meanwhile
            if made:
                os.remove(os.path.join(settings.video_dir, name))
        else:
            log.info("session %s: %s recording linked (%s)", session_id, screen, name)
    except NotPlayable as e:
        _failed[key] = str(e)
        log.warning("session %s: %s recording not linked: %s", session_id, screen, e)
    except Exception as e:
        _failed[key] = "The recording couldn't be added to the review."
        log.warning("session %s: %s recording not linked: %s", session_id, screen, e)
    finally:
        _jobs.pop(key, None)


async def _row(session_id: UUID):
    async with db.acquire() as conn:
        return await conn.fetchrow(
            "select instrument_video_filename, otw_video_filename, recordings from sessions where id = $1",
            session_id)


def _start(session_id: UUID, row, retry: bool) -> None:
    """Start linking every screen that has a recording and no video yet."""
    recs = json.loads(row["recordings"] or "[]")
    for screen in SCREENS:
        key = (session_id, screen)
        mine = [r for r in recs if r["screen"] == screen]
        if not mine or row[f"{screen}_video_filename"] or key in _jobs or (key in _failed and not retry):
            continue
        _failed.pop(key, None)
        _jobs[key] = asyncio.create_task(_link(session_id, screen, mine[-1]))


async def recordings_saved(session_id: UUID) -> None:
    """New recordings were reported for this session (videos.py)."""
    row = await _row(session_id)
    if row is not None:
        _start(session_id, row, retry=True)


@router.get("/sessions/{session_id}/recordings")
async def status(session_id: UUID):
    """{screen: {state, detail}}. state: linked | processing (OBS finishing the
    file) | failed (detail says why) | waiting (no recording reported)."""
    row = await _row(session_id)
    if row is None:
        raise HTTPException(404, "Session not found")
    _start(session_id, row, retry=False)
    out = {}
    for screen in sorted(SCREENS):
        key = (session_id, screen)
        state = ("linked" if row[f"{screen}_video_filename"] else "processing" if key in _jobs
                 else "failed" if key in _failed else "waiting")
        out[screen] = {"state": state, "detail": _failed.get(key) if state == "failed" else None}
    return out
