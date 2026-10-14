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

APP_VERSION = "0.2.0"
import uvicorn

from adapters.input_hid import HIDInput, ControlSample
from adapters.sim_base import Telemetry
from coach.llm_coach import Coach, clip_spoken
from coach import phrases
from core.events import CoachEvent
from core.leaderboard import Leaderboard
from core.instructors import InstructorRegistry
from core.recorder import FlightRecorder
from core.event_log import EventLog
from core.free_flight import LABELS as FREE_FLIGHT_LABELS, FreeFlightTracker
from core.lesson_catalog import flatten_lessons, lesson_meta, load_catalog
from core.familiarization import (PART_CUES, STEP_TITLES, is_familiarization, runway_start,
                                  start_position)
from core.demo import DEMO_LONG_STEPS, DESCRIBE_ONLY, NO_DEMO, DemoPilot
from lessons import lesson00_preflight, lesson00_cockpit

# Reading-kind lessons (catalog.json "kind": "reading"): narrated checklists with
# no flight telemetry, keyed by the same lesson id the catalog and sidebar use.
READING_LESSONS = {
    "lesson00_preflight": lesson00_preflight,
    "lesson00_cockpit": lesson00_cockpit,
}
from core.reference_profiles import ReferenceRegistry
from core.modes import policy_for, normalize_mode, allow_coach_event
from voice.tts import TTS
from voice.speaker import VoiceStore
from voice.stt import WhisperSTT
from web.server import build_app
from runtime.bootstrap import (alt_scale_max, event_log_setup, lesson_for_sim, lesson_state,
                               load_yaml, make_adapter, make_engine, make_scorer,
                               replay_video_for_web)
from runtime.context import Runtime
from runtime.chat_router import build_chat
from runtime.checkpoints import GATED, MAX_ATTEMPTS, build_checkpoints

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


async def amain(cfg):
    rt = Runtime()       # lesson / sim / engine / ... swapped mid-run: always read via rt
    rt.lesson = load_yaml(cfg["lesson"])
    aircraft = load_yaml(cfg["aircraft_profile"])
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
    phrases.configure(flap_style, "layman")
    phrases.set_lesson_lines(rt.lesson.get("say"))    # lesson's own wording for some callouts
    # main-menu selection: visitor/trainee/pilot/exam policies; jargon still
    # independent (Settings). Defaults are safe before the menu sends anything.
    _default_policy = policy_for("visitor")
    session = {"mode": "visitor", "jargon": "layman", "tone": "warm", "name": "",
               "reference": "planned", "instructor_unlocked": False, "policy": _default_policy,
               "session_id": None}
    instructor_pin = str(cfg.get("instructor", {}).get("pin", "1234"))
    rt.engine = make_engine(cfg, rt.lesson, aircraft)
    rt.scorer = make_scorer(rt.lesson, aircraft)
    recorder = FlightRecorder()          # §15: capture each run (groundwork for reference re-score)
    def _sim_now() -> float:
        """Telemetry clock (== flight_data.ts in IRIS; differs from wall time in replay)."""
        return (state.get("tel") or {}).get("t") or time.time()

    event_log = EventLog()               # start/end time of each to-do step / phase, for IRIS to grade
    event_log.configure(*event_log_setup(rt.lesson, rt.engine))

    # Free Flight has no set track, so its phases come from the aircraft's own state
    # (core/free_flight.py), not from the circuit detector's runway geometry
    _vspd = aircraft.get("v_speeds") or {}
    free_tracker = FreeFlightTracker(vs1=_vspd.get("vs1", 48), vs0=_vspd.get("vs0", 40))

    def _flight_phase() -> str:
        """The phase to show and log: the circuit's, or the free-flight tracker's."""
        return free_tracker.phase if session.get("free_flight") else rt.engine.phase

    def _publish_events() -> None:
        """Push the event log + flight list into the state IRIS's bridge reads."""
        state["flight_events"] = event_log.to_list()
        state["flights"] = event_log.flights()

    board = Leaderboard(HERE / "data" / "leaderboard.json")
    board.seed_demo_if_empty()
    rt.lesson_id = Path(cfg["lesson"]).stem
    catalog = load_catalog()
    rt.refs = ReferenceRegistry(rt.lesson, rt.lesson_id)
    instructors = InstructorRegistry(instructor_pin)
    score_seq = {"n": 0}
    run_gen = {"n": 0}          # bumped on restart, stale finish_lesson tasks ignore
    # up-front demonstration of an airborne lesson (core/demo.py): Skip ends
    # it; `shown` = steps the trainee watched (they then get a short cue, no hold)
    demo_ctl = {"skip": asyncio.Event(), "shown": set()}
    pending: dict = {"card": None, "posted": False}
    scored = {"done": False}
    rt.tts = None
    tts_ready = asyncio.Event()
    speak_gen = {"n": 0}
    transcript: list[dict] = []
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
                   "speaking": False, "night": False, "track": [[]],
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
    loop = asyncio.get_event_loop()

    # SOP for the aircraft actually being flown (sop/runtime.py): resolved at
    # every lesson start from the sim's aircraft; rules kept in sop_rt.active.
    try:
        from sop.runtime import SopRuntime
        sop_rt = SopRuntime(cfg, cfg["aircraft_profile"])
    except ImportError as e:
        log.info("SOP rules disabled (%s)", e)
        sop_rt = None
    state["sop"] = None

    async def _load_sop():
        if sop_rt is None:
            state["sop_live"] = _rules_off("SOP rules are disabled in MAVIS (see the startup log).")
            return
        ident = None
        if hasattr(rt.sim, "aircraft_ident"):
            try:
                ident = await loop.run_in_executor(None, rt.sim.aircraft_ident)
            except Exception as e:
                log.info("aircraft ident failed: %s", e)
        state["sop"] = await sop_rt.load(ident)
        log.info("SOP for this flight: %s", state["sop"])
        try:
            await _start_rule_checks()
        except Exception as e:          # runs as a background task: never fail silently
            log.exception("starting live rule checks failed")
            live_rules["ev"] = None
            state["sop_live"] = _rules_off(f"Rule checks failed to start: {e}")

    # SOP / handbook rules checked live (sop/evaluate.py) -- the same engine
    # IRIS's Review re-runs post-flight, so the two never disagree. Cautions
    # and advisories are shown; a broken WARNING rule is also spoken
    # (not in exam mode, at most once per rule every RULE_REPEAT_S).
    RULE_REPEAT_S = 30.0
    live_rules: dict = {"ev": None, "ctx": None, "spoken": {}}
    state["sop_live"] = None
    try:
        from sop.evaluate import RuleEvaluator, sample_from_telemetry, summarize
        from sop.results import ResultStore, aircraft_note, snapshot
        rule_results = ResultStore(sop_rt.store, cfg) if sop_rt is not None else None
    except ImportError as e:
        log.info("live rule checks disabled (%s)", e)
        rule_results = None

    def _rules_off(reason: str) -> dict:
        """state["sop_live"] when nothing is being checked: the live views say why."""
        log.warning("live rule checks off: %s", reason)
        return {"status": "off", "reason": reason, "n_rules": 0, "active": [], "recent": [],
                "n_findings": 0, "summary": None}

    def _why_no_rules() -> str:
        sop = state.get("sop") or {}
        st, plane = sop.get("status"), sop.get("aircraft_name") or sop.get("aircraft_id") or "this aircraft"
        if st == "unavailable":
            return f"Rules database unavailable: {sop.get('error') or 'Postgres not reachable'}"
        if st == "no_profile":
            return (f"Aircraft not recognised ({sop.get('detected') or 'nothing reported'}) and no training "
                    "handbook rule set is active.")
        lt = sop.get("lesson_type") or "normal"
        if st == "none":
            return (f"No active rule set for {plane} and no active training handbook. "
                    "Activate one on the SOP Rules page.")
        return (f"The active rule sets have no enabled rules for {lt} lessons "
                f"({sop.get('n_unresolved') or 0} left out for a missing aircraft speed).")

    async def _start_rule_checks():
        live_rules.update(ev=None, ctx=None, spoken={})
        state["sop_live"] = None
        if rule_results is None:
            state["sop_live"] = _rules_off("Rule checks are disabled in MAVIS (needs asyncpg; see the startup log).")
            return
        act = sop_rt.active if sop_rt is not None else None
        if not act or not act.get("rules"):
            state["sop_live"] = _rules_off(_why_no_rules())
            return
        strictness, why = await rule_results.strictness_for(
            session.get("name"), session.get("mode"), session.get("strictness"))
        rules = act["rules"]
        live_rules["ctx"] = {
            "aircraft_profile": (state.get("sop") or {}).get("aircraft_id"), "lesson_id": rt.lesson_id,
            "lesson_type": (state.get("sop") or {}).get("lesson_type"),
            "elevation_ft": (rt.lesson.get("airport") or {}).get("elevation_ft"),
            "rule_set_ids": [x["rule_set_id"] for x in act.get("layers") or []], "rules": snapshot(rules),
            "aircraft_note": aircraft_note(state.get("sop")), "strictness": strictness, "why": why}
        live_rules["ev"] = RuleEvaluator(rules, strictness)
        state["sop_live"] = {"status": "checking", "strictness": strictness, "strictness_why": why,
                             "aircraft_note": live_rules["ctx"]["aircraft_note"],
                             "n_rules": len(rules),
                             "active": [], "recent": [], "n_findings": 0, "summary": None}
        log.info("checking %d rule(s) live, %s (%s)", len(rules), strictness, why)

    def _check_rules_live(tel) -> None:
        ev = live_rules["ev"]
        sl = state.get("sop_live")
        if ev is None or sl is None:
            return
        found = ev.step(sample_from_telemetry(tel.as_dict(), rt.engine.phase))
        sl["active"] = ev.active()
        if not found:
            return
        pol = session.get("policy") or {}
        now = time.time()
        for f in found:
            sl["n_findings"] += 1
            sl["recent"] = ([f] + sl["recent"])[:8]
            if f["severity"] != "warning" or pol.get("coaching") == "silent":
                continue
            if now - live_rules["spoken"].get(f["rule_id"], 0.0) < RULE_REPEAT_S:
                continue
            live_rules["spoken"][f["rule_id"]] = now
            coach.submit(CoachEvent("SOP_RULE", severity="safety", data={**f, "phase": rt.engine.phase}))

    async def _finish_rule_checks() -> None:
        """End of a run: the live results go to the state (scorecard view) and,
        for an IRIS session, into flight_evaluations (Review re-checks from
        flight_data whenever asked)."""
        ev, ctx = live_rules["ev"], live_rules["ctx"]
        live_rules["ev"] = None
        if ev is None or ctx is None:
            return
        res = ev.results()
        if state.get("sop_live") is not None:
            state["sop_live"].update(active=[], summary=summarize(res))
        sid = session.get("session_id")
        if not sid or rule_results is None:
            return
        try:
            await rule_results.save(uuid.UUID(sid), source="live", strictness=ctx["strictness"], why=ctx["why"],
                                    context=ctx, results=res, n_samples=ev.n_samples,
                                    t_first=ev.t_first, t_last=ev.t_last)
        except Exception as e:      # IRIS not running (no sessions row) / DB down: live view still has it
            log.warning("couldn't store this flight's rule checks: %s", e)

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
        rt.tts = await loop.run_in_executor(None, TTS, cfg["voice"])
        tts_ready.set()

    async def speak(text: str):
        if state.get("muted"):
            return
        gen = speak_gen["n"]
        await tts_ready.wait()
        if gen != speak_gen["n"]:
            return
        if not rt.tts or not rt.tts.engine:
            log.warning("TTS unavailable. Coaching line not spoken: %s", text[:80])
            return
        state["speaking"] = True
        try:
            if to_tablet:
                tts_seq["n"] += 1
                wav = tts_dir / f"say_{tts_seq['n']}.wav"
                try:
                    ok = await asyncio.wait_for(
                        loop.run_in_executor(tts_pool, rt.tts.synth, text, str(wav)),
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
                await loop.run_in_executor(None, rt.tts.speak, text)
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
        if not rt.tts or not rt.tts.engine:
            return {"ok": False}
        wav = tts_dir / f"reading_{lesson_id}_{checkpoint_id}.wav"
        step = module.CHECKPOINTS.index(cp) + 1
        text = module.speech_text(cp, step)
        ok = await loop.run_in_executor(reading_pool, rt.tts.synth, text, str(wav))
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
        own = _lesson_text(rt.lesson.get("briefing"))
        if is_familiarization(rt.lesson):
            alt = (rt.lesson.get("airborne_start") or {}).get("alt_msl_ft", 0)
            await coach.say(own.replace("{alt}", f"{alt:,}"))
            rt.engine.start_flying()
            return
        if session.get("reference_recording"):
            await coach.say(phrases.render("BRIEFING_1_REF", {}))
        elif own:
            await coach.say(own)
        else:
            await coach.say(phrases.render("BRIEFING_1", {}))
        if not rt.lesson.get("segment"):        # whole-circuit wording; Take-off/Landing have their own
            await coach.say(phrases.render("BRIEFING_2", {}))
        if rt.lesson.get("airborne_start"):
            await _set_frozen(False)          # held on final through the briefing
        rt.engine.start_flying()

    def _lesson_clock() -> tuple[int, int]:
        """Sim local time the lesson is flown at: its night_event time when the
        lesson starts at night, otherwise mid-morning."""
        night = rt.lesson.get("night_event") or {}
        if night.get("at") == "start":
            h, m = map(int, night["local_time"].split(":"))
            return h, m
        return 10, 0

    async def _apply_lesson_clock():
        h, m = _lesson_clock()
        if not await loop.run_in_executor(None, rt.sim.set_local_time, h, m):
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
                await loop.run_in_executor(None, rt.sim.connect)
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
        rw = rt.lesson["runway"]
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
        rt.lesson = reciprocal_runway(rt.lesson)
        total = rt.engine.detector.total_circuits          # keep configure_session's choice
        rt.engine = make_engine(cfg, rt.lesson, aircraft)
        rt.engine.detector.total_circuits = total
        rt.scorer = make_scorer(rt.lesson, aircraft)
        rt.refs = ReferenceRegistry(rt.lesson, rt.lesson_id)
        state["lesson"] = lesson_state(rt.lesson)
        state.update(**rt.refs.payload_for_state())
        _apply_reference(session.get("reference", "planned"))
        log.info("aircraft is lined up on runway %s (heading %.0f): lesson geometry reversed",
                 rt.lesson["runway"]["ident"], tel.hdg)

    def _fam_freezes() -> bool:
        """Familiarization holds the aircraft still while it explains -- not in
        silent (Exam) coaching, where nothing is explained."""
        pol = session.get("policy") or {}
        return (is_familiarization(rt.lesson) and bool(rt.lesson.get("freeze_during_instructions"))
                and pol.get("coaching") != "silent")

    async def _set_frozen(on: bool) -> None:
        fn = getattr(rt.sim, "set_frozen", None)
        if fn and not await loop.run_in_executor(None, fn, on):
            log.warning("sim did not confirm freeze=%s", on)

    _speakable = phrases.speakable      # to-do text as MAVIS says it

    def _demos_wanted() -> bool:
        """Fly the whole airborne lesson once as a demonstration before the
        trainee's turn? Not in Exam (silent) coaching, not when the lesson
        says `demos: false`, not on a sim that can't be flown remotely."""
        pol = session.get("policy") or {}
        return (bool(rt.lesson.get("demos", True)) and pol.get("coaching") != "silent"
                and hasattr(rt.sim, "demo_control"))

    async def _fam_instruct(ev):
        """Speak one familiarization step with the aircraft frozen, then hand
        over: "You have control", unfreeze, and start the step's clock."""
        gen, eng = run_gen["n"], rt.engine
        phase = ev.data.get("phase")
        try:
            await _set_frozen(True)
            await coach.say(phrases.render("LESSON_NOTE", ev.data), phase=phase)
            if gen == run_gen["n"] and started.is_set():
                await coach.say(phrases.render("FAM_YOU_HAVE_CONTROL", {}), phase=phase)
        finally:
            if gen == run_gen["n"] and eng is rt.engine:
                await _set_frozen(False)
                eng.release()
                if hasattr(rt.sim, "comply"):     # mock student flies the step from here
                    rt.sim.comply(f"PHASE_ENTER_{phase}", ev.data)

    async def _lock_controls(on: bool) -> None:
        """Trainee's stick/pedals/throttle/trim locked out while MAVIS flies
        the demo, handed back after."""
        fn = getattr(rt.sim, "lock_controls", None)
        if fn and not await loop.run_in_executor(None, fn, on):
            log.warning("sim did not take lock_controls(%s): the trainee's controls "
                        "may still move the aircraft during the demo", on)

    async def _fly_demo(pilot: DemoPilot, gen: int):
        """Feed the demo pilot live telemetry and send its commands to the sim."""
        last = time.time()
        while not pilot.done and gen == run_gen["n"]:
            now = time.time()
            cmd = pilot.tick(state.get("tel") or {}, now - last)
            last = now
            if cmd:
                await loop.run_in_executor(None, rt.sim.demo_control, cmd)
            await asyncio.sleep(0.2)

    async def _fam_opening(briefing: bool):
        """Start of an airborne lesson: the briefing (turn-by-turn modes), the
        whole lesson flown once as a demonstration, then the hand-over, so the
        trainee then flies every step in one go."""
        gen = run_gen["n"]
        try:
            if briefing:
                alt = (rt.lesson.get("airborne_start") or {}).get("alt_msl_ft", 0)
                await coach.say(_lesson_text(rt.lesson.get("briefing")).replace("{alt}", f"{alt:,}"))
            if gen != run_gen["n"]:
                return
            await _fam_demo_all(gen)
            if gen != run_gen["n"]:
                return
            if demo_ctl["shown"]:
                await coach.say(phrases.render("FAM_YOU_HAVE_CONTROL", {}))
                if gen != run_gen["n"]:
                    return
            # controls stay locked from the demo's start until "You have control"
            await _lock_controls(False)
            if demo_ctl["shown"]:
                await _set_frozen(False)
            rt.engine.start_flying()
        finally:
            await _lock_controls(False)     # whatever happened: never leave them locked

    async def _fam_demo_all(gen: int):
        """"I have control": the sim flies every step in order while MAVIS
        explains each, then the aircraft goes back to the start, frozen.
        Skip (state.demo, /demo/skip_demo) ends it there; steps not shown in
        full are explained the usual way (held still) when the trainee gets
        to them."""
        # fresh from the sim: right after the airborne start, state["tel"] can
        # still be the parked aircraft from before it
        tel0 = (await loop.run_in_executor(None, rt.sim.get_telemetry)).as_dict()
        steps = [s for s in rt.engine.detector.ORDER[1:-1]
                 if s not in NO_DEMO and (rt.engine.notes.get(s) or {}).get("demo", True)]
        if not steps or not tel0 or tel0.get("on_ground", True):
            return
        mock = cfg["sim"]["adapter"] == "mock"
        base_th = tel0.get("throttle", 0.7)
        # the whole demo in about two minutes (lesson demo_max_s): each step gets
        # an equal share of the flying time, less a little for the opening and
        # closing lines
        # steps that change height need longer to show than the steady ones
        weight = {s: (3.0 if s in DEMO_LONG_STEPS else 1.0) for s in steps}
        unit = (float(rt.lesson.get("demo_max_s", 120)) - 10.0) / sum(weight.values())
        demo_ctl["skip"].clear()
        state["demo"] = {"n": 0, "of": len(steps), "title": "Getting ready"}
        flew = skipped = False
        try:
            await coach.say(phrases.render("FAM_DEMO_START", {}))
            if demo_ctl["skip"].is_set() or gen != run_gen["n"]:
                return
            await _lock_controls(True)          # hands off: MAVIS has control
            await _set_frozen(False)
            flew = True
            # MAVIS flies this stretch: marked as its own event so it is never mistaken for
            # (or graded as) the trainee's flying
            event_log.observe("DEMO", _sim_now(), kind="demo")
            _publish_events()
            for i, step in enumerate(steps):
                tel = dict(state.get("tel") or tel0)
                data = rt.engine.preview(step, Telemetry(**tel), demo=True)
                if data.get("demo_layman"):
                    # a step the autopilot can't fly: its explain-only lines
                    data = {**data, "layman": data["demo_layman"],
                            "pilot": data.get("demo_pilot") or data["demo_layman"]}
                state["demo"] = {"n": i + 1, "of": len(steps),
                                 "title": data.get("title") or STEP_TITLES.get(step, step),
                                 "describe": step in DESCRIBE_ONLY}
                state["look_at"] = {"items": data.get("look") or [], "phase": step, "seq": time.time()}
                pilot = DemoPilot(step, rt.engine, tel, mock=mock, base_throttle=base_th,
                                  max_s=max(4.0, unit * weight[step]))
                speak = asyncio.create_task(coach.say(phrases.render("LESSON_NOTE", data), phase=step))
                fly = asyncio.create_task(_fly_demo(pilot, gen))
                skip_w = asyncio.create_task(demo_ctl["skip"].wait())
                await asyncio.wait({fly, skip_w}, return_when=asyncio.FIRST_COMPLETED)
                if not demo_ctl["skip"].is_set():
                    # flown: let the explanation finish (the autopilot holds it)
                    await asyncio.wait({speak, skip_w}, return_when=asyncio.FIRST_COMPLETED)
                skipped = demo_ctl["skip"].is_set() or gen != run_gen["n"]
                for t in (fly, skip_w) + ((speak,) if skipped else ()):
                    t.cancel()
                if skipped:
                    stop_speech()
                    break
                if pilot.rescued:
                    log.warning("demo: %s abandoned near the stall (%.0f kt) -- recovered",
                                step, (state.get("tel") or {}).get("ias", 0))
                demo_ctl["shown"].add(step)
            skipped = skipped or demo_ctl["skip"].is_set()
        finally:
            if flew and gen == run_gen["n"]:
                event_log.close(_sim_now(), "skipped" if (skipped or demo_ctl["skip"].is_set()) else "completed")
                _publish_events()
            await loop.run_in_executor(None, rt.sim.demo_control, {"ap": False, "rudder": 0.0})
            state["demo"] = None
            state["look_at"] = None
            if flew and gen == run_gen["n"]:
                await _set_frozen(True)
                ok = await loop.run_in_executor(None, lambda: rt.sim.set_position(
                    tel0["lat"], tel0["lon"], tel0["alt_msl"], tel0["hdg"], max(60.0, tel0["ias"]),
                    throttle_pct=base_th * 100.0))
                if not ok:
                    log.warning("demo: couldn't put the aircraft back at the start")
                state["track"].append([])      # the trainee's own flight starts a fresh line
        if gen == run_gen["n"] and demo_ctl["shown"]:
            await coach.say(phrases.render("FAM_DEMO_SKIPPED" if skipped else "FAM_DEMO_END", {}))

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
        await _to_runway_start()

    async def _to_runway_start() -> bool:
        """Aircraft lined up at the start of the lesson's runway, stopped,
        throttle idle."""
        fn = getattr(rt.sim, "set_position", None)
        lat, lon, hdg = runway_start(rt.lesson)
        elev = rt.lesson["airport"]["elevation_ft"]
        ok = fn is not None and await loop.run_in_executor(
            None, lambda: fn(lat, lon, elev, hdg, 0, throttle_pct=0, on_ground=True))
        if ok:
            log.info("aircraft placed at the start of runway %s", rt.lesson["runway"]["ident"])
        else:
            log.warning("couldn't put the aircraft at the start of the runway")
        return ok

    async def _airborne_start():
        """Familiarization: place the aircraft in the air before the session
        starts. Raises (a 500 on POST /demo/start, shown on the start menu)
        if the sim can't do it, so no session is minted."""
        a = rt.lesson.get("airborne_start") or {}
        lat, lon = start_position(rt.lesson)
        fn = getattr(rt.sim, "set_position", None)
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
            tel = await loop.run_in_executor(None, rt.sim.get_telemetry)
            if not tel.on_ground and tel.alt_agl > 500:
                return
            await asyncio.sleep(0.2)
        log.warning("airborne start: telemetry still not airborne after 6 s, continuing")

    async def start(keep_session: bool = False):
        if started.is_set():
            return
        if cfg["sim"]["adapter"] == "msfs":
            await _connect_msfs_with_retries()
            if not is_familiarization(rt.lesson) and not rt.lesson.get("airborne_start"):
                _orient_lesson_to_aircraft(await loop.run_in_executor(None, rt.sim.get_telemetry))
        if rt.lesson.get("airborne_start"):
            await _airborne_start()
            if _fam_freezes():
                await _set_frozen(True)     # held through the briefing
            elif (not is_familiarization(rt.lesson)
                    and (session.get("policy") or {}).get("coaching") == "turn_by_turn"):
                await _set_frozen(True)     # landing: held on final while it's briefed
        state["look_at"] = None
        demo_ctl["shown"] = set()
        started.set()
        asyncio.create_task(_load_sop())     # never blocks the start of the lesson
        new_session = not (keep_session and session.get("session_id"))
        if new_session:
            session["session_id"] = str(uuid.uuid4())
            event_log.reset()
        state["session_id"] = session["session_id"]
        # a new flight in this session: which curriculum it came from (free flight is
        # not a lesson, whatever lesson MAVIS happens to have loaded underneath)
        meta = lesson_meta(catalog, rt.lesson_id) or {}
        free = bool(session.get("free_flight"))
        free_tracker.reset()
        labels, kind = event_log_setup(rt.lesson, rt.engine)
        event_log.labels = {**labels, **(FREE_FLIGHT_LABELS if free else {})}
        event_log.kind = kind
        if not event_log.in_flight:            # a Restart carries on the flight it interrupted
            event_log.begin_flight(
                _sim_now(), rt.lesson_id,
                "Free Flight" if free else (meta.get("title") or rt.lesson.get("name") or rt.lesson_id),
                "free_flight" if free else "lesson",
                "" if free else meta.get("module_title", ""), session.get("mode", ""))
        _publish_events()
        state["lesson_running"] = True
        if (rt.lesson.get("night_event") or {}).get("at") == "start":
            await _apply_lesson_clock()
            rt.engine.mark_night_done()
        # Never block mock / lesson start on briefing TTS, warmup can hog the GPU.
        if hasattr(rt.sim, "release"):        # mock: start the autopilot immediately
            rt.sim.release()
        pol = session.get("policy") or {}
        if is_familiarization(rt.lesson) and _demos_wanted():
            # briefing, the whole lesson flown once as a demo, then hand over
            asyncio.create_task(_fam_opening(pol.get("coaching") == "turn_by_turn"))
        elif pol.get("coaching") == "turn_by_turn":
            asyncio.create_task(_briefing_lines())
        else:
            rt.engine.start_flying()
        tel0 = await loop.run_in_executor(None, rt.sim.get_telemetry)
        checkpoints[rt.engine.detector.ORDER[1]] = {
            "tel": tel0.as_dict(), "circuit": rt.engine.circuit,
            "blob": rt.sim.save_state() if hasattr(rt.sim, "save_state") else None}

    async def night_sequence():
        if not rt.lesson.get("night_event"):
            return
        rt.engine.mark_night_done()
        silent = (session.get("policy") or {}).get("coaching") == "silent"
        if not silent:
            await coach.say(phrases.render("NIGHT_ANNOUNCE", {}))
        h, m = map(int, rt.lesson["night_event"]["local_time"].split(":"))
        if not await loop.run_in_executor(None, rt.sim.set_local_time, h, m):
            log.error("sim rejected time change - use fallback keybind")
        await asyncio.sleep(2)
        if not silent:
            await coach.say(phrases.render("NIGHT_AFTER", {}))

    async def restart():
        stop_speech()
        run_gen["n"] += 1
        live_rules["ev"] = None
        state["sop_live"] = None
        rt.engine.restart()
        rt.scorer.reset()
        recorder.reset()
        # Restart: same session, same flight. The attempt in progress ends as
        # "restarted" (still data); the next attempt's events count as try 2.
        event_log.restart_attempt(_sim_now())
        _publish_events()
        scored["done"] = False
        coach.resume()
        session.pop("reference_recording", None)
        rt.engine.detector.total_circuits = int(rt.lesson.get("total_circuits", 1))
        state["total_circuits"] = rt.engine.detector.total_circuits
        _discard_card()
        state["speaking"] = False
        started.clear()
        state["lesson_running"] = False
        intro_shown.clear()
        checkpoints.clear()
        state["track"] = [[]]
        state["look_at"] = None
        transcript.clear()
        if is_familiarization(rt.lesson):
            await _set_frozen(False)
        if hasattr(rt.sim, "reset"):
            rt.sim.reset()
            await _apply_lesson_clock()   # mock reset returns to daytime

    relaunching = asyncio.Lock()

    async def relaunch(new_session: bool = False):
        """Reset the run, put the aircraft back where the lesson starts -- the
        airborne start for familiarization (start() does that), the start of the
        runway for the circuit lessons -- and begin again. The Restart button keeps
        the same session; the scorecard's Fly again (new_session) starts a new one."""
        if relaunching.locked():
            return                      # a double-click shouldn't restart twice
        async with relaunching:
            await restart()
            if not rt.lesson.get("airborne_start") and cfg["sim"]["adapter"] == "msfs":
                await _connect_msfs_with_retries()
                if not await _to_runway_start():
                    raise RuntimeError("Couldn't move the aircraft back to the start of the "
                                       "runway -- check MAVIS's log.")
            await start(keep_session=not new_session)

    async def relaunch_new():
        """Scorecard "Fly again": a whole new cycle -- new session, new recording."""
        await relaunch(new_session=True)

    async def mute():
        state["muted"] = not state.get("muted", False)

    # ---------- checkpoints ("let me try that again") + forced repeats (runtime/checkpoints.py) ----------
    checkpoints: dict[str, dict] = {}   # phase -> {"tel", "blob", "circuit"}
    attempts: dict[str, int] = {}       # graded leg -> forced repeats so far (up to MAX_ATTEMPTS)
    retry, phase_passed = build_checkpoints(rt, state, coach, loop, event_log, _sim_now, checkpoints)

    def _sync_refs_to_state() -> None:
        state.update(rt.refs.payload_for_state())

    def _apply_reference(ref_id: str) -> None:
        prof = rt.refs.get(ref_id)
        session["reference"] = prof.id
        state["reference"] = {"id": prof.id, "name": prof.display_name}
        state["reference_track"] = prof.track
        rt.engine.monitors.apply_reference(prof)

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
        if new_id == rt.lesson_id:
            return {"ok": True, "lesson_id": rt.lesson_id}
        meta = lesson_meta(catalog, new_id)
        path = HERE / "lessons" / f"{new_id}.yaml"
        if (not meta or not meta.get("available") or meta.get("kind") == "reading"
                or not path.is_file()):
            return {"ok": False, "error": "lesson not available"}
        if started.is_set():
            return {"ok": False, "error": "lesson in progress"}
        if is_familiarization(rt.lesson):
            await _set_frozen(False)
        stop_speech()
        coach.pause()
        rt.lesson = load_yaml(path)
        rt.lesson_id = new_id
        if cfg["sim"]["adapter"] == "mock":      # mock flies the lesson's own geometry
            rt.sim = make_adapter(cfg, rt.lesson)
            rt.sim.connect()
        rt.lesson = lesson_for_sim(cfg, rt.sim, rt.lesson)
        phrases.set_lesson_lines(rt.lesson.get("say"))
        rt.engine = make_engine(cfg, rt.lesson, aircraft)
        rt.scorer = make_scorer(rt.lesson, aircraft)
        rt.refs = ReferenceRegistry(rt.lesson, rt.lesson_id)
        recorder.reset()
        event_log.configure(*event_log_setup(rt.lesson, rt.engine))
        _publish_events()
        attempts.clear()
        checkpoints.clear()
        scored["done"] = False
        _discard_card()
        transcript.clear()
        state.update(
            phase=rt.engine.phase, circuit=1,
            total_circuits=int(rt.lesson.get("total_circuits", 1)),
            track=[[]], scorecard=None, speaking=False, look_at=None, demo=None,
            lesson_id=rt.lesson_id,
            active_lesson=meta,
            reference_track=None,
            lesson=lesson_state(rt.lesson),
            **rt.refs.payload_for_state())
        state["scales"]["alt_max"] = alt_scale_max(rt.lesson)
        _apply_reference("planned")
        await _apply_lesson_clock()
        coach.resume()
        log.info("lesson -> %s (%s)", rt.lesson_id, rt.lesson.get("name", rt.lesson_id))
        return {"ok": True, "lesson_id": rt.lesson_id}

    async def configure_session(mode: str, jargon: str, reference: str = "planned",
                                reference_recording: bool = False,
                                trainee_name: str = "",
                                tone: str = "warm", free_flight: bool = False,
                                strictness: str = ""):
        stop_speech()
        coach.pause()
        if is_familiarization(rt.lesson):
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
        session["free_flight"] = bool(free_flight)
        session["name"] = (trainee_name or "").strip()[:48]
        # rule-check strictness chosen for this session; empty = the trainee's, else the mode's
        session["strictness"] = strictness if strictness in ("lenient", "standard", "strict") else None
        tc = int(rt.lesson.get("total_circuits", 1))
        rt.engine.detector.total_circuits = 1 if reference_recording else tc
        state["total_circuits"] = rt.engine.detector.total_circuits
        phrases.configure(flap_style, session["jargon"],
                          guidance=policy.get("guidance", "direct"),
                          tone=session["tone"])
        attempts.clear()
        run_gen["n"] += 1
        scored["done"] = False
        _discard_card()
        rt.engine.restart()
        rt.scorer.reset()
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
        if prev != rid and started.is_set() and rt.engine.briefing_done:
            await _announce_reference_change(rt.refs.get(rid))

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
        if not rt.refs.rename(run_id, display_name):
            return False
        _sync_refs_to_state()
        if state["reference"].get("id") == run_id:
            state["reference"]["name"] = display_name.strip()[:64]
        return True

    async def instructor_delete(run_id: str) -> bool:
        if not session.get("instructor_unlocked"):
            return False
        if not rt.refs.delete(run_id):
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

    # ---------- chat: rule-based intents + agentic tool layer (runtime/chat_router.py) ----------
    chat = build_chat(rt, state, coach, loop, retry)

    # ---------- lesson scoring + leaderboard (spec §14) ----------
    def _card_state(card, posted, debrief_pending: bool = False):
        ref_id = session.get("reference", "planned")
        prof = rt.refs.get(ref_id)
        return {"seq": score_seq["n"], "name": card.name, "mode": card.mode,
                "scores": card.scores, "retries": card.retries, "total": card.total,
                "grade": card.grade, "capped": card.capped, "practice": card.practice,
                "incomplete": (card.incomplete if card.incomplete is not None
                               else card.scores.get("landing") is None),
                "scored_reference": ref_id,
                "scored_reference_name": prof.display_name if prof else "Planned circuit",
                "lesson_type": "familiarization" if is_familiarization(rt.lesson) else "circuit",
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
        """Use the trainee's name at most once; strip invented names when none
        on file; keep it to a short spoken length."""
        text = clip_spoken(text, 35)
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
        if is_familiarization(rt.lesson):
            return await _make_fam_debrief(card, sc)
        best, worst = max(sc, key=sc.get), min(sc, key=sc.get)
        fallback = (f"Nicely flown. Best was your {best}; work on {worst} next time."
                    if rt.lesson.get("segment") else
                    f"Nice circuit. Best was your {best}; work on {worst} next time.")
        if card.incomplete and rt.lesson.get("segment"):
            fallback = f"Good effort. Your {best} stood out. We didn't finish it this time."
        elif card.scores.get("landing") is None and not rt.lesson.get("segment"):
            fallback = f"Good effort. Your {best} stood out. No landing yet, so that's unscored."
        if not coach.llm_ok:
            return fallback
        who = (session.get("name") or "").strip()
        who_line = (f"The trainee's name is {who}. Use that name at most once in the whole debrief."
                    if who else "Address the trainee only as 'you'. Do not use any proper name.")
        what = rt.lesson.get("debrief_summary") or "circuit"
        sysp = (phrases.PERSONA + f"\nWrite a spoken debrief of the trainee's {what} from "
                "these 0-100 scores: two short sentences, 30 words at most. Name one strength "
                "and one thing to work on. No lists; don't read out the numbers. " + who_line)
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
                 "bank": "turns", "climb_speed": "climb speed", "speed": "speed control",
                 "level_off": "levelling off",
                 "smoothness": "smooth hands"}
        best, worst = max(sc, key=sc.get), min(sc, key=sc.get)
        fallback = f"Lovely flying. Best was your {names[best]}; work on {names[worst]} next time."
        if card.incomplete:
            fallback = (f"Good effort. Your {names[best]} stood out. "
                        + ("We didn't reach Paya Lebar this time." if rt.engine.finds_field
                           else "We didn't get through every step this time."))
        if not coach.llm_ok:
            return fallback
        who = (session.get("name") or "").strip()
        who_line = (f"The trainee's name is {who}. Use that name at most once in the whole debrief."
                    if who else "Address the trainee only as 'you'. Do not use any proper name.")
        about = (rt.lesson.get("debrief_summary") or "flight familiarisation exercise (airborne "
                 "start, straight and level, a turn each way, a climb and a descent, then "
                 "finding Paya Lebar; no take-off or landing)")
        sysp = (phrases.PERSONA + f"\nWrite a spoken debrief of the trainee's {about} from "
                "these 0-100 scores: two short sentences, 30 words at most. Name one strength "
                "and one thing to work on. No lists; don't read out the numbers. " + who_line)
        if not rt.engine.finds_field:
            # the persona describes a circuit at Paya Lebar; this lesson has no
            # navigation part, so keep the model from inventing one
            sysp += (" This lesson has no navigation: do not mention Paya Lebar, the airfield, "
                     "finding it, or flying back to it.")
        reached = "yes" if not card.incomplete else "no"   # finished the lesson
        try:
            out = await coach._ollama(
                sysp, f"scores={sc} finished_lesson={reached} total={card.total} grade={card.grade}", 10.0)
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
            code = (("FAM_DEBRIEF" if rt.engine.finds_field else "FAM_DEBRIEF_DONE")
                    if is_familiarization(rt.lesson) else "PHASE_ENTER_DEBRIEF")
            own = _lesson_text(rt.lesson.get("closing"))    # e.g. Take-off / Landing's own close
            await coach.say(own or phrases.render(code, {
                "phase": "DEBRIEF", "circuit": rt.engine.circuit}), phase="DEBRIEF")
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
        if hasattr(rt.scorer, "skipped"):         # familiarization: timed-out steps cost points
            rt.scorer.skipped = set(getattr(rt.engine, "skipped", ()))
        card = rt.scorer.finalize(
            session.get("name", ""), session["mode"], rt.lesson_id, practice,
            score_caps=bool(pol.get("score_caps")))
        # §15 groundwork: hold this run's recording (for reference re-score later) + save last run
        pending["recording"] = recorder.to_dict(meta={
            "lesson": rt.lesson_id, "aircraft": Path(cfg["aircraft_profile"]).stem,
            "mode": session["mode"], "reference": session.get("reference", "planned"),
            "practice": practice, "name": session.get("name", ""),
            "events": event_log.to_list()})
        try:
            recorder.save(HERE / "data" / "recordings" / "last_run.json")
        except Exception as e:
            log.debug("recording save failed: %s", e)
        pending["card"], pending["posted"] = card, False
        await _finish_rule_checks()
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
        rt.engine.briefing_done = False

    quit_task: dict = {"t": None}   # keeps a reference so the task isn't GC'd

    async def quit_lesson():
        # trainee pressed Back before finishing -> practice summary, NOT posted
        if is_familiarization(rt.lesson):
            await _set_frozen(False)        # hand the aircraft back on the way out
        if scored["done"]:
            # the flight already finished on its own and is scored: nothing more to score,
            # but leaving the lesson must still end the run -- otherwise it stays "in
            # progress", the next lesson can't be selected and the page re-enters it
            started.clear()
            state["lesson_running"] = False
            intro_shown.clear()
            return
        scored["done"] = True
        # a Free Flight has no lesson to complete: ending it is how it finishes
        event_log.end_flight(_sim_now(), "completed" if session.get("free_flight") else "abandoned")
        _publish_events()
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

    async def skip_demo():
        demo_ctl["skip"].set()

    async def score_submit(name: str, given: str = "", family: str = ""):
        _post_card(name, given, family)

    actions = {"start": start, "night": night_sequence,
               "skip": _wrap(lambda: rt.engine.skip_phase()), "restart": restart,
               "skip_demo": skip_demo,
               "relaunch": relaunch, "relaunch_new": relaunch_new,
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
                events = rt.engine.tick(tel, ctrl)
                # trainee/pilot: if a graded leg just ended below tolerance, send
                # them back to re-fly it (bounded) instead of progressing. Never in Free
                # Flight: there is no set track to be held to, and MAVIS must not move
                # the aircraft on its own there.
                pol = session.get("policy") or {}
                force_redo = (pol.get("force_repeat")
                              and not session.get("free_flight")
                              and not session.get("reference_recording")
                              and rt.engine.phase != prev_phase
                              and prev_phase in GATED
                              and not phase_passed(prev_phase, tel)
                              and attempts.get(prev_phase, 0) < MAX_ATTEMPTS)
                for ev in events:
                    if force_redo and ev.code.startswith("PHASE_ENTER_"):
                        continue   # don't announce the new leg; we're reverting
                    if ev.code == "PHASE_ENTER_DEBRIEF":
                        continue   # finish_lesson/_await_spoken_close owns this line
                    if (not allow_coach_event(pol, ev.code, ev.severity)
                            and ev.code not in (rt.lesson.get("always_say") or [])
                            # "now come back" calls: every mode but Exam (silent)
                            and not (ev.code in PART_CUES and pol.get("coaching") != "silent")):
                        continue
                    if ev.code == "LESSON_NOTE" and is_familiarization(rt.lesson):
                        state["look_at"] = {"items": ev.data.get("look") or [],
                                            "phase": ev.data.get("phase"), "seq": time.time()}
                        if ev.data.get("phase") in demo_ctl["shown"]:
                            # watched in the demo: a short cue, no hold -- keep the momentum
                            title = ev.data.get("title") or ev.data.get("phase")
                            action = (ev.data.get("todo") or [""])[0]
                            try:
                                action = action.format(**ev.data)    # "Bank left {med_bank_lo}-..."
                            except (KeyError, IndexError, ValueError):
                                pass
                            cue = phrases.render("FAM_STEP_CUE", {
                                "title": title, "title_lc": title[:1].lower() + title[1:],
                                "action": _speakable(action).rstrip(".")})
                            ev = CoachEvent("LESSON_NOTE", ev.severity,
                                            {**ev.data, "layman": cue, "pilot": cue}, cooldown_s=0)
                        elif _fam_freezes() and ev.data.get("freeze", True):
                            rt.engine.hold()       # judge nothing until "you have control"
                            asyncio.create_task(_fam_instruct(ev))
                            continue
                        if hasattr(rt.sim, "comply"):
                            # mock student: fly the step even when the mode
                            # filters out its PHASE_ENTER announcement
                            rt.sim.comply(f"PHASE_ENTER_{ev.data.get('phase')}", ev.data)
                    coach.submit(ev)
                    if hasattr(rt.sim, "comply"):   # mock student obeys callouts
                        rt.sim.comply(ev.code, ev.data)
                    if ev.code.startswith("PHASE_ENTER_"):   # checkpoint each phase
                        checkpoints[ev.data["phase"]] = {
                            "tel": tel.as_dict(), "circuit": rt.engine.circuit,
                            "blob": rt.sim.save_state() if hasattr(rt.sim, "save_state") else None}
                if force_redo:
                    attempts[prev_phase] = attempts.get(prev_phase, 0) + 1
                    asyncio.create_task(retry(prev_phase, "FORCE_REPEAT"))
                # ---- scoring: sample the tick, capture touchdowns, finalize at debrief ----
                if rt.engine.briefing_done:
                    geom_d = ({"along": rt.engine.geom.along, "cross": rt.engine.geom.cross}
                              if rt.engine.geom else None)
                    if not getattr(rt.engine, "held", False):   # frozen for an explanation: not flying
                        rt.scorer.sample(tel.as_dict(), geom_d, ctrl.as_dict(), rt.engine.phase)
                    recorder.sample(tel.as_dict(), ctrl.as_dict(), geom_d, rt.engine.phase)
                    if not getattr(rt.engine, "held", False):
                        try:
                            _check_rules_live(tel)
                        except Exception:       # a rule check must never stop the lesson
                            log.exception("live rule check failed -- rule checks off for this run")
                            live_rules["ev"] = None
                    # the to-do list / phase tracker decides event boundaries: when it
                    # moves on, the previous event ends and the next starts, at this tick
                    if session.get("free_flight"):
                        free_tracker.tick(tel)
                    changed = event_log.observe(_flight_phase(), tel.t, rt.engine.circuit,
                                                getattr(rt.engine, "skipped", ()),
                                                kind="stall" if _flight_phase() == "STALL" else None)
                    if rt.engine.phase == "DEBRIEF" and not scored["done"]:
                        event_log.end_flight(tel.t, "completed")
                        changed = True
                    if changed:
                        _publish_events()
                    if prev_on_ground is False and tel.on_ground \
                            and rt.engine.phase in ("FLARE_TOUCHDOWN", "FINAL", "ROLLOUT"):
                        rt.scorer.on_landing(tel.as_dict(), geom_d)
                    prev_on_ground = tel.on_ground
                    if rt.engine.phase == "DEBRIEF" and not scored["done"]:
                        scored["done"] = True
                        if is_familiarization(rt.lesson) or rt.lesson.get("end_reposition"):
                            # practice over: freeze, score, then back to the runway
                            # (airborne lessons, and the take-off lesson)
                            asyncio.create_task(_fam_finish())
                        else:
                            asyncio.create_task(finish_lesson(False))
            elif not spike:
                last_good = tel
            if not scored["done"] and rt.lesson.get("night_event") \
                    and rt.engine.wants_night_switch() and night_task is None:
                night_task = asyncio.create_task(night_sequence())
            state.update(
                tel=tel.as_dict(), ctrl=ctrl.as_dict(), phase=_flight_phase(),
                circuit=rt.engine.circuit, total_circuits=rt.engine.detector.total_circuits,
                targets=rt.engine.targets(),
                night=not (6.0 < tel.local_time_h < 18.5),
                transcript=transcript[-60:], llm_ok=coach.llm_ok,
                fam_todo=rt.engine.todo() if started.is_set() and hasattr(rt.engine, "todo") else None,
                geom={"along": rt.engine.geom.along, "cross": rt.engine.geom.cross}
                     if rt.engine.geom else None)
            # Ground track keeps the whole flight (taxi, take-off roll and
            # rollout included), like IRIS's post-flight ground track. Long
            # flights are thinned rather than trimmed: past TRACK_MAX points
            # the older part is halved, so the full path stays on the map
            # (just coarser at the start) and the payload stays bounded.
            if not scored["done"] and t0 - last_track > 1.0 and not spike \
                    and abs(tel.lat) > 0.01 and not state.get("demo"):
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
