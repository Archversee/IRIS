"""AI Flight Instructor - entry point.

    python main.py                          # mock sim (config default)
    python main.py --sim msfs               # real MSFS via SimConnect
    python main.py --sim msfs --input udp   # MSFS + joystick PTT (hid_forwarder on sim PC)
"""
from __future__ import annotations
import argparse
import asyncio
import logging
import os
import signal
import time
from pathlib import Path

APP_VERSION = "0.2.0"
import uvicorn

from adapters.input_hid import HIDInput, ControlSample
from coach.llm_coach import Coach
from coach import phrases
from core.leaderboard import Leaderboard
from core.instructors import InstructorRegistry
from core.recorder import FlightRecorder
from core.event_log import EventLog
from core.free_flight import FreeFlightTracker
from core.lesson_catalog import flatten_lessons, lesson_meta, load_catalog
from core.reference_profiles import ReferenceRegistry
from core.modes import policy_for
from voice.speaker import VoiceStore
from voice.stt import WhisperSTT
from web.server import build_app
from runtime.bootstrap import (READING_LESSONS, alt_scale_max, event_log_setup, lesson_for_sim,
                               lesson_state, load_yaml, make_adapter, make_engine, make_scorer,
                               replay_video_for_web)
from runtime.context import Runtime
from runtime.chat_router import build_chat
from runtime.checkpoints import build_checkpoints
from runtime.instructor_admin import build_instructor_admin
from runtime.sop_live import build_sop_live
from runtime.scoring_flow import build_scoring_flow
from runtime.speech import build_speech
from runtime.flight_flow import build_flight_flow
from runtime.session import build_session
from runtime import tick_steps
from runtime.tick_steps import _jump

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("main")
HERE = Path(__file__).parent


class EmbeddedServer(uvicorn.Server):
    """Run uvicorn inside asyncio.run(); honour should_exit for clean Ctrl+C."""

    async def serve(self, sockets=None):
        try:
            await self._serve(sockets)
        except asyncio.CancelledError:
            self.should_exit = True
            raise


async def amain(cfg):
    rt = Runtime()       # shared runtime context (runtime/context.py): modules read it at call time
    rt.cfg = cfg
    rt.lesson = load_yaml(cfg["lesson"])
    aircraft = load_yaml(cfg["aircraft_profile"])
    rt.aircraft = aircraft
    rt.sim = make_adapter(cfg, rt.lesson)
    # msfs's connect() is the real SimConnect handshake, which needs MSFS
    # already running with a flight loaded -- deferred to start() (below,
    # with retries) so the web server/menu/reading-lessons all come up
    # regardless of MSFS's state. mock/replay have no such dependency, so
    # they still connect immediately as before.
    if cfg["sim"]["adapter"] != "msfs":
        rt.sim.connect()
    rt.lesson = lesson_for_sim(cfg, rt.sim, rt.lesson)

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
    rt.flap_style = flap_style
    phrases.configure(flap_style, "layman")
    phrases.set_lesson_lines(rt.lesson.get("say"))    # lesson's own wording for some callouts
    # main-menu selection: visitor/trainee/pilot/exam policies; jargon still
    # independent (Settings). Defaults are safe before the menu sends anything.
    _default_policy = policy_for("visitor")
    session = {"mode": "visitor", "jargon": "layman", "tone": "warm", "name": "",
               "reference": "planned", "instructor_unlocked": False, "policy": _default_policy,
               "session_id": None}
    rt.session = session
    instructor_pin = str(cfg.get("instructor", {}).get("pin", "1234"))
    rt.engine = make_engine(cfg, rt.lesson, aircraft)
    rt.scorer = make_scorer(rt.lesson, aircraft)
    recorder = FlightRecorder()          # §15: capture each run (groundwork for reference re-score)
    rt.recorder = recorder
    def _sim_now() -> float:
        """Telemetry clock (== flight_data.ts in IRIS; differs from wall time in replay)."""
        return (state.get("tel") or {}).get("t") or time.time()
    rt._sim_now = _sim_now

    event_log = EventLog()               # start/end time of each to-do step / phase, for IRIS to grade
    rt.event_log = event_log
    event_log.configure(*event_log_setup(rt.lesson, rt.engine))

    # Free Flight has no set track, so its phases come from the aircraft's own state
    # (core/free_flight.py), not from the circuit detector's runway geometry
    _vspd = aircraft.get("v_speeds") or {}
    free_tracker = FreeFlightTracker(vs1=_vspd.get("vs1", 48), vs0=_vspd.get("vs0", 40))
    rt.free_tracker = free_tracker

    def _flight_phase() -> str:
        """The phase to show and log: the circuit's, or the free-flight tracker's."""
        return free_tracker.phase if session.get("free_flight") else rt.engine.phase
    rt._flight_phase = _flight_phase

    def _publish_events() -> None:
        """Push the event log + flight list into the state IRIS's bridge reads."""
        state["flight_events"] = event_log.to_list()
        state["flights"] = event_log.flights()
    rt._publish_events = _publish_events

    board = Leaderboard(HERE / "data" / "leaderboard.json")
    rt.board = board
    board.seed_demo_if_empty()
    rt.lesson_id = Path(cfg["lesson"]).stem
    catalog = load_catalog()
    rt.catalog = catalog
    rt.refs = ReferenceRegistry(rt.lesson, rt.lesson_id)
    instructors = InstructorRegistry(instructor_pin)
    rt.instructors = instructors
    score_seq = {"n": 0}
    rt.score_seq = score_seq
    run_gen = {"n": 0}          # bumped on restart, stale finish_lesson tasks ignore
    rt.run_gen = run_gen
    # up-front demonstration of an airborne lesson (core/demo.py): Skip ends
    # it; `shown` = steps the trainee watched (they then get a short cue, no hold)
    demo_ctl = {"skip": asyncio.Event(), "shown": set()}
    rt.demo_ctl = demo_ctl
    pending: dict = {"card": None, "posted": False}
    rt.pending = pending
    scored = {"done": False}
    rt.scored = scored
    rt.tts = None
    tts_ready = asyncio.Event()
    rt.tts_ready = tts_ready
    speak_gen = {"n": 0}
    rt.speak_gen = speak_gen
    transcript: list[dict] = []
    rt.transcript = transcript
    # track is a list of SEGMENTS; a retry/teleport starts a new segment so
    # old attempts render dim and there's no false line across the jump
    state: dict = {"phase": rt.engine.phase, "circuit": 1, "total_circuits": int(rt.lesson.get("total_circuits", 1)),
                   "session_id": None,
                   # True from Start until quit/restart -- lets the web UI
                   # reattach to a running session after navigating away.
                   "lesson_running": False,
                   "flight_events": [],
                   "flights": [],
                   "transcript": [],
                   "speaking": False, "night": False, "track": [[]], "demo_track": [],
                   "demo": None,       # {phase, title} while MAVIS is flying a demo
                   "theme": cfg["web"].get("theme", {}),
                   "monitor": cfg["web"].get("monitor_sources", []),
                   # replay: the trainee-view clip is the recording of the replayed flight,
                   # started by the client at the same instant playback begins
                   "demo_video": replay_video_for_web(cfg),
                   "scales": {"speed_max": aircraft["v_speeds"].get("vne", 163),
                              "alt_max": alt_scale_max(rt.lesson)},
                   "flaps_style": flap_style, "session": session,
                   "scorecard": None, "leaderboard": board.snapshot(),
                   "lesson_id": rt.lesson_id,
                   "lesson_catalog": catalog,
                   "app_version": APP_VERSION,
                   "lesson_list": flatten_lessons(catalog),
                   "reading_lessons": {lid: mod.as_state() for lid, mod in READING_LESSONS.items()},
                   "active_lesson": lesson_meta(catalog, rt.lesson_id) or {"id": rt.lesson_id, "title": rt.lesson.get("name", rt.lesson_id), "available": True},
                   "instructor_unlocked": False,
                   "instructor": None,
                   "instructors": [],
                   **rt.refs.payload_for_state(),
                   "reference": {"id": "planned", "name": "Planned circuit"},
                   "reference_track": None,
                   "lesson": lesson_state(rt.lesson)}
    rt.state = state
    loop = asyncio.get_event_loop()
    rt.loop = loop

    # ---------- SOP rules + live rule checks (runtime/sop_live.py) ----------
    build_sop_live(rt)

    # ---------- speech: TTS channel + reading narration (runtime/speech.py) ----------
    build_speech(rt)

    coach = Coach(cfg["coach"], aircraft, rt.speak, transcript)
    rt.coach = coach
    coach.live_tel = lambda: state.get("tel")   # for stale-callout suppression
    coach.interrupt = rt.stop_speech            # turn / safety calls cut off lesser lines
    await coach.check_llm()

    started = asyncio.Event()     # set by start(), cleared on restart / reconfigure / quit
    rt.started = started

    # ---------- flight sequence: briefing, freeze, demo, night (runtime/flight_flow.py) ----------
    build_flight_flow(rt)

    # ---------- session lifecycle: configure / start / restart / quit (runtime/session.py) ----------
    build_session(rt)

    # ---------- checkpoints ("let me try that again") + forced repeats (runtime/checkpoints.py) ----------
    checkpoints: dict[str, dict] = {}   # phase -> {"tel", "blob", "circuit"}
    rt.checkpoints = checkpoints
    attempts: dict[str, int] = {}       # graded leg -> forced repeats so far (up to MAX_ATTEMPTS)
    rt.attempts = attempts
    retry, phase_passed = build_checkpoints(rt, state, coach, loop, event_log, _sim_now, checkpoints)
    rt.phase_passed = phase_passed

    # ---------- instructor admin (runtime/instructor_admin.py) ----------
    build_instructor_admin(rt)

    rt._apply_reference("planned")

    # ---------- chat: rule-based intents + agentic tool layer (runtime/chat_router.py) ----------
    chat = build_chat(rt, state, coach, loop, retry)

    # ---------- lesson scoring + debrief + leaderboard (runtime/scoring_flow.py) ----------
    build_scoring_flow(rt)

    # ---------- web actions (web/server.py calls these by name) ----------
    actions = {"start": rt.start, "night": rt.night_sequence,
               "skip": _wrap(lambda: rt.engine.skip_phase()), "restart": rt.restart,
               "skip_demo": rt.skip_demo,
               "relaunch": rt.relaunch, "relaunch_new": rt.relaunch_new,
               "chat": chat, "mute": rt.mute, "intro": rt.intro, "retry": retry,
               "configure_session": rt.configure_session,
               "select_lesson": rt.select_lesson,
               "quit": rt.quit_lesson, "score_submit": rt.score_submit,
               "set_reference": rt.set_reference,
               "reading_say": rt.reading_say,
               "instructor_unlock": rt.instructor_unlock,
               "instructor_lock": rt.instructor_lock,
               "instructor_rename": rt.instructor_rename,
               "instructor_delete": rt.instructor_delete,
               "instructor_add": rt.instructor_add,
               "instructor_update": rt.instructor_update,
               "instructor_remove": rt.instructor_remove}

    # ---------- main tick loop ----------
    async def ticker():
        hz = cfg["sim"].get("tick_hz", 10)
        last_track = 0.0
        night_task = None
        last_good = prev_raw = None
        prev_on_ground = None
        last_tick_error = 0.0
        while True:
            t0 = time.time()
            # a lesson is being flown (the phase alone stays mid-circuit after Quit)
            state["flying"] = started.is_set() and not scored["done"]
            # One bad sim read must never take MAVIS down: an exception here
            # used to end the whole process (asyncio.gather), which the web
            # proxy then reports as 502. Log it (throttled) and keep ticking.
            try:
                tel = await loop.run_in_executor(None, rt.sim.get_telemetry)
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
                prev_phase = rt.engine.phase
                events = rt.engine.tick(tel, ctrl)      # phase detection + monitors, in the loop
                pol = session.get("policy") or {}
                force_redo = tick_steps.force_redo_due(rt, pol, prev_phase, tel)
                tick_steps.dispatch_events(rt, pol, events, tel, force_redo)
                if force_redo:
                    attempts[prev_phase] = attempts.get(prev_phase, 0) + 1
                    asyncio.create_task(retry(prev_phase, "FORCE_REPEAT"))
                prev_on_ground = tick_steps.score_tick(rt, tel, ctrl, prev_on_ground)
            elif not spike:
                last_good = tel
            if not scored["done"] and rt.lesson.get("night_event") \
                    and rt.engine.wants_night_switch() and night_task is None:
                night_task = asyncio.create_task(rt.night_sequence())
            tick_steps.publish_state(rt, tel, ctrl)
            last_track = tick_steps.append_track(rt, tel, t0, spike, last_track)
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
    app = build_app(state, actions, str(media_path), tts_dir=str(rt.tts_dir),
                    voice_store=voice_store, stt=stt, voice_cfg=voice_cfg, app_cfg=cfg)
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
        await rt.init_tts()
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
            rt.sim.close()
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
        rt.tts_pool.shutdown(wait=False, cancel_futures=True)
        log.info("Stopped.")


def _wrap(fn):
    async def inner(): fn()
    return inner


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
