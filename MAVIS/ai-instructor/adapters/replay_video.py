"""Prepare a screen recording of a replayed flight for the tablet's trainee view.

Recordings arrive as whatever the capture tool wrote: index at the end of the
file (unplayable until fully downloaded), a first frame that is not a keyframe,
60 fps, and sometimes frames the encoder dropped and left as blank bytes. The
prepared copy is a web-friendly, video-only, fixed-rate MP4 whose clock starts
at the recording's first sample, so it runs in lockstep with the telemetry log:
undecodable frames are skipped and the last good frame is held across the gap.

The prepared file sits beside the source and is rebuilt only when the source is
newer, so swapping in a new recording is a file copy.
"""
from __future__ import annotations
import logging
import time
from fractions import Fraction
from pathlib import Path

log = logging.getLogger(__name__)

PREPARED_SUFFIX = ".web.mp4"


def prepared_path(src: Path) -> Path:
    return src.with_name(src.stem + PREPARED_SUFFIX)


def prepare_video(src: Path, fps: int = 30) -> Path:
    """Return the prepared copy of `src`, building it if missing or stale."""
    dst = prepared_path(src)
    if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
        return dst
    import av
    av.logging.set_level(av.logging.FATAL)
    t0 = time.time()
    log.info("preparing replay video %s -> %s", src.name, dst.name)
    container = av.open(str(src))
    vs = container.streams.video[0]
    vs.thread_type = "AUTO"
    out = av.open(str(dst), "w", format="mp4", options={"movflags": "faststart"})
    ov = out.add_stream("libx264", rate=fps)
    ov.width, ov.height = vs.codec_context.width, vs.codec_context.height
    ov.pix_fmt = "yuv420p"
    ov.time_base = Fraction(1, fps)
    ov.options = {"preset": "veryfast", "crf": "23", "g": str(fps * 2)}

    step = 1.0 / fps
    state = {"n": 0, "next_t": 0.0}

    def emit(frame, upto: float) -> None:
        while state["next_t"] < upto:
            f = frame.reformat(format="yuv420p")
            f.pts, f.time_base = state["n"], Fraction(1, fps)
            for p in ov.encode(f):
                out.mux(p)
            state["n"] += 1
            state["next_t"] += step

    last, skipped = None, 0
    for pkt in container.demux(vs):
        if pkt.dts is None:
            continue
        try:
            frames = pkt.decode()
        except Exception:
            skipped += 1
            continue
        for fr in frames:
            emit(last if last is not None else fr, fr.time)
            last = fr
    if last is not None:
        emit(last, last.time + step)
    for p in ov.encode(None):
        out.mux(p)
    out.close()
    container.close()
    log.info("replay video ready: %d frames @ %d fps (%.0f s), %d unreadable packets skipped, %.0f s",
             state["n"], fps, state["n"] / fps, skipped, time.time() - t0)
    return dst
