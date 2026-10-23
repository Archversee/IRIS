"""
Live Monitor's screens, captured on this machine by window title.

The browser's getDisplayMedia() can't be told which window to grab -- it
always shows its own picker -- so the backend (which runs on the sim PC
alongside MSFS, see config.py) finds the OTW and instrument windows by
title itself and streams them as MJPEG, which a plain <img> can show from
any machine. The Live page's Capture buttons stay as a manual override.

Capture uses Windows Graphics Capture (the `windows-capture` package), which
works on DirectX windows like MSFS even when they're covered by others. A
screen's capture only runs while someone is watching it.

  GET /capture/status         which windows were found, polled by the Live page
  GET /capture/{screen}.mjpg  multipart/x-mixed-replace stream (screen: otw | instrument)
"""
import asyncio
import ctypes
import logging
import sys
import threading
from ctypes import wintypes

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ..config import settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/capture", tags=["capture"])

try:
    import cv2
    from windows_capture import WindowsCapture
except Exception as e:  # not Windows / package missing -- screens just show as unavailable
    WindowsCapture = None
    log.warning("window capture unavailable: %s", e)

_FPS = 30
_MAX_WIDTH = 1280      # frames are scaled down to this before encoding -- plenty for a monitor tile
_JPEG_QUALITY = 70
_BOUNDARY = "frame"


def _title_for(screen: str) -> str:
    return {"otw": settings.capture_otw_title, "instrument": settings.capture_instrument_title}[screen]


# ---------------------------------------------------------------------------
# Window lookup
# ---------------------------------------------------------------------------

def _visible_windows() -> list[tuple[int, str, int]]:
    """(hwnd, title, area) of every visible, titled top-level window."""
    if sys.platform != "win32":
        return []
    user32 = ctypes.windll.user32
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                r = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                out.append((hwnd, buf.value, max(0, r.right - r.left) * max(0, r.bottom - r.top)))
        return True

    user32.EnumWindows(cb, 0)
    return out


def find_window(screen: str) -> tuple[int, str] | None:
    """Best window whose title contains this screen's configured title.

    The instrument window's title is checked against the OTW one too, so a
    popout titled e.g. "Cessna 172 - Microsoft Flight Simulator" is never
    taken for the main OTW window. Titles starting with the pattern win over
    ones that merely contain it (a browser tab about MSFS, say), then the
    biggest window wins.
    """
    want = _title_for(screen).lower()
    other = _title_for("instrument" if screen == "otw" else "otw").lower()
    matches = []
    for hwnd, title, area in _visible_windows():
        t = title.lower()
        if want not in t:
            continue
        if screen == "otw" and other in t:
            continue
        matches.append((t.startswith(want), area, hwnd, title))
    if not matches:
        return None
    _, _, hwnd, title = max(matches)
    return hwnd, title


# ---------------------------------------------------------------------------
# Capture sessions (one per screen, shared by every viewer of it)
# ---------------------------------------------------------------------------

class _ScreenCapture:
    def __init__(self, screen: str):
        self.screen = screen
        self.lock = threading.Lock()
        self.viewers = 0
        self.control = None
        self.alive = False
        self.seq = 0
        self.jpeg: bytes | None = None
        self.waiters: set = set()   # (loop, asyncio.Event) per viewer, set on each new frame

    def _start(self, hwnd: int) -> None:
        cap = WindowsCapture(cursor_capture=False, draw_border=False,
                             minimum_update_interval=int(1000 / _FPS), window_hwnd=hwnd)

        @cap.event
        def on_frame_arrived(frame, control):
            if not self.alive:
                control.stop()
                return
            img = frame.frame_buffer[:, :, :3]
            if frame.width > _MAX_WIDTH:
                img = cv2.resize(img, (_MAX_WIDTH, round(frame.height * _MAX_WIDTH / frame.width)),
                                 interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY])
            if ok:
                self.jpeg = buf.tobytes()
                self.seq += 1
                self._wake()

        @cap.event
        def on_closed():  # window closed (or capture stopped)
            self.alive = False
            self._wake()

        self.alive = True
        self.jpeg = None
        self.control = cap.start_free_threaded()

    def _wake(self) -> None:
        # runs on the capture thread -- hand each viewer's event to its own loop
        for loop, ev in list(self.waiters):
            loop.call_soon_threadsafe(ev.set)

    def subscribe(self) -> bool:
        with self.lock:
            if not self.alive:
                found = find_window(self.screen)
                if not found:
                    return False
                try:
                    self._start(found[0])
                except Exception as e:
                    log.warning("capturing %s window %r failed: %s", self.screen, found[1], e)
                    self.alive = False
                    return False
                log.info("capturing %s from window %r", self.screen, found[1])
            self.viewers += 1
            return True

    def unsubscribe(self) -> None:
        with self.lock:
            self.viewers -= 1
            if self.viewers <= 0:
                self.viewers = 0
                self.alive = False
                if self.control:
                    try:
                        self.control.stop()
                    except Exception:
                        pass
                    self.control = None


_captures = {s: _ScreenCapture(s) for s in ("otw", "instrument")}


async def _mjpeg(cap: _ScreenCapture):
    # Sends each frame the moment it's encoded (no polling delay); a viewer
    # that falls behind just skips to the newest frame.
    ev = asyncio.Event()
    waiter = (asyncio.get_running_loop(), ev)
    cap.waiters.add(waiter)
    try:
        last = -1
        while cap.alive:
            ev.clear()
            if cap.seq != last and cap.jpeg:
                last, jpg = cap.seq, cap.jpeg
                yield (f"--{_BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                       f"Content-Length: {len(jpg)}\r\n\r\n").encode() + jpg + b"\r\n"
                continue
            try:
                await asyncio.wait_for(ev.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass  # no new frame (static/minimised window) -- recheck alive
    finally:
        cap.waiters.discard(waiter)
        cap.unsubscribe()


@router.get("/status")
async def status():
    out = {}
    for screen in _captures:
        found = find_window(screen) if WindowsCapture else None
        out[screen] = {"available": bool(found), "title": found[1] if found else None,
                       "looking_for": _title_for(screen)}
    return out


@router.get("/{screen}.mjpg")
async def stream(screen: str):
    cap = _captures.get(screen)
    if cap is None:
        raise HTTPException(404, "unknown screen")
    if WindowsCapture is None:
        raise HTTPException(503, "window capture isn't available on this machine")
    if not await asyncio.to_thread(cap.subscribe):
        raise HTTPException(404, f"no window titled {_title_for(screen)!r} is open")
    return StreamingResponse(_mjpeg(cap), media_type=f"multipart/x-mixed-replace; boundary={_BOUNDARY}",
                             headers={"Cache-Control": "no-cache, no-store"})
