"""MAVIS's voice: the serialized coaching speech channel and reading-lesson narration.

One TTS worker thread (tts_pool) so coaching lines never overlap; speak_gen
bumps on stop_speech() so a line queued before a stop / restart is dropped.
Reading checklists use their own worker + cache so they never queue behind
live coaching. No TTS engine: lines go to the log / transcript instead."""
from __future__ import annotations
import asyncio
import concurrent.futures
import logging
import tempfile
import wave
from pathlib import Path

from runtime.bootstrap import READING_LESSONS
from voice.tts import TTS

log = logging.getLogger("main")     # same logger name as before the move: log lines unchanged


def _wav_seconds(path) -> float:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / w.getframerate()
    except Exception:
        return 3.0


def build_speech(rt):
    """Creates the TTS workers / temp dir (at startup, in the same order as
    before) and publishes speak / stop_speech / reading_say / init_tts on rt."""
    tts_dir = Path(tempfile.mkdtemp(prefix="ai_tts_"))
    tts_seq = {"n": 0}
    tts_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="mavis-tts")
    # Separate worker + cache for the read-only checklists: they must not queue
    # behind live in-lesson coaching narration on tts_pool, and replaying the
    # same checkpoint shouldn't re-run CPU inference every click. Cache and wav
    # names are keyed by (lesson, checkpoint) so ids may repeat across lessons.
    reading_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="mavis-tts-reading")
    reading_audio_cache: dict[tuple[str, str], str] = {}
    to_tablet = rt.cfg["voice"].get("output", "tablet") == "tablet"

    async def init_tts():
        """Voice engines (Kokoro) can take a minute to load - do it in the
        background so the web UI is reachable immediately."""
        rt.tts = await rt.loop.run_in_executor(None, TTS, rt.cfg["voice"])
        rt.tts_ready.set()

    async def speak(text: str):
        if rt.state.get("muted"):
            return
        gen = rt.speak_gen["n"]
        await rt.tts_ready.wait()
        if gen != rt.speak_gen["n"]:
            return
        if not rt.tts or not rt.tts.engine:
            log.warning("TTS unavailable. Coaching line not spoken: %s", text[:80])
            return
        rt.state["speaking"] = True
        try:
            if to_tablet:
                tts_seq["n"] += 1
                wav = tts_dir / f"say_{tts_seq['n']}.wav"
                try:
                    ok = await asyncio.wait_for(
                        rt.loop.run_in_executor(tts_pool, rt.tts.synth, text, str(wav)),
                        timeout=25.0)
                except asyncio.TimeoutError:
                    log.warning("TTS synth timed out: %s", text[:80])
                    ok = False
                if gen != rt.speak_gen["n"]:
                    return
                if ok:
                    dur = _wav_seconds(wav)
                    log.info("TTS -> tablet %s (%.1fs)", wav.name, dur)
                    rt.state["audio"] = {"url": f"/tts/{wav.name}", "seq": tts_seq["n"]}
                    # wait out the line, unless stop_speech() cuts it off (the
                    # tablet switches to the next published line straight away)
                    end = rt.loop.time() + dur + 0.3
                    while rt.loop.time() < end and gen == rt.speak_gen["n"]:
                        await asyncio.sleep(min(0.1, end - rt.loop.time()))
                else:
                    log.warning("TTS synth failed. No audio: %s", text[:80])
                    await asyncio.sleep(min(6.0, 0.4 + len(text) / 15))  # console mode
            else:
                await rt.loop.run_in_executor(None, rt.tts.speak, text)
        finally:
            if gen == rt.speak_gen["n"]:
                rt.state["speaking"] = False

    def stop_speech():
        rt.speak_gen["n"] += 1
        rt.state["speaking"] = False
        rt.state.pop("audio", None)

    async def reading_say(lesson_id: str = "", checkpoint_id: str = ""):
        """On-demand narration for one reading-lesson checkpoint (menu-level
        modal, not gated by 'inlesson' like the coaching audio channel), reuses
        the same tts engine instance as the coaching voice, just delivered
        straight back to the caller instead of via state['audio']."""
        module = READING_LESSONS.get(lesson_id)
        if not module:
            return {"ok": False}
        cp = module.find(checkpoint_id)
        if not cp:
            return {"ok": False}
        cached = reading_audio_cache.get((lesson_id, checkpoint_id))
        if cached:
            return {"ok": True, "url": cached}
        await rt.tts_ready.wait()
        if not rt.tts or not rt.tts.engine:
            return {"ok": False}
        wav = tts_dir / f"reading_{lesson_id}_{checkpoint_id}.wav"
        step = module.CHECKPOINTS.index(cp) + 1
        text = module.speech_text(cp, step)
        ok = await rt.loop.run_in_executor(reading_pool, rt.tts.synth, text, str(wav))
        if not ok:
            return {"ok": False}
        url = f"/tts/{wav.name}"
        reading_audio_cache[(lesson_id, checkpoint_id)] = url
        return {"ok": True, "url": url}

    rt.init_tts = init_tts
    rt.reading_say = reading_say
    rt.speak = speak
    rt.stop_speech = stop_speech
    rt.tts_dir = tts_dir
    rt.tts_pool = tts_pool
