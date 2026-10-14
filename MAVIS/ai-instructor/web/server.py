"""FastAPI web layer: serves the tablet UI, streams state over WebSocket,
accepts typed Q&A, voice enrolment / PTT, and presenter demo actions."""
from __future__ import annotations
import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

log = logging.getLogger(__name__)
HERE = Path(__file__).parent

class ChatMsg(BaseModel):
    text: str


class SessionCfg(BaseModel):
    mode: str = "visitor"      # visitor | trainee | pilot | exam (legacy: casual|serious)
    jargon: str = "layman"     # layman | pilot
    tone: str = "warm"         # warm | drill
    reference: str = "planned" # planned | maverick | iceman | ...
    reference_recording: bool = False  # instructor capture: one circuit, full stop
    trainee_name: str = ""     # display name for debrief / scorecard
    free_flight: bool = False  # Free Flight menu: the session is logged as free flight, not as a lesson


class RefCfg(BaseModel):
    id: str = "planned"


class LessonSel(BaseModel):
    id: str = ""


class CheckpointMsg(BaseModel):
    id: str = ""
    lesson: str = "lesson00_preflight"


class NameMsg(BaseModel):
    name: str = ""
    given: str = ""
    family: str = ""


class PinMsg(BaseModel):
    pin: str = ""


class RunRename(BaseModel):
    display_name: str = ""


class InstructorBody(BaseModel):
    callsign: str = ""
    pin: str = ""


class InstructorPatch(BaseModel):
    callsign: str = ""
    pin: str = ""


def build_app(state: dict, actions: dict, media_dir: str, tts_dir: str = "",
              voice_store=None, stt=None, voice_cfg: dict | None = None,
              app_cfg: dict | None = None) -> FastAPI:
    """actions: dict of async callables: start, skip, night, restart, chat(text)
    voice_store / stt: optional local enrolment + Whisper pipeline (§16)."""
    app = FastAPI(title="AI Flight Instructor")
    vcfg = voice_cfg or {}
    enrol_min_s = float(vcfg.get("enrol_min_seconds", 2.0))
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    if tts_dir and Path(tts_dir).is_dir():
        app.mount("/tts", StaticFiles(directory=tts_dir), name="tts")
    mp = Path(media_dir)
    _MEDIA_STEMS = ("splash-bg", "menu-bg", "mode-visitor", "mode-trainee", "mode-pilot")
    _MEDIA_EXTS = (".jpg", ".jpeg", ".png", ".webp")
    if mp.is_dir():
        @app.get("/media/manifest")
        async def media_manifest():
            """Optional menu/splash images. Client loads only files that exist (no 404 spam)."""
            assets: dict[str, str] = {}
            for stem in _MEDIA_STEMS:
                for ext in _MEDIA_EXTS:
                    p = mp / f"{stem}{ext}"
                    if p.is_file():
                        assets[stem] = f"/media/{p.name}"
                        break
            return assets

        app.mount("/media", StaticFiles(directory=mp), name="media")
    else:
        log.warning("media dir %s not found - avatar videos won't load", mp)

    @app.get("/")
    async def index():
        # Always revalidate, tablet UI is edited in place during development
        return FileResponse(
            HERE / "static" / "index.html",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate",
                     "Pragma": "no-cache"},
        )

    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        try:
            while True:
                await sock.send_json(state)
                await asyncio.sleep(0.2)
        except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
            pass
        finally:
            try:
                await sock.close()
            except Exception:
                pass

    @app.post("/chat")
    async def chat(msg: ChatMsg):
        # Exam (and any policy with chat:false) rejects typed Q&A server-side too
        asyncio.create_task(actions["chat"](msg.text.strip()[:2000]))
        return {"ok": True}

    @app.post("/session")
    async def session(cfg: SessionCfg):
        """Main-menu mode/jargon selection: visitor|trainee|pilot|exam + jargon."""
        fn = actions.get("configure_session")
        if fn:
            await fn(cfg.mode, cfg.jargon, cfg.reference, cfg.reference_recording,
                     cfg.trainee_name.strip(), cfg.tone, cfg.free_flight)
        return {"ok": True}

    @app.post("/lesson/select")
    async def lesson_select(msg: LessonSel):
        """Curriculum sidebar picked a flying lesson; the server reloads it."""
        fn = actions.get("select_lesson")
        if not fn:
            return {"ok": False}
        return await fn(msg.id.strip())

    @app.post("/reference")
    async def reference(cfg: RefCfg):
        fn = actions.get("set_reference")
        if fn:
            await fn(cfg.id)
        return {"ok": True}

    @app.post("/reading/say")
    async def reading_say(msg: CheckpointMsg):
        """Narration for one checkpoint of a reading-kind lesson (walk-around,
        cockpit familiarisation)."""
        fn = actions.get("reading_say")
        if not fn:
            return {"ok": False}
        return await fn(msg.lesson, msg.id)

    @app.post("/instructor/unlock")
    async def instructor_unlock(msg: PinMsg):
        fn = actions.get("instructor_unlock")
        if not fn:
            return {"ok": False}
        result = await fn(msg.pin.strip())
        if isinstance(result, tuple):
            ok, inst = result
            return {"ok": ok, "instructor": inst}
        return {"ok": bool(result)}

    @app.post("/instructor/lock")
    async def instructor_lock():
        fn = actions.get("instructor_lock")
        if fn:
            await fn()
        return {"ok": True}

    @app.patch("/instructor/runs/{run_id}")
    async def instructor_rename(run_id: str, body: RunRename):
        fn = actions.get("instructor_rename")
        if not fn:
            return {"ok": False}
        ok = await fn(run_id, body.display_name.strip())
        return {"ok": ok}

    @app.delete("/instructor/runs/{run_id}")
    async def instructor_delete(run_id: str):
        fn = actions.get("instructor_delete")
        if not fn:
            return {"ok": False}
        ok = await fn(run_id)
        return {"ok": ok}

    @app.post("/instructor/instructors")
    async def instructor_add(body: InstructorBody):
        fn = actions.get("instructor_add")
        if not fn:
            return {"ok": False}
        ok = await fn(body.callsign.strip(), body.pin.strip())
        return {"ok": ok}

    @app.patch("/instructor/instructors/{inst_id}")
    async def instructor_update(inst_id: str, body: InstructorPatch):
        fn = actions.get("instructor_update")
        if not fn:
            return {"ok": False}
        ok = await fn(inst_id, body.callsign.strip(), body.pin.strip())
        return {"ok": ok}

    @app.delete("/instructor/instructors/{inst_id}")
    async def instructor_remove(inst_id: str):
        fn = actions.get("instructor_remove")
        if not fn:
            return {"ok": False}
        ok = await fn(inst_id)
        return {"ok": ok}

    @app.post("/score/submit")
    async def score_submit(msg: NameMsg):
        """Post the held scorecard to the leaderboard under this name."""
        fn = actions.get("score_submit")
        if fn:
            await fn(msg.name.strip()[:24], msg.given.strip()[:32], msg.family.strip()[:32])
        return {"ok": True}

    @app.post("/demo/{action}")
    async def demo(action: str):
        fn = actions.get(action)
        if not fn:
            return {"ok": False}
        try:
            await fn()
        except Exception as e:
            # still a 500 (callers treat it as failure), but with the reason in
            # the body so the menu can show "Couldn't connect to MSFS ... (Last
            # error: ...)" rather than a bare "Internal Server Error"
            log.exception("/demo/%s failed", action)
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
        return {"ok": True}

    # ---- voice enrolment + PTT (§16) ----
    @app.get("/voice/lookup")
    async def voice_lookup(name: str = ""):
        if not voice_store:
            return {"enrolled": False, "available": False}
        row = voice_store.lookup(name)
        return {
            "enrolled": bool(row),
            "available": True,
            "name": (row or {}).get("name") or name,
            "ts": (row or {}).get("ts"),
            "backend": getattr(voice_store.engine, "backend", None),
        }

    @app.get("/voice/config")
    async def voice_config():
        return {
            "enrol_seconds": int(vcfg.get("enrol_seconds", 10)),
            "enrol_min_seconds": enrol_min_s,
            "stt": bool(stt),
            "speaker": bool(voice_store),
        }

    @app.post("/voice/enrol")
    async def voice_enrol(name: str = Form(...), audio: UploadFile = File(...)):
        if not voice_store:
            return {"ok": False, "reason": "unavailable"}
        from voice.audio_util import prepare_utterance, to_wav_bytes
        data = await audio.read()
        pcm, reason = prepare_utterance(data, min_s=enrol_min_s)
        if pcm is None:
            return {"ok": False, "reason": reason or "bad_audio"}
        loop = asyncio.get_running_loop()
        ok, why, score = await loop.run_in_executor(None, voice_store.enrol, name, pcm)
        log.info("voice enrolment %s name=%r score=%s backend=%s",
                 "OK" if ok else f"FAILED({why})", name.strip()[:48], score,
                 getattr(voice_store.engine, "backend", "?"))
        if not ok:
            return {"ok": False, "reason": why}
        sample_b64 = None
        try:
            import base64
            max_playback = int(vcfg.get("enrol_seconds", 10)) * 16000
            sample_b64 = base64.b64encode(
                to_wav_bytes(pcm[: min(pcm.size, max_playback)])
            ).decode("ascii")
        except Exception:
            pass
        return {
            "ok": True,
            "name": name.strip()[:48],
            "score": score,
            "playback_wav_b64": sample_b64,
        }

    @app.post("/voice/ptt")
    async def voice_ptt(audio: UploadFile = File(...), name: str = Form("")):
        if not stt:
            return {"ok": False, "reason": "stt_unavailable"}
        from voice.audio_util import prepare_utterance
        data = await audio.read()
        pcm, reason = prepare_utterance(data, min_s=0.2, min_voiced=0.02, min_rms=0.002)
        if pcm is None:
            return {"ok": False, "reason": reason or "bad_audio"}

        loop = asyncio.get_running_loop()
        sim = None
        verify_reason = "not_enrolled"
        # Verify speaker before Whisper, rejected clips must not hold the STT lock.
        if voice_store and name.strip():
            accepted, sim, verify_reason = await loop.run_in_executor(
                None, voice_store.verify, name, pcm)
            if not accepted:
                log.info("PTT REJECTED speaker=%r sim=%.3f (threshold %.2f)",
                         name.strip(), sim if sim is not None else -1.0,
                         voice_store.threshold)
                return {"ok": False, "reason": "speaker_reject", "similarity": sim}

        try:
            text = await asyncio.wait_for(
                loop.run_in_executor(None, stt.transcribe, pcm), timeout=30.0)
        except asyncio.TimeoutError:
            log.warning("PTT transcription timed out (%.1fs audio)", len(pcm) / 16000.0)
            return {"ok": False, "reason": "stt_timeout"}
        log.info("PTT verify=%s sim=%s text=%r",
                 verify_reason,
                 f"{sim:.3f}" if sim is not None else "-",
                 (text or "").strip()[:120])
        if not text:
            return {"ok": False, "reason": "empty", "similarity": sim}
        text = text.strip()[:2000]
        asyncio.create_task(actions["chat"](text))
        return {
            "ok": True,
            "text": text,
            "similarity": sim,
            "verify": verify_reason,
        }

    # SOP upload -> LLM-drafted rules -> instructor verification -> aircraft
    # binding (sop/, docs/sop-rules.md). Optional: MAVIS runs without it if
    # its extra deps (pymupdf, asyncpg) aren't installed.
    try:
        from sop.routes import build_sop_router
        sop_router, sop_jobs, sop_store = build_sop_router(state, app_cfg, HERE.parent)
        app.include_router(sop_router)
        app.state.sop_store = sop_store

        @app.on_event("shutdown")
        async def _close_sop_store():
            await sop_store.close()
    except ImportError as e:
        log.warning("SOP rules disabled (missing dependency: %s)", e)

    return app
