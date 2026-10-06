"""AI Flight Instructor - entry point.

    python main.py                          # mock sim (config default)
    python main.py --sim msfs               # real MSFS via SimConnect
    python main.py --sim msfs --input udp   # MSFS + joystick PTT (hid_forwarder on sim PC)
"""
from __future__ import annotations
import argparse
import asyncio
import concurrent.futures
import logging
import math
import os
import signal
import re
import tempfile
import time
import uuid
import wave
from pathlib import Path

import yaml

APP_VERSION = "0.2.0"
import uvicorn

from adapters.input_hid import HIDInput, ControlSample
from coach.llm_coach import Coach
from coach import phrases
from core.events import CoachEvent
from core.lesson_engine import LessonEngine
from core.scoring import Scorer
from core.leaderboard import Leaderboard
from core.instructors import InstructorRegistry
from core.recorder import FlightRecorder
from core.lesson_catalog import flatten_lessons, lesson_meta, load_catalog
from core.familiarization import (FamiliarizationEngine, FamiliarizationScorer,
                                  field_center, is_familiarization, runway_start,
                                  start_position)
from lessons import lesson00_preflight, lesson00_cockpit

# Reading-kind lessons (catalog.json "kind": "reading"): narrated checklists with
# no flight telemetry, keyed by the same lesson id the catalog and sidebar use.
READING_LESSONS = {
    "lesson00_preflight": lesson00_preflight,
    "lesson00_cockpit": lesson00_cockpit,
}
from core.reference_profiles import ReferenceRegistry
from core.modes import policy_for, normalize_mode, allow_coach_event
from agent.tools import Tool, ToolRegistry
from agent.router import IntentRouter
from voice.tts import TTS
from voice.speaker import VoiceStore
from voice.stt import WhisperSTT
from web.server import build_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("main")
HERE = Path(__file__).parent

# Live ground track size cap (points per attempt, ~1/s). Past TRACK_MAX the
# older part is thinned 2:1 while the newest TRACK_KEEP_FULL stay full-rate.
TRACK_MAX = 3000
TRACK_KEEP_FULL = 1000


class EmbeddedServer(uvicorn.Server):
    """Run uvicorn inside asyncio.run(); honour should_exit for clean Ctrl+C."""

    async def serve(self, sockets=None):
        try:
            await self._serve(sockets)
        except asyncio.CancelledError:
            self.should_exit = True
            raise


def load_yaml(p): return yaml.safe_load(Path(HERE / p).read_text(encoding="utf-8"))


def make_adapter(cfg, lesson):
    kind = cfg["sim"]["adapter"]
    if kind == "mock":
        from adapters.sim_mock import MockAdapter
        aircraft = load_yaml(cfg["aircraft_profile"])
        return MockAdapter(lesson, aircraft=aircraft)
    if kind == "msfs":
        from adapters.sim_msfs import MSFSAdapter
        return MSFSAdapter(cfg["sim"]["host"], cfg["sim"]["port"],
                           weather=cfg["sim"].get("weather"))
    if kind == "replay":
        from adapters.sim_replay import ReplayAdapter
        rp = cfg["sim"].get("replay") or {}
        if not rp.get("file"):
            raise SystemExit("sim.replay.file must name the flight log CSV")
        return ReplayAdapter(HERE / rp["file"], lesson["airport"]["elevation_ft"],
                             session=rp.get("session"))
    raise SystemExit(f"unknown sim adapter: {kind}")


def replay_video_for_web(cfg) -> str | None:
    """Media-relative path of the browser-ready copy of the replay recording."""
    rp = cfg["sim"].get("replay") or {}
    if cfg["sim"]["adapter"] != "replay" or not rp.get("video"):
        return None
    media = HERE / cfg["web"]["media_dir"]
    src = media / rp["video"]
    if not src.exists():
        log.warning("replay video not found: %s (trainee view will show no signal)", src)
        return None
    from adapters.replay_video import prepare_video
    return prepare_video(src).relative_to(media).as_posix()


def make_engine(cfg, lesson, aircraft):
    """Circuit lessons run the phase detector; the familiarization lesson
    (type: familiarization) runs its own airborne exercise steps."""
    kind = FamiliarizationEngine if is_familiarization(lesson) else LessonEngine
    return kind(lesson, aircraft, sim_adapter=cfg["sim"]["adapter"])


def make_scorer(lesson, aircraft):
    return (FamiliarizationScorer if is_familiarization(lesson) else Scorer)(lesson, aircraft)


def lesson_state(lesson) -> dict:
    """state["lesson"]: what the web map needs to draw this lesson."""
    out = {"runway": lesson["runway"], "pattern": lesson["pattern"],
           "airport": lesson["airport"], "scoring": lesson.get("scoring", {})}
    if is_familiarization(lesson):
        lat, lon = field_center(lesson)
        out.update(type="familiarization", field={"lat": lat, "lon": lon},
                   finish_radius_nm=float((lesson.get("finish") or {}).get("radius_nm", 2.0)))
    return out


def alt_scale_max(lesson) -> int:
    start = (lesson.get("airborne_start") or {}).get("alt_msl_ft")
    return int((start or 0) * 1.4) or int(lesson["pattern"]["altitude_msl_ft"] * 1.5)


def lesson_for_sim(cfg, sim, lesson):
    """A replayed flight is coached and scored against the runway end it was
    actually flown from; the map and pattern follow the same geometry."""
    if cfg["sim"]["adapter"] == "replay":
        from adapters.sim_replay import flown_from_reciprocal, reciprocal_runway
        if flown_from_reciprocal(sim.rows, lesson):
            lesson = reciprocal_runway(lesson)
            log.info("replay flown from runway %s: lesson geometry reversed",
                     lesson["runway"]["ident"])
    return lesson


async def amain(cfg):
    lesson = load_yaml(cfg["lesson"])
    aircraft = load_yaml(cfg["aircraft_profile"])
    sim = make_adapter(cfg, lesson)
    # msfs's connect() is the real SimConnect handshake, which needs MSFS
    # already running with a flight loaded -- deferred to start() (below,
    # with retries) so the web server/menu/reading-lessons all come up
    # regardless of MSFS's state. mock/replay have no such dependency, so
    # they still connect immediately as before.
    if cfg["sim"]["adapter"] != "msfs":
        sim.connect()
    lesson = lesson_for_sim(cfg, sim, lesson)

    hid = None
    mode = cfg["input"].get("mode", "none")
    if mode == "local_hid":
        hid = HIDInput([load_yaml(p) for p in cfg["input"]["profiles"]])
        hid.connect()
    elif mode == "udp":
        from adapters.input_udp import UDPInput
        hid = UDPInput(cfg["input"].get("udp_port", 6947))
        hid.connect()

    flap_style = aircraft.get("flaps", {}).get("control_style", "notches")
    phrases.configure(flap_style, "layman")
    # main-menu selection: visitor/trainee/pilot/exam policies; jargon still
    # independent (Settings). Defaults are safe before the menu sends anything.
    _default_policy = policy_for("visitor")
    session = {"mode": "visitor", "jargon": "layman", "tone": "warm", "name": "",
               "reference": "planned", "instructor_unlocked": False, "policy": _default_policy,
               "session_id": None}
    instructor_pin = str(cfg.get("instructor", {}).get("pin", "1234"))
    engine = make_engine(cfg, lesson, aircraft)
    scorer = make_scorer(lesson, aircraft)
    recorder = FlightRecorder()          # §15: capture each run (groundwork for reference re-score)
    board = Leaderboard(HERE / "data" / "leaderboard.json")
    board.seed_demo_if_empty()
    lesson_id = Path(cfg["lesson"]).stem
    catalog = load_catalog()
    refs = ReferenceRegistry(lesson, lesson_id)
    instructors = InstructorRegistry(instructor_pin)
    score_seq = {"n": 0}
    run_gen = {"n": 0}          # bumped on restart, stale finish_lesson tasks ignore
    pending: dict = {"card": None, "posted": False}
    scored = {"done": False}
    tts: TTS | None = None
    tts_ready = asyncio.Event()
    speak_gen = {"n": 0}
    transcript: list[dict] = []
    # track is a list of SEGMENTS; a retry/teleport starts a new segment so
    # old attempts render dim and there's no false line across the jump
    state: dict = {"phase": engine.phase, "circuit": 1, "total_circuits": int(lesson.get("total_circuits", 1)),
                   "session_id": None,
                   # True from Start until quit/restart -- lets the web UI
                   # reattach to a running session after navigating away.
                   "lesson_running": False,
                   "transcript": [],
                   "speaking": False, "night": False, "track": [[]],
                   "theme": cfg["web"].get("theme", {}),
                   "monitor": cfg["web"].get("monitor_sources", []),
                   # replay: the trainee-view clip is the recording of the replayed flight,
                   # started by the client at the same instant playback begins
                   "demo_video": replay_video_for_web(cfg),
                   "scales": {"speed_max": aircraft["v_speeds"].get("vne", 163),
                              "alt_max": alt_scale_max(lesson)},
                   "flaps_style": flap_style, "session": session,
                   "scorecard": None, "leaderboard": board.snapshot(),
                   "lesson_id": lesson_id,
                   "lesson_catalog": catalog,
                   "app_version": APP_VERSION,
                   "lesson_list": flatten_lessons(catalog),
                   "reading_lessons": {lid: mod.as_state() for lid, mod in READING_LESSONS.items()},
                   "active_lesson": lesson_meta(catalog, lesson_id) or {"id": lesson_id, "title": lesson.get("name", lesson_id), "available": True},
                   "instructor_unlocked": False,
                   "instructor": None,
                   "instructors": [],
                   **refs.payload_for_state(),
                   "reference": {"id": "planned", "name": "Planned circuit"},
                   "reference_track": None,
                   "lesson": lesson_state(lesson)}
    loop = asyncio.get_event_loop()

    tts_dir = Path(tempfile.mkdtemp(prefix="ai_tts_"))
    tts_seq = {"n": 0}
    tts_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="mavis-tts")
    # Separate worker + cache for the read-only checklists: they must not queue
    # behind live in-lesson coaching narration on tts_pool, and replaying the
    # same checkpoint shouldn't re-run CPU inference every click. Cache and wav
    # names are keyed by (lesson, checkpoint) so ids may repeat across lessons.
    reading_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="mavis-tts-reading")
    reading_audio_cache: dict[tuple[str, str], str] = {}
    to_tablet = cfg["voice"].get("output", "tablet") == "tablet"

    async def init_tts():
        """Voice engines (Kokoro) can take a minute to load - do it in the
        background so the web UI is reachable immediately."""
        nonlocal tts
        tts = await loop.run_in_executor(None, TTS, cfg["voice"])
        tts_ready.set()

    async def speak(text: str):
        if state.get("muted"):
            return
        gen = speak_gen["n"]
        await tts_ready.wait()
        if gen != speak_gen["n"]:
            return
        if not tts or not tts.engine:
            log.warning("TTS unavailable. Coaching line not spoken: %s", text[:80])
            return
        state["speaking"] = True
        try:
            if to_tablet:
                tts_seq["n"] += 1
                wav = tts_dir / f"say_{tts_seq['n']}.wav"
                try:
                    ok = await asyncio.wait_for(
                        loop.run_in_executor(tts_pool, tts.synth, text, str(wav)),
                        timeout=25.0)
                except asyncio.TimeoutError:
                    log.warning("TTS synth timed out: %s", text[:80])
                    ok = False
                if gen != speak_gen["n"]:
                    return
                if ok:
                    dur = _wav_seconds(wav)
                    log.info("TTS -> tablet %s (%.1fs)", wav.name, dur)
                    state["audio"] = {"url": f"/tts/{wav.name}", "seq": tts_seq["n"]}
                    await asyncio.sleep(dur + 0.3)
                else:
                    log.warning("TTS synth failed. No audio: %s", text[:80])
                    await asyncio.sleep(min(6.0, 0.4 + len(text) / 15))  # console mode
            else:
                await loop.run_in_executor(None, tts.speak, text)
        finally:
            if gen == speak_gen["n"]:
                state["speaking"] = False

    def stop_speech():
        speak_gen["n"] += 1
        state["speaking"] = False
        state.pop("audio", None)

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
        await tts_ready.wait()
        if not tts or not tts.engine:
            return {"ok": False}
        wav = tts_dir / f"reading_{lesson_id}_{checkpoint_id}.wav"
        step = module.CHECKPOINTS.index(cp) + 1
        text = module.speech_text(cp, step)
        ok = await loop.run_in_executor(reading_pool, tts.synth, text, str(wav))
        if not ok:
            return {"ok": False}
        url = f"/tts/{wav.name}"
        reading_audio_cache[(lesson_id, checkpoint_id)] = url
        return {"ok": True, "url": url}

    coach = Coach(cfg["coach"], aircraft, speak, transcript)
    coach.live_tel = lambda: state.get("tel")   # for stale-callout suppression
    await coach.check_llm()

    # ---------- actions (web) ----------
    started = asyncio.Event()

    def _lesson_text(block: dict | None) -> str:
        """Lesson-authored line in the session's jargon ({layman, pilot} variants)."""
        if not block:
            return ""
        return block.get(session.get("jargon", "layman")) or block.get("layman") or ""

    async def _briefing_lines():
        own = _lesson_text(lesson.get("briefing"))
        if is_familiarization(lesson):
            alt = (lesson.get("airborne_start") or {}).get("alt_msl_ft", 0)
            await coach.say(own.replace("{alt}", f"{alt:,}"))
            engine.start_flying()
            return
        if session.get("reference_recording"):
            await coach.say(phrases.render("BRIEFING_1_REF", {}))
        elif own:
            await coach.say(own)
        else:
            await coach.say(phrases.render("BRIEFING_1", {}))
        await coach.say(phrases.render("BRIEFING_2", {}))
        engine.start_flying()

    def _lesson_clock() -> tuple[int, int]:
        """Sim local time the lesson is flown at: its night_event time when the
        lesson starts at night, otherwise mid-morning."""
        night = lesson.get("night_event") or {}
        if night.get("at") == "start":
            h, m = map(int, night["local_time"].split(":"))
            return h, m
        return 10, 0

    async def _apply_lesson_clock():
        h, m = _lesson_clock()
        if not await loop.run_in_executor(None, sim.set_local_time, h, m):
            log.error("sim rejected time change - use fallback keybind")

    async def _connect_msfs_with_retries(attempts: int = 5, delay_s: float = 2.0) -> None:
        """Only called from start() -- i.e. only when a session is actually
        about to be minted, not at process boot. Raises (propagating as a
        500 through POST /demo/start) if every attempt fails, so the
        frontend's existing error handling shows it and no session gets
        minted; pressing Start again retries the whole thing."""
        last_err = None
        for i in range(attempts):
            try:
                await loop.run_in_executor(None, sim.connect)
                return
            except Exception as e:
                last_err = e
                log.warning("SimConnect connect attempt %d/%d failed: %s", i + 1, attempts, e)
                if getattr(e, "no_retry", False):
                    raise                   # e.g. MSFS at its main menu: say so now
                if i < attempts - 1:
                    await asyncio.sleep(delay_s)
        raise ConnectionError(
            f"Couldn't connect to MSFS after {attempts} attempts -- make sure it's "
            f"running with a flight loaded, then press Start again. (Last error: {last_err})"
        ) from last_err

    def _orient_lesson_to_aircraft(tel) -> None:
        """Live MSFS: the lesson YAML describes one runway end (e.g. RWY 02),
        but MSFS may spawn the aircraft on the other end (RWY 20, e.g. for
        wind). Without this the planned circuit, map and every turn callout
        are mirrored 180 deg. Same idea as replay's lesson_for_sim(), decided
        from the aircraft's heading -- but only when it's actually lined up on
        the runway (on the apron the heading says nothing about the runway)."""
        nonlocal lesson, engine, scorer, refs
        rw = lesson["runway"]
        th = math.radians(rw["heading_true"])
        lat0, lon0 = rw["threshold"]["lat"], rw["threshold"]["lon"]
        n = (tel.lat - lat0) * 111320.0
        e = (tel.lon - lon0) * 111320.0 * math.cos(math.radians(lat0))
        along = e * math.sin(th) + n * math.cos(th)
        cross = -e * math.cos(th) + n * math.sin(th)
        if abs(cross) > 150 or not (-300 <= along <= rw["length_m"] + 300):
            log.warning("aircraft not on runway %s at start (along %.0f m, cross %.0f m) -- "
                        "keeping lesson geometry as configured", rw.get("ident"), along, cross)
            return
        diff = abs((tel.hdg - rw["heading_true"] + 180.0) % 360.0 - 180.0)
        if diff <= 90.0:
            return
        from adapters.sim_replay import reciprocal_runway
        lesson = reciprocal_runway(lesson)
        total = engine.detector.total_circuits          # keep configure_session's choice
        engine = make_engine(cfg, lesson, aircraft)
        engine.detector.total_circuits = total
        scorer = make_scorer(lesson, aircraft)
        refs = ReferenceRegistry(lesson, lesson_id)
        state["lesson"] = lesson_state(lesson)
        state.update(**refs.payload_for_state())
        _apply_reference(session.get("reference", "planned"))
        log.info("aircraft is lined up on runway %s (heading %.0f): lesson geometry reversed",
                 lesson["runway"]["ident"], tel.hdg)

    def _fam_freezes() -> bool:
        """Familiarization holds the aircraft still while it explains -- not in
        silent (Exam) coaching, where nothing is explained."""
        pol = session.get("policy") or {}
        return (is_familiarization(lesson) and bool(lesson.get("freeze_during_instructions"))
                and pol.get("coaching") != "silent")

    async def _set_frozen(on: bool) -> None:
        fn = getattr(sim, "set_frozen", None)
        if fn and not await loop.run_in_executor(None, fn, on):
            log.warning("sim did not confirm freeze=%s", on)

    async def _fam_instruct(ev):
        """Speak one familiarization step with the aircraft frozen, then hand
        over: "You have control", unfreeze, and start the step's clock."""
        gen, eng = run_gen["n"], engine
        try:
            await _set_frozen(True)
            await coach.say(phrases.render("LESSON_NOTE", ev.data), phase=ev.data.get("phase"))
            if gen == run_gen["n"] and started.is_set():
                await coach.say(phrases.render("FAM_YOU_HAVE_CONTROL", {}),
                                phase=ev.data.get("phase"))
        finally:
            if gen == run_gen["n"] and eng is engine:
                await _set_frozen(False)
                eng.release()

    async def _fam_finish():
        """End of the exercise: hold the aircraft while it's scored, then put
        it back at the start of the runway, stopped, ready for whatever's next."""
        gen = run_gen["n"]
        await _set_frozen(True)
        try:
            await finish_lesson(False)
        except Exception:
            log.exception("finishing the familiarization lesson failed")
        if gen != run_gen["n"]:
            return                      # Fly again / reset already took over
        await _set_frozen(False)
        fn = getattr(sim, "set_position", None)
        lat, lon, hdg = runway_start(lesson)
        elev = lesson["airport"]["elevation_ft"]
        ok = fn is not None and await loop.run_in_executor(
            None, lambda: fn(lat, lon, elev, hdg, 0, throttle_pct=0, on_ground=True))
        if ok:
            log.info("familiarization done: aircraft back at the start of runway %s",
                     lesson["runway"]["ident"])
        else:
            log.warning("couldn't put the aircraft back on the runway after the lesson")

    async def _airborne_start():
        """Familiarization: place the aircraft in the air before the session
        starts. Raises (a 500 on POST /demo/start, shown on the start menu)
        if the sim can't do it, so no session is minted."""
        a = lesson.get("airborne_start") or {}
        lat, lon = start_position(lesson)
        fn = getattr(sim, "set_position", None)
        ok = fn is not None and await loop.run_in_executor(
            None, lambda: fn(lat, lon, a["alt_msl_ft"], a["heading_true"],
                             a["ias_kt"], throttle_pct=a.get("throttle_pct")))
        if not ok:
            raise RuntimeError("Couldn't place the aircraft in the air for the "
                               "familiarization flight -- check MAVIS's log, then press Start again.")
        # wait for telemetry to show the new position, so the first exercise
        # snapshots the airborne altitude, not the ramp
        deadline = time.time() + 6.0
        while time.time() < deadline:
            tel = await loop.run_in_executor(None, sim.get_telemetry)
            if not tel.on_ground and tel.alt_agl > 500:
                return
            await asyncio.sleep(0.2)
        log.warning("airborne start: telemetry still not airborne after 6 s, continuing")

    async def start():
        if started.is_set():
            return
        if cfg["sim"]["adapter"] == "msfs":
            await _connect_msfs_with_retries()
            if not is_familiarization(lesson):
                _orient_lesson_to_aircraft(await loop.run_in_executor(None, sim.get_telemetry))
        if is_familiarization(lesson):
            await _airborne_start()
            if _fam_freezes():
                await _set_frozen(True)     # held through the briefing
        state["look_at"] = None
        started.set()
        session["session_id"] = str(uuid.uuid4())
        state["session_id"] = session["session_id"]
        state["lesson_running"] = True
        if (lesson.get("night_event") or {}).get("at") == "start":
            await _apply_lesson_clock()
            engine.mark_night_done()
        # Never block mock / lesson start on briefing TTS, warmup can hog the GPU.
        if hasattr(sim, "release"):        # mock: start the autopilot immediately
            sim.release()
        pol = session.get("policy") or {}
        if pol.get("coaching") == "turn_by_turn":
            asyncio.create_task(_briefing_lines())
        else:
            engine.start_flying()
        tel0 = await loop.run_in_executor(None, sim.get_telemetry)
        checkpoints[engine.detector.ORDER[1]] = {
            "tel": tel0.as_dict(), "circuit": engine.circuit,
            "blob": sim.save_state() if hasattr(sim, "save_state") else None}

    async def night_sequence():
        if not lesson.get("night_event"):
            return
        engine.mark_night_done()
        silent = (session.get("policy") or {}).get("coaching") == "silent"
        if not silent:
            await coach.say(phrases.render("NIGHT_ANNOUNCE", {}))
        h, m = map(int, lesson["night_event"]["local_time"].split(":"))
        if not await loop.run_in_executor(None, sim.set_local_time, h, m):
            log.error("sim rejected time change - use fallback keybind")
        await asyncio.sleep(2)
        if not silent:
            await coach.say(phrases.render("NIGHT_AFTER", {}))

    # weather words -> config.yaml sim.weather preset; first match wins, so
    # "thunderstorm" is a storm before it's rain
    WEATHER_WORDS = [(r"thunder|storm|lightning", "storm"), (r"rain|shower|drizzle|wet", "rain"),
                     (r"fog|mist|haze|murk", "fog"), (r"snow", "snow"),
                     (r"cloud|overcast|grey|gray|gloomy", "cloudy"),
                     (r"clear|sunny|blue sk|nice weather|good weather|fine weather", "clear")]
    WEATHER_HINT = (r"(thunder|storm|lightning|rain|shower|drizzle|fog|mist|haze|snow|cloud|"
                    r"overcast|sunny|clear sk|blue sk|weather)")
    # a request, not a question: "can we get some fog" yes, "do we have rain?" no
    ENV_ASK_RE = (r"\b(change|switch|make|set|turn|try|add|bring|give|want|get|put|"
                  r"see what|looks? like|show me|let'?s)\b")
    QUESTION_RE = r"^\s*(is|are|will|was|were|what'?s?|why|how|does|do|did|when|where|which)\b"
    TIMES = {"dusk": (19, 15), "sunset": (19, 10), "sunrise": (7, 0), "dawn": (6, 45),
             "morning": (8, 0), "noon": (12, 0), "midday": (12, 0), "night": (20, 30),
             "midnight": (0, 0), "daytime": (10, 0), "evening": (19, 45),
             "afternoon": (15, 0)}
    # word boundaries: "set" must not match inside "sunset"
    CHANGE_RE = r"\b(change|switch|make|set|turn|try|see what|looks? like|show me)\b"
    TIME_QUERY_RE = (r"(what'?s?|tell me|do you know).{0,25}\b(time|clock)\b|"
                     r"\bwhat time is it\b|\bcurrent time\b|\bthe time\s*(now|right now)?\s*\??\s*$")
    TIME_HINT = (r"(time|clock|hrs|o'?clock|[ap]\.?m\b|\d{1,2}[:.]\d{2}|night|day(time)?|dusk|"
                 r"dawn|sunset|sunrise|morning|noon|midnight|midday|evening|afternoon)")

    def parse_clock(t: str):
        """Deterministic parse of explicit times: 12:45pm, 0630, 1400 hrs, 7 pm."""
        import re
        m = re.search(r"\b(\d{1,2})[:.](\d{2})\s*([ap]\.?m\.?)?\b", t)
        if m:
            h, mi = int(m.group(1)), int(m.group(2))
            ap = (m.group(3) or "").replace(".", "")
            if ap == "pm" and h < 12: h += 12
            if ap == "am" and h == 12: h = 0
            if 0 <= h < 24 and 0 <= mi < 60: return h, mi
        m = re.search(r"\b(\d{3,4})\s*(hrs|hours|h)\b", t)
        if m:
            h, mi = divmod(int(m.group(1)), 100)
            if 0 <= h < 24 and 0 <= mi < 60: return h, mi
        m = re.search(r"\b(\d{1,2})\s*([ap]\.?m\.?)\b", t)
        if m:
            h = int(m.group(1)) % 12 + (12 if "p" in m.group(2) else 0)
            if 0 <= h < 24: return h, 0
        # bare 24-hour time after to/at/for: "set it to 1400", "at 0630"
        m = re.search(r"\b(?:to|at|for)\s+(\d{2})(\d{2})\b", t)
        if m:
            h, mi = int(m.group(1)), int(m.group(2))
            if 0 <= h < 24 and 0 <= mi < 60: return h, mi
        return None

    async def set_sim_time(h: int, m: int):
        await coach.say(f"Of course. Setting the clock to {h:02d}{m:02d} hours, watch the sky.")
        ok = await loop.run_in_executor(None, sim.set_local_time, h, m)
        if not ok:
            await coach.say("Hmm, the simulator didn't accept that, we'll try again on the ground.")

    async def chat(text: str):
        if not text:
            return
        if not session.get("policy", {}).get("chat", True):
            return
        import re
        await coach.say(text, who="trainee")
        t = text.lower()
        # --- UI intents: controls guide ---
        if re.search(r"(close|hide|remove).{0,20}(guide|diagram|picture)", t):
            state["ui_cmd"] = {"action": "close_guide", "seq": time.time()}
            await coach.say("Closed. Eyes back outside, you're doing great.")
            return
        if re.search(r"(show|open|where|see|bring up).{0,30}(guide|controls|throttle|stick|brake)", t):
            state["ui_cmd"] = {"action": "open_guide", "seq": time.time()}
            await coach.say("Here's the controls guide. Just say 'close the guide' when you're done.")
            return
        if re.search(r"\bflaps?\b", t) and re.search(r"(what|why|for|explain|tell me|mean|do)", t):
            await coach.say("Flaps are panels on the wing that hinge down, they let you fly "
                            "slower and more safely for landing, and we use them on downwind, "
                            "base, and final. Eyes outside, you're doing fine.")
            return
        # --- retry intents: "let me try the landing again", "go back" ---
        if re.search(r"((try|do|fly|attempt).{0,25}again)|go back|redo|rewind|"
                     r"checkpoint|one more (try|go|time)|mess(ed)? (that |it )?up", t):
            phase = None
            for word, ph in PHASE_WORDS.items():
                if word in t:
                    phase = ph
                    break
            asyncio.create_task(retry(phase))
            return
        # --- time query: "what time is it?", read sim clock, don't guess ---
        if re.search(TIME_QUERY_RE, t) and not re.search(CHANGE_RE, t):
            lt = state.get("tel", {}).get("local_time_h")
            if lt is not None:
                h, m = int(lt) % 24, int(round((lt % 1) * 60)) % 60
                await coach.say(f"In the simulator it's {h:02d}{m:02d} hours, "
                                "that's the clock on your map. Eyes outside.")
            else:
                import datetime as _dt
                now = _dt.datetime.now()
                await coach.say(f"It's {now.strftime('%H%M')} hours here on the instructor station.")
            return
        # --- environment intents: time of day and/or weather ---
        # Requests only ("is it sunset yet?" is a question, not a change). One
        # sentence can ask for both: "make it a stormy night".
        asking = bool(re.search(ENV_ASK_RE, t)) and not re.search(QUESTION_RE, t)
        weather_ask = asking and bool(re.search(WEATHER_HINT, t))
        wcond = (next((c for pat, c in WEATHER_WORDS if re.search(pat, t)), None)
                 if weather_ask else None)

        async def _time_then_weather(h: int, m: int):
            await set_sim_time(h, m)
            if wcond:
                await _tool_weather(wcond)

        # 1) explicit clock time ("12:45pm", "1400 hrs") - deterministic, no LLM needed
        hm = parse_clock(t)
        if hm and not re.search(QUESTION_RE, t) and re.search(CHANGE_RE + r"|\b(at|to)\b", t):
            await _time_then_weather(*hm)
            return
        # 2) named time of day ("make it night", "try dusk") - deterministic;
        #    longest word first so "midnight" isn't "night", "afternoon" isn't "noon"
        if asking:
            for word in sorted(TIMES, key=len, reverse=True):
                if re.search(rf"\b{word}\b", t):
                    await _time_then_weather(*TIMES[word])
                    return
        # 3) fuzzy but time-flavoured ("just before the sun goes down") - ask the LLM
        if asking and re.search(TIME_HINT, t) and coach.llm_ok:
            hm = await coach.extract_time(text)
            if hm:
                await _time_then_weather(*hm)
                return
        # weather on its own ("make it rain", "can we get some fog")
        if weather_ask:
            if wcond:
                await _tool_weather(wcond)
            else:
                await coach.say("I can set clear skies, clouds, rain, a storm, fog or snow, "
                                "and change the time of day. Which would you like?")
            return
        # --- agentic fallback: let the local LLM pick ONE safe tool for a fuzzy
        #     request the rules above didn't catch (deterministic path already ran) ---
        decision = await router.decide(
            text, {"phase": engine.phase, "time": state.get("tel", {}).get("local_time_h")},
            allow_regex=False)
        if (decision.kind == "tool" and decision.tool in ("set_weather", "set_time_of_day")
                and re.search(QUESTION_RE, t)):
            decision.kind = "answer"     # "is it going to rain?" asks, it doesn't order
        if decision.kind == "tool":
            tool = tools.get(decision.tool)
            log.info("agent tool -> %s %s (%s)", decision.tool, decision.args, decision.source)
            if tool:
                await tool.handler(**(decision.args or {}))
                return
        # --- everything else: free Q&A via the LLM ---
        tel = state.get("tel", {})
        snippet = {k: tel.get(k) for k in ("ias", "alt_msl", "hdg", "flaps_deg", "local_time_h")}
        await coach.answer(text, snippet, engine.phase)

    async def restart():
        stop_speech()
        run_gen["n"] += 1
        engine.restart()
        scorer.reset()
        recorder.reset()
        scored["done"] = False
        coach.resume()
        session.pop("reference_recording", None)
        engine.detector.total_circuits = int(lesson.get("total_circuits", 1))
        state["total_circuits"] = engine.detector.total_circuits
        _discard_card()
        state["speaking"] = False
        started.clear()
        state["lesson_running"] = False
        intro_shown.clear()
        checkpoints.clear()
        state["track"] = [[]]
        state["look_at"] = None
        transcript.clear()
        if is_familiarization(lesson):
            await _set_frozen(False)
        if hasattr(sim, "reset"):
            sim.reset()
            await _apply_lesson_clock()   # mock reset returns to daytime

    async def mute():
        state["muted"] = not state.get("muted", False)

    # ---------- checkpoints ("let me try that again") ----------
    checkpoints: dict[str, dict] = {}   # phase -> {"tel", "blob", "circuit"}
    PHASE_WORDS = {"takeoff": "TAKEOFF_ROLL", "runway": "PRE_TAKEOFF",
                   "start": "PRE_TAKEOFF", "beginning": "PRE_TAKEOFF",
                   "climb": "CLIMB_OUT", "crosswind": "CROSSWIND",
                   "downwind": "DOWNWIND", "base": "BASE", "final": "FINAL",
                   "landing": "FINAL", "approach": "FINAL",
                   "touchdown": "FLARE_TOUCHDOWN", "flare": "FLARE_TOUCHDOWN",
                   "rollout": "ROLLOUT",
                   "circuit": "PRE_TAKEOFF", "lap": "PRE_TAKEOFF"}

    async def retry(phase: str | None = None, reason_code: str = "TELEPORT"):
        """Teleport back to the start of a phase and let the trainee retry.
        reason_code selects the spoken line (TELEPORT for a request, FORCE_REPEAT
        when trainee/pilot mode is making them re-fly a leg)."""
        order = engine.detector.ORDER
        want = phase if phase in order else order[1]   # generic redo -> first step, not current phase
        # walk backwards until we find a phase we actually checkpointed
        i = order.index(want)
        ckpt = None
        while i >= 0:
            ckpt = checkpoints.get(order[i])
            if ckpt:
                want = order[i]
                break
            i -= 1
        if not ckpt:
            await coach.say(phrases.render("TELEPORT_FAIL", {}))
            return
        await coach.say(phrases.render(reason_code, {}))
        t = ckpt["tel"]
        ok = True
        if ckpt.get("blob") is not None and hasattr(sim, "restore_state"):
            await loop.run_in_executor(None, sim.restore_state, ckpt["blob"])
        elif hasattr(sim, "set_position"):
            gnd = bool(t.get("on_ground"))
            ok = await loop.run_in_executor(
                None, lambda: sim.set_position(t["lat"], t["lon"], t["alt_msl"], t["hdg"],
                                               t["ias"] if gnd else max(60.0, t["ias"]),
                                               on_ground=gnd))
        if ok:
            scorer.note_retry()
            engine.load_phase(want, ckpt["circuit"])
            state["track"].append([])          # new attempt = new track segment (all attempts kept)
        else:
            await coach.say(phrases.render("TELEPORT_FAIL", {}))

    # ---------- training modes (visitor / trainee / pilot / exam) ----------
    # Trainee + pilot gate progression with FORCE_REPEAT when a graded leg isn't
    # flown to tolerance (up to MAX_ATTEMPTS). Visitor + exam never force a
    # repeat. Coaching density / guidance style come from session["policy"].
    GATED = {"CROSSWIND", "DOWNWIND", "BASE", "FINAL"}
    MAX_ATTEMPTS = 2
    attempts: dict[str, int] = {}

    def phase_passed(phase: str, tel) -> bool:
        m = engine.monitors
        spd = m.tgt_speed.get(phase)
        if spd is not None and abs(tel.ias - spd) > 15:
            return False
        if phase == "DOWNWIND" and abs(tel.alt_msl - m.pattern_alt) > 150:
            return False
        if phase == "FINAL" and engine.geom is not None and abs(engine.geom.cross) > 120:
            return False
        return True

    def _sync_refs_to_state() -> None:
        state.update(refs.payload_for_state())

    def _apply_reference(ref_id: str) -> None:
        prof = refs.get(ref_id)
        session["reference"] = prof.id
        state["reference"] = {"id": prof.id, "name": prof.display_name}
        state["reference_track"] = prof.track
        engine.monitors.apply_reference(prof)

    async def _announce_reference_change(prof) -> None:
        if prof.id == "planned":
            await coach.say("Back to the planned circuit numbers from here.")
        else:
            await coach.say(
                f"Following {prof.display_name} from here, I'll coach to their speeds and heights.")

    async def select_lesson(new_id: str) -> dict:
        """Switch the flying lesson from the curriculum sidebar. Rebuilds every
        lesson-derived object (adapter geometry, engine, scorer, references)
        the same way startup does, and resets the sim clock to the new lesson's
        time of day. Refused while a run is in progress."""
        nonlocal lesson, sim, engine, scorer, refs, lesson_id
        if new_id == lesson_id:
            return {"ok": True, "lesson_id": lesson_id}
        meta = lesson_meta(catalog, new_id)
        path = HERE / "lessons" / f"{new_id}.yaml"
        if (not meta or not meta.get("available") or meta.get("kind") == "reading"
                or not path.is_file()):
            return {"ok": False, "error": "lesson not available"}
        if started.is_set():
            return {"ok": False, "error": "lesson in progress"}
        if is_familiarization(lesson):
            await _set_frozen(False)
        stop_speech()
        coach.pause()
        lesson = load_yaml(path)
        lesson_id = new_id
        if cfg["sim"]["adapter"] == "mock":      # mock flies the lesson's own geometry
            sim = make_adapter(cfg, lesson)
            sim.connect()
        lesson = lesson_for_sim(cfg, sim, lesson)
        engine = make_engine(cfg, lesson, aircraft)
        scorer = make_scorer(lesson, aircraft)
        refs = ReferenceRegistry(lesson, lesson_id)
        recorder.reset()
        attempts.clear()
        checkpoints.clear()
        scored["done"] = False
        _discard_card()
        transcript.clear()
        state.update(
            phase=engine.phase, circuit=1,
            total_circuits=int(lesson.get("total_circuits", 1)),
            track=[[]], scorecard=None, speaking=False, look_at=None,
            lesson_id=lesson_id,
            active_lesson=meta,
            reference_track=None,
            lesson=lesson_state(lesson),
            **refs.payload_for_state())
        state["scales"]["alt_max"] = alt_scale_max(lesson)
        _apply_reference("planned")
        await _apply_lesson_clock()
        coach.resume()
        log.info("lesson -> %s (%s)", lesson_id, lesson.get("name", lesson_id))
        return {"ok": True, "lesson_id": lesson_id}

    async def configure_session(mode: str, jargon: str, reference: str = "planned",
                                reference_recording: bool = False,
                                trainee_name: str = "",
                                tone: str = "warm"):
        stop_speech()
        coach.pause()
        if is_familiarization(lesson):
            await _set_frozen(False)
        mid = normalize_mode(mode)
        policy = policy_for(mid)
        session["mode"] = mid
        session["policy"] = policy
        session["jargon"] = "pilot" if jargon == "pilot" else "layman"
        # Hardcore tone only for Trainee/Pilot + Pilot jargon
        want_drill = (tone == "drill" and session["jargon"] == "pilot"
                      and mid in ("trainee", "pilot"))
        session["tone"] = "drill" if want_drill else "warm"
        session["reference_recording"] = bool(reference_recording)
        session["name"] = (trainee_name or "").strip()[:48]
        tc = int(lesson.get("total_circuits", 1))
        engine.detector.total_circuits = 1 if reference_recording else tc
        state["total_circuits"] = engine.detector.total_circuits
        phrases.configure(flap_style, session["jargon"],
                          guidance=policy.get("guidance", "direct"),
                          tone=session["tone"])
        attempts.clear()
        run_gen["n"] += 1
        scored["done"] = False
        _discard_card()
        engine.restart()
        scorer.reset()
        checkpoints.clear()
        state["track"] = [[]]
        transcript.clear()
        started.clear()
        state["lesson_running"] = False
        intro_shown.clear()
        coach.resume()
        _apply_reference(reference or "planned")
        log.info("session configured: mode=%s jargon=%s tone=%s guidance=%s coaching=%s "
                 "reference=%s trainee=%s",
                 session["mode"], session["jargon"], session["tone"],
                 policy.get("guidance"), policy.get("coaching"), session["reference"],
                 session["name"] or "-")

    async def set_reference(ref_id: str):
        prev = session.get("reference", "planned")
        rid = ref_id or "planned"
        _apply_reference(rid)
        if prev != rid and started.is_set() and engine.briefing_done:
            await _announce_reference_change(refs.get(rid))

    def _sync_instructors_state() -> None:
        state["instructors"] = instructors.list_admin() if session.get("instructor_unlocked") else []

    async def instructor_unlock(pin: str):
        inst = instructors.verify_pin(pin)
        if not inst:
            return False, None
        session["instructor_unlocked"] = True
        session["instructor"] = inst
        state["instructor_unlocked"] = True
        state["instructor"] = inst
        _sync_instructors_state()
        _sync_refs_to_state()
        return True, inst

    async def instructor_lock():
        session["instructor_unlocked"] = False
        session.pop("instructor", None)
        state["instructor_unlocked"] = False
        state["instructor"] = None
        state["instructors"] = []

    async def instructor_add(callsign: str, pin: str) -> bool:
        if not session.get("instructor_unlocked"):
            return False
        if not instructors.add(callsign, pin):
            return False
        _sync_instructors_state()
        return True

    async def instructor_update(inst_id: str, callsign: str = "", pin: str = "") -> bool:
        if not session.get("instructor_unlocked"):
            return False
        kw = {}
        if callsign.strip():
            kw["callsign"] = callsign
        if pin.strip():
            kw["pin"] = pin
        if not kw:
            return False
        if not instructors.update(inst_id, **kw):
            return False
        if session.get("instructor", {}).get("id") == inst_id:
            row = next((r for r in instructors.list_admin() if r["id"] == inst_id), None)
            if row:
                session["instructor"] = {"id": row["id"], "callsign": row["callsign"]}
                state["instructor"] = session["instructor"]
        _sync_instructors_state()
        return True

    async def instructor_remove(inst_id: str) -> bool:
        if not session.get("instructor_unlocked"):
            return False
        if session.get("instructor", {}).get("id") == inst_id:
            return False
        if not instructors.delete(inst_id):
            return False
        _sync_instructors_state()
        return True

    async def instructor_rename(run_id: str, display_name: str) -> bool:
        if not session.get("instructor_unlocked"):
            return False
        if not refs.rename(run_id, display_name):
            return False
        _sync_refs_to_state()
        if state["reference"].get("id") == run_id:
            state["reference"]["name"] = display_name.strip()[:64]
        return True

    async def instructor_delete(run_id: str) -> bool:
        if not session.get("instructor_unlocked"):
            return False
        if not refs.delete(run_id):
            return False
        _sync_refs_to_state()
        if state["reference"].get("id") == run_id:
            _apply_reference("planned")
        return True

    _apply_reference("planned")

    intro_shown = asyncio.Event()

    async def intro():
        """Intro video speaks the line; show matching text in chat (no TTS)."""
        if intro_shown.is_set():
            return
        if (session.get("policy") or {}).get("coaching") == "silent":
            intro_shown.set()
            return
        intro_shown.set()
        code = phrases.intro_code(session.get("mode", "visitor"))
        await coach.say(phrases.render(code, {}), speak=False)

    # ---------- agentic tool layer (Phase 1: conversational tool-router) ----------
    # The rule-based intents in chat() still run first and stay authoritative; this
    # only catches fuzzy requests they miss, by letting the local LLM pick ONE safe,
    # validated tool. Everything degrades to plain Q&A if the model is slow/offline.
    tools = ToolRegistry()

    async def _tool_set_time(hour: int, minute: int = 0):
        await set_sim_time(int(hour) % 24, int(minute) % 60)

    async def _tool_retry(phase: str | None = None):
        ph = PHASE_WORDS.get((phase or "").lower(), phase) if phase else None
        await retry(ph)

    async def _tool_guide(action: str = "open"):
        if action == "close":
            state["ui_cmd"] = {"action": "close_guide", "seq": time.time()}
            await coach.say("Closed. Eyes back outside, you're doing great.")
        else:
            state["ui_cmd"] = {"action": "open_guide", "seq": time.time()}
            await coach.say("Here's the controls guide. Just say 'close the guide' when you're done.")

    WEATHER_SAY = {"clear": "clear skies", "cloudy": "some cloud", "rain": "some rain",
                   "storm": "a storm", "fog": "some fog", "snow": "some snow"}

    async def _tool_weather(condition: str = ""):
        fn = getattr(sim, "set_weather", None)
        ok = await loop.run_in_executor(None, fn, condition) if fn else False
        what = WEATHER_SAY.get(condition, condition)
        if ok:
            # the sim can't confirm weather back, so don't promise it arrived
            await coach.say(f"Okay, I've asked the simulator for {what}. "
                            "Give it a moment to roll in.")
        else:
            await coach.say(f"Sorry, the simulator isn't letting me set {what} right now. "
                            "I can still change the time of day for you.")

    tools.register(Tool(
        "set_time_of_day",
        "Change the simulator time of day / lighting (e.g. make it darker, sunset, a clock time).",
        {"hour": {"type": "integer", "description": "local hour 0-23"},
         "minute": {"type": "integer", "description": "minute 0-59", "required": False}},
        _tool_set_time, examples=["can you make it a bit darker", "set the clock to sunset"]))
    tools.register(Tool(
        "retry_phase",
        "Teleport the aeroplane back so the student can re-fly part of the circuit.",
        {"phase": {"type": "string",
                   "enum": ["takeoff", "climb", "crosswind", "downwind", "base", "final", "landing"],
                   "description": "which leg to retry; omit for the current/last one",
                   "required": False}},
        _tool_retry, examples=["let me have another go at that landing", "can we redo the downwind"]))
    tools.register(Tool(
        "controls_guide",
        "Show or hide the on-screen controls guide diagram.",
        {"action": {"type": "string", "enum": ["open", "close"],
                    "description": "open to show, close to hide", "required": False}},
        _tool_guide, examples=["where are the flaps again", "hide that diagram"]))
    tools.register(Tool(
        "set_weather",
        "Set the simulator weather to a preset (config.yaml sim.weather; MAVIS says "
        "so if the simulator won't take it).",
        {"condition": {"type": "string",
                       "enum": ["clear", "cloudy", "rain", "storm", "fog", "snow"],
                       "description": "closest weather preset to what the student asked for"}},
        _tool_weather, examples=["can you add some clouds", "make it rainy"]))

    router = IntentRouter(tools, coach._ollama, lambda: coach.llm_ok)

    # ---------- lesson scoring + leaderboard (spec §14) ----------
    def _card_state(card, posted, debrief_pending: bool = False):
        ref_id = session.get("reference", "planned")
        prof = refs.get(ref_id)
        return {"seq": score_seq["n"], "name": card.name, "mode": card.mode,
                "scores": card.scores, "retries": card.retries, "total": card.total,
                "grade": card.grade, "capped": card.capped, "practice": card.practice,
                "incomplete": (card.incomplete if card.incomplete is not None
                               else card.scores.get("landing") is None),
                "scored_reference": ref_id,
                "scored_reference_name": prof.display_name if prof else "Planned circuit",
                "lesson_type": "familiarization" if is_familiarization(lesson) else "circuit",
                "posted": posted, "debrief": card.debrief, "debrief_pending": debrief_pending}

    def _limit_name_mentions(text: str, name: str, max_count: int = 1) -> str:
        """Keep spoken debrief natural. Use the trainee's name at most once."""
        n = (name or "").strip()
        if not n or max_count < 1:
            return text
        pat = re.compile(re.escape(n), re.I)
        seen = 0

        def repl(m):
            nonlocal seen
            seen += 1
            return m.group(0) if seen <= max_count else "you"

        out = pat.sub(repl, text)
        out = re.sub(r"\s+,", ",", out)
        out = re.sub(r",\s+you\b", ", you", out)
        out = re.sub(r"\byou,\s+you\b", "you", out)
        return re.sub(r"\s{2,}", " ", out).strip()

    def _sanitize_debrief(text: str, name: str) -> str:
        """Use the trainee's name at most once; strip invented names when none on file."""
        n = (name or "").strip()
        if n:
            return _limit_name_mentions(text, n)
        out = text
        out = re.sub(r",\s*[A-Z][a-z]+\.", ".", out)
        out = re.sub(r",\s*[A-Z][a-z]+\b", "", out)
        out = re.sub(r"^[A-Z][a-z]+,\s+", "", out)
        return re.sub(r"\s{2,}", " ", out).strip()

    async def _make_debrief(card) -> str:
        sc = {k: v for k, v in card.scores.items() if v is not None}
        if not sc:
            return "You gave it a go. Next time we'll aim for a full circuit."
        if is_familiarization(lesson):
            return await _make_fam_debrief(card, sc)
        best, worst = max(sc, key=sc.get), min(sc, key=sc.get)
        fallback = (f"Nice circuit. Your {best} was the strongest part. A little more "
                    f"attention to {worst} next time and it'll be even better.")
        if card.scores.get("landing") is None:
            fallback = (f"Good effort on the parts you flew. Your {best} stood out. "
                        "We didn't reach a landing, so that isn't scored yet.")
        if not coach.llm_ok:
            return fallback
        who = (session.get("name") or "").strip()
        who_line = (f"The trainee's name is {who}. Use that name at most once in the whole debrief."
                    if who else "Address the trainee only as 'you'. Do not use any proper name.")
        sysp = (phrases.PERSONA + "\nWrite a 2-3 sentence spoken debrief of the trainee's "
                "circuit from these 0-100 scores. Warm and specific: name one strength and "
                "one thing to work on. No lists; don't read out every number. " + who_line)
        try:
            out = await coach._ollama(
                sysp, f"scores={sc} retries={card.retries} total={card.total} grade={card.grade}", 10.0)
            out = (out or "").strip()
            if 10 < len(out) < 400:
                return _sanitize_debrief(out, who)
            return _sanitize_debrief(fallback, who)
        except Exception:
            return _sanitize_debrief(fallback, who)

    async def _make_fam_debrief(card, sc) -> str:
        names = {"exercises": "getting through every exercise", "altitude": "height holding",
                 "bank": "turns", "climb_speed": "climb speed", "smoothness": "smooth hands"}
        best, worst = max(sc, key=sc.get), min(sc, key=sc.get)
        fallback = (f"Lovely familiarisation flight. Your {names[best]} was the strongest part. "
                    f"A little more attention to {names[worst]} next time.")
        if card.incomplete:
            fallback = (f"Good effort on the parts you flew. Your {names[best]} stood out. "
                        "We didn't make it back to Paya Lebar this time.")
        if not coach.llm_ok:
            return fallback
        who = (session.get("name") or "").strip()
        who_line = (f"The trainee's name is {who}. Use that name at most once in the whole debrief."
                    if who else "Address the trainee only as 'you'. Do not use any proper name.")
        sysp = (phrases.PERSONA + "\nWrite a 2-3 sentence spoken debrief of the trainee's "
                "flight familiarisation exercise (an airborne start, straight and level, a turn "
                "each way, a climb and a descent, then finding Paya Lebar and flying to it; no "
                "take-off or landing) from these 0-100 scores. Warm and specific: name one "
                "strength and one thing to work on. No lists; don't read out every number. "
                + who_line)
        reached = "yes" if not card.incomplete else "no"
        try:
            out = await coach._ollama(
                sysp, f"scores={sc} reached_airfield={reached} total={card.total} grade={card.grade}", 10.0)
            out = (out or "").strip()
            if 10 < len(out) < 400:
                return _sanitize_debrief(out, who)
        except Exception:
            pass
        return _sanitize_debrief(fallback, who)

    def _debrief_spoken() -> bool:
        return any(t.get("phase") == "DEBRIEF" and t.get("who") == "instructor"
                   for t in transcript)

    def _coach_active() -> bool:
        return bool(coach.queue.qsize() or state.get("speaking") or coach.busy)

    async def _await_spoken_close():
        """Let the landing callout finish, then the debrief line, then a short beat."""
        pol = session.get("policy") or policy_for(session.get("mode", "visitor"))
        if not allow_coach_event(pol, "PHASE_ENTER_DEBRIEF", "info"):
            return
        deadline = time.time() + 45.0
        # Phase 1, drain any in-flight callout (incl. coach min-gap sleep)
        while time.time() < deadline:
            if _coach_active():
                await asyncio.sleep(0.1)
                continue
            break
        # Phase 2, spoken close (single owner; not submitted from the ticker)
        if not _debrief_spoken():
            code = "FAM_DEBRIEF" if is_familiarization(lesson) else "PHASE_ENTER_DEBRIEF"
            await coach.say(phrases.render(code, {
                "phase": "DEBRIEF", "circuit": engine.circuit}), phase="DEBRIEF")
        # Phase 3, wait until TTS actually finishes
        while time.time() < deadline and _coach_active():
            await asyncio.sleep(0.1)
        await asyncio.sleep(2.0)

    async def finish_lesson(practice: bool = False, spoken_close: bool = True):
        gen = run_gen["n"]
        if spoken_close:
            await _await_spoken_close()
        await _halt_lesson()
        pol = session.get("policy") or policy_for(session.get("mode", "visitor"))
        if pol.get("auto_practice_summary"):
            practice = True
        if hasattr(scorer, "skipped"):         # familiarization: timed-out steps cost points
            scorer.skipped = set(getattr(engine, "skipped", ()))
        card = scorer.finalize(
            session.get("name", ""), session["mode"], lesson_id, practice,
            score_caps=bool(pol.get("score_caps")))
        # §15 groundwork: hold this run's recording (for reference re-score later) + save last run
        pending["recording"] = recorder.to_dict(meta={
            "lesson": lesson_id, "aircraft": Path(cfg["aircraft_profile"]).stem,
            "mode": session["mode"], "reference": session.get("reference", "planned"),
            "practice": practice, "name": session.get("name", "")})
        try:
            recorder.save(HERE / "data" / "recordings" / "last_run.json")
        except Exception as e:
            log.debug("recording save failed: %s", e)
        pending["card"], pending["posted"] = card, False
        score_seq["n"] += 1
        if gen != run_gen["n"]:
            return
        state["scorecard"] = _card_state(card, posted=False, debrief_pending=True)
        card.debrief = await _make_debrief(card)
        if gen != run_gen["n"]:
            return
        state["scorecard"] = _card_state(card, posted=pending["posted"], debrief_pending=False)

    async def _halt_lesson():
        """Freeze coaching + phase progression once a run is over or abandoned."""
        stop_speech()
        coach.pause()
        engine.briefing_done = False

    quit_task: dict = {"t": None}   # keeps a reference so the task isn't GC'd

    async def quit_lesson():
        # trainee pressed Back before finishing -> practice summary, NOT posted
        if is_familiarization(lesson):
            await _set_frozen(False)        # hand the aircraft back on the way out
        if scored["done"]:
            return
        scored["done"] = True
        stop_speech()
        coach.pause()
        started.clear()
        state["lesson_running"] = False
        intro_shown.clear()
        transcript.clear()
        # Scoring + the LLM debrief can take a while (or fail); run them in
        # the background so POST /demo/quit returns at once -- the scorecard
        # arrives over the WS state like any other update. A failure here is
        # logged instead of turning the quit itself into an HTTP 500.
        async def _finish():
            try:
                await finish_lesson(practice=True, spoken_close=False)
            except Exception:
                log.exception("finishing the lesson after quit failed")
        quit_task["t"] = asyncio.create_task(_finish())

    def _post_card(name: str = "", given: str = "", family: str = "") -> None:
        """Post the held scorecard once; an empty name posts as Anonymous."""
        card = pending.get("card")
        if not card or card.practice or pending["posted"]:
            return
        card.name = (name or "").strip()[:24] or "Anonymous"
        card.given = (given or "").strip()[:32]
        card.family = (family or "").strip()[:32]
        board.add(card.to_entry())
        pending["posted"] = True
        state["leaderboard"] = board.snapshot()
        state["scorecard"] = _card_state(card, posted=True)

    def _discard_card() -> None:
        """Every completed run reaches the board: a card the trainee never
        answered for is posted as Anonymous before it is cleared."""
        _post_card()
        state["scorecard"] = None
        pending["card"] = None
        pending["posted"] = False

    async def score_submit(name: str, given: str = "", family: str = ""):
        _post_card(name, given, family)

    actions = {"start": start, "night": night_sequence,
               "skip": _wrap(lambda: engine.skip_phase()), "restart": restart,
               "chat": chat, "mute": mute, "intro": intro, "retry": retry,
               "configure_session": configure_session,
               "select_lesson": select_lesson,
               "quit": quit_lesson, "score_submit": score_submit,
               "set_reference": set_reference,
               "reading_say": reading_say,
               "instructor_unlock": instructor_unlock,
               "instructor_lock": instructor_lock,
               "instructor_rename": instructor_rename,
               "instructor_delete": instructor_delete,
               "instructor_add": instructor_add,
               "instructor_update": instructor_update,
               "instructor_remove": instructor_remove}

    # ---------- main tick loop ----------
    def _jump(a, b):   # crude position delta in degrees (~0.01 deg = 1.1 km)
        return abs(a.lat - b.lat) + abs(a.lon - b.lon)

    async def ticker():
        hz = cfg["sim"].get("tick_hz", 10)
        last_track = 0.0
        night_task = None
        last_good = prev_raw = None
        prev_on_ground = None
        last_tick_error = 0.0
        while True:
            t0 = time.time()
            # One bad sim read must never take MAVIS down: an exception here
            # used to end the whole process (asyncio.gather), which the web
            # proxy then reports as 502. Log it (throttled) and keep ticking.
            try:
                tel = await loop.run_in_executor(None, sim.get_telemetry)
            except Exception:
                if t0 - last_tick_error > 10:
                    log.exception("reading sim telemetry failed -- will keep trying")
                    last_tick_error = t0
                await asyncio.sleep(0.5)
                continue
            ctrl = hid.poll() if hid and hid.ok else ControlSample()
            # glitch guard: SimConnect sometimes emits a one-sample position
            # spike. Ignore isolated spikes for lesson logic + track; accept
            # a persistent move (2 samples in the same place = real reposition)
            spike = (last_good is not None and prev_raw is not None
                     and _jump(tel, last_good) > 0.02 and _jump(tel, prev_raw) > 0.01)
            prev_raw = tel
            if not spike and not scored["done"]:
                last_good = tel
                prev_phase = engine.phase
                events = engine.tick(tel, ctrl)
                # trainee/pilot: if a graded leg just ended below tolerance, send
                # them back to re-fly it (bounded) instead of progressing.
                pol = session.get("policy") or {}
                force_redo = (pol.get("force_repeat")
                              and not session.get("reference_recording")
                              and engine.phase != prev_phase
                              and prev_phase in GATED
                              and not phase_passed(prev_phase, tel)
                              and attempts.get(prev_phase, 0) < MAX_ATTEMPTS)
                for ev in events:
                    if force_redo and ev.code.startswith("PHASE_ENTER_"):
                        continue   # don't announce the new leg; we're reverting
                    if ev.code == "PHASE_ENTER_DEBRIEF":
                        continue   # finish_lesson/_await_spoken_close owns this line
                    if not allow_coach_event(pol, ev.code, ev.severity):
                        continue
                    if ev.code == "LESSON_NOTE" and is_familiarization(lesson):
                        state["look_at"] = {"items": ev.data.get("look") or [],
                                            "phase": ev.data.get("phase"), "seq": time.time()}
                        if _fam_freezes() and ev.data.get("freeze", True):
                            engine.hold()       # judge nothing until "you have control"
                            asyncio.create_task(_fam_instruct(ev))
                            continue
                    coach.submit(ev)
                    if hasattr(sim, "comply"):   # mock student obeys callouts
                        sim.comply(ev.code, ev.data)
                    if ev.code.startswith("PHASE_ENTER_"):   # checkpoint each phase
                        checkpoints[ev.data["phase"]] = {
                            "tel": tel.as_dict(), "circuit": engine.circuit,
                            "blob": sim.save_state() if hasattr(sim, "save_state") else None}
                if force_redo:
                    attempts[prev_phase] = attempts.get(prev_phase, 0) + 1
                    asyncio.create_task(retry(prev_phase, "FORCE_REPEAT"))
                # ---- scoring: sample the tick, capture touchdowns, finalize at debrief ----
                if engine.briefing_done:
                    geom_d = ({"along": engine.geom.along, "cross": engine.geom.cross}
                              if engine.geom else None)
                    if not getattr(engine, "held", False):   # frozen for an explanation: not flying
                        scorer.sample(tel.as_dict(), geom_d, ctrl.as_dict(), engine.phase)
                    recorder.sample(tel.as_dict(), ctrl.as_dict(), geom_d, engine.phase)
                    if prev_on_ground is False and tel.on_ground \
                            and engine.phase in ("FLARE_TOUCHDOWN", "FINAL", "ROLLOUT"):
                        scorer.on_landing(tel.as_dict(), geom_d)
                    prev_on_ground = tel.on_ground
                    if engine.phase == "DEBRIEF" and not scored["done"]:
                        scored["done"] = True
                        if is_familiarization(lesson):
                            # practice over: freeze, score, then back to the runway
                            asyncio.create_task(_fam_finish())
                        else:
                            asyncio.create_task(finish_lesson(False))
            elif not spike:
                last_good = tel
            if not scored["done"] and lesson.get("night_event") \
                    and engine.wants_night_switch() and night_task is None:
                night_task = asyncio.create_task(night_sequence())
            state.update(
                tel=tel.as_dict(), ctrl=ctrl.as_dict(), phase=engine.phase,
                circuit=engine.circuit, total_circuits=engine.detector.total_circuits,
                targets=engine.targets(),
                night=not (6.0 < tel.local_time_h < 18.5),
                transcript=transcript[-60:], llm_ok=coach.llm_ok,
                fam_todo=engine.todo() if started.is_set() and hasattr(engine, "todo") else None,
                geom={"along": engine.geom.along, "cross": engine.geom.cross}
                     if engine.geom else None)
            # Ground track keeps the whole flight (taxi, take-off roll and
            # rollout included), like IRIS's post-flight ground track. Long
            # flights are thinned rather than trimmed: past TRACK_MAX points
            # the older part is halved, so the full path stays on the map
            # (just coarser at the start) and the payload stays bounded.
            if not scored["done"] and t0 - last_track > 1.0 and not spike \
                    and abs(tel.lat) > 0.01:
                seg = state["track"][-1]
                d = (abs(tel.lat - seg[-1][0]) + abs(tel.lon - seg[-1][1])) if seg else 1.0
                if d >= 0.02:     # real reposition (> ~2 km/sample): new segment, no false line across the jump
                    if seg:
                        state["track"].append([])
                        seg = state["track"][-1]
                    seg.append([tel.lat, tel.lon])
                    last_track = t0
                elif d > 2e-5:    # skip samples while parked (< ~2 m moved)
                    seg.append([tel.lat, tel.lon])
                    if len(seg) > TRACK_MAX:
                        seg[:] = seg[:-TRACK_KEEP_FULL:2] + seg[-TRACK_KEEP_FULL:]
                    last_track = t0
            await asyncio.sleep(max(0.01, 1.0 / hz - (time.time() - t0)))

    media_path = Path(cfg["web"]["media_dir"])
    if not media_path.is_absolute():
        media_path = HERE / media_path
    voice_cfg = dict(cfg.get("voice") or {})
    voices_dir = Path(voice_cfg.get("voices_dir", "data/voices"))
    if not voices_dir.is_absolute():
        voices_dir = HERE / voices_dir
    voice_store = VoiceStore(voices_dir, threshold=float(voice_cfg.get("speaker_threshold", 0.30)))
    stt = WhisperSTT(
        model_size=str(voice_cfg.get("stt_model", "small")),
        device=str(voice_cfg.get("stt_device", "auto")),
        compute_type=str(voice_cfg.get("stt_compute", "auto")),
        initial_prompt=voice_cfg.get("stt_initial_prompt"),
    )
    app = build_app(state, actions, str(media_path), tts_dir=str(tts_dir),
                    voice_store=voice_store, stt=stt, voice_cfg=voice_cfg)
    server = EmbeddedServer(uvicorn.Config(
        app, host=cfg["web"]["host"], port=cfg["web"]["port"], log_level="warning"))
    log.info("UI: http://<sim-pc-ip>:%s  (tablet on same LAN)", cfg["web"]["port"])

    async def warm_voice_models():
        """Whisper + ECAPA after TTS, SpeechBrain import breaks Kokoro if it runs first."""
        def _load():
            try:
                stt.ensure()
            except Exception as e:
                log.info("stt warmup: %s", e)
            try:
                voice_store.engine.ensure()
            except Exception as e:
                log.info("speaker warmup: %s", e)
        await loop.run_in_executor(None, _load)

    async def init_voice_stack():
        """Kokoro TTS must initialize before SpeechBrain (ECAPA) or Kokoro fails to import."""
        await init_tts()
        await warm_voice_models()

    tick_task = coach_task = serve_task = voice_stack_task = None
    stop_count = {"n": 0}

    def _request_stop():
        """First Ctrl+C: cooperative shutdown. Second: immediate exit (Windows/CUDA safe)."""
        if stop_count["n"]:
            log.warning("Force exit.")
            os._exit(130)
        stop_count["n"] += 1
        log.info("Shutting down…")
        server.should_exit = True
        server.force_exit = True
        for t in (tick_task, coach_task, serve_task, voice_stack_task):
            if t is not None and not t.done():
                t.cancel()

    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, _request_stop)
    except (NotImplementedError, RuntimeError):
        signal.signal(signal.SIGINT, lambda *_: loop.call_soon_threadsafe(_request_stop))

    try:
        serve_task = asyncio.create_task(server.serve())   # UI reachable first
        voice_stack_task = asyncio.create_task(init_voice_stack())
        tick_task = asyncio.create_task(ticker())
        coach_task = asyncio.create_task(coach.run())
        await asyncio.gather(tick_task, coach_task, serve_task, voice_stack_task)
    except asyncio.CancelledError:
        pass
    finally:
        server.should_exit = True
        server.force_exit = True
        try:
            sim.close()
        except Exception:
            pass
        if hid and hasattr(hid, "close"):
            try:
                hid.close()
            except Exception:
                pass
        for t in (tick_task, coach_task, serve_task, voice_stack_task):
            if t is not None and not t.done():
                t.cancel()
        pending = [t for t in (tick_task, coach_task, serve_task, voice_stack_task)
                   if t is not None]
        if pending:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True), timeout=1.5)
            except asyncio.TimeoutError:
                log.warning("Shutdown timed out.")
        tts_pool.shutdown(wait=False, cancel_futures=True)
        log.info("Stopped.")


def _wrap(fn):
    async def inner(): fn()
    return inner


def _wav_seconds(path) -> float:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / w.getframerate()
    except Exception:
        return 3.0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--sim", choices=["mock", "msfs", "replay"],
                    help="sim adapter (overrides config sim.adapter)")
    ap.add_argument("--input", choices=["none", "udp", "local_hid"],
                    help="control input source (overrides config input.mode)")
    args = ap.parse_args()
    cfg = load_yaml(args.config)
    if args.sim:
        cfg["sim"]["adapter"] = args.sim
    if args.input:
        cfg.setdefault("input", {})["mode"] = args.input
    exit_code = 0
    try:
        asyncio.run(amain(cfg))
    except KeyboardInterrupt:
        log.info("Interrupted.")
        exit_code = 130
    # Bypass hung PyTorch/CUDA/MKL atexit hooks on Windows after Ctrl+C.
    os._exit(exit_code)
