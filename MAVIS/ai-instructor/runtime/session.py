"""Session lifecycle: the web actions that set up, start, restart and end a
run (configure_session, select_lesson, start, restart, relaunch, quit) and the
reference-profile choice.

A new run bumps rt.run_gen so background tasks of the old one stand down;
start() mints the session_id IRIS keys its flight data on."""
from __future__ import annotations
import asyncio
import logging
import uuid

from coach import phrases
from core.familiarization import is_familiarization
from core.free_flight import LABELS as FREE_FLIGHT_LABELS
from core.lesson_catalog import lesson_meta
from core.modes import policy_for, normalize_mode
from core.reference_profiles import ReferenceRegistry
from runtime.bootstrap import (HERE, alt_scale_max, event_log_setup, lesson_for_sim, lesson_state,
                               load_yaml, make_adapter, make_engine, make_scorer)

log = logging.getLogger("main")     # same logger name as before the move: log lines unchanged


def build_session(rt):
    """Defines the session actions and publishes them on rt."""
    async def start(keep_session: bool = False):
        if rt.started.is_set():
            return
        if rt.cfg["sim"]["adapter"] == "msfs":
            await rt._connect_msfs_with_retries()
            if not is_familiarization(rt.lesson) and not rt.lesson.get("airborne_start"):
                rt._orient_lesson_to_aircraft(await rt.loop.run_in_executor(None, rt.sim.get_telemetry))
        if rt.lesson.get("airborne_start"):
            await rt._airborne_start()
            if rt._fam_freezes():
                await rt._set_frozen(True)     # held through the briefing
            elif (not is_familiarization(rt.lesson)
                    and (rt.session.get("policy") or {}).get("coaching") == "turn_by_turn"):
                await rt._set_frozen(True)     # landing: held on final while it's briefed
        rt.state["look_at"] = None
        rt.demo_ctl["shown"] = set()
        rt.started.set()
        asyncio.create_task(rt._load_sop())     # never blocks the start of the lesson
        new_session = not (keep_session and rt.session.get("session_id"))
        if new_session:
            rt.session["session_id"] = str(uuid.uuid4())
            rt.event_log.reset()
        rt.state["session_id"] = rt.session["session_id"]
        # a new flight in this session: which curriculum it came from (free flight is
        # not a lesson, whatever lesson MAVIS happens to have loaded underneath)
        meta = lesson_meta(rt.catalog, rt.lesson_id) or {}
        free = bool(rt.session.get("free_flight"))
        rt.free_tracker.reset()
        labels, kind = event_log_setup(rt.lesson, rt.engine)
        rt.event_log.labels = {**labels, **(FREE_FLIGHT_LABELS if free else {})}
        rt.event_log.kind = kind
        if not rt.event_log.in_flight:            # a Restart carries on the flight it interrupted
            rt.event_log.begin_flight(
                rt._sim_now(), rt.lesson_id,
                "Free Flight" if free else (meta.get("title") or rt.lesson.get("name") or rt.lesson_id),
                "free_flight" if free else "lesson",
                "" if free else meta.get("module_title", ""), rt.session.get("mode", ""))
        rt._publish_events()
        rt.state["lesson_running"] = True
        if (rt.lesson.get("night_event") or {}).get("at") == "start":
            await rt._apply_lesson_clock()
            rt.engine.mark_night_done()
        # Never block mock / lesson start on briefing TTS, warmup can hog the GPU.
        if hasattr(rt.sim, "release"):        # mock: start the autopilot immediately
            rt.sim.release()
        pol = rt.session.get("policy") or {}
        if is_familiarization(rt.lesson) and rt._demos_wanted():
            # briefing, the whole lesson flown once as a demo, then hand over
            asyncio.create_task(rt._fam_opening(pol.get("coaching") == "turn_by_turn"))
        elif pol.get("coaching") == "turn_by_turn":
            asyncio.create_task(rt._briefing_lines())
        else:
            rt.engine.start_flying()
        tel0 = await rt.loop.run_in_executor(None, rt.sim.get_telemetry)
        rt.checkpoints[rt.engine.detector.ORDER[1]] = {
            "tel": tel0.as_dict(), "circuit": rt.engine.circuit,
            "blob": rt.sim.save_state() if hasattr(rt.sim, "save_state") else None}

    async def restart():
        rt.stop_speech()
        rt.run_gen["n"] += 1
        rt.live_rules["ev"] = None
        rt.state["sop_live"] = None
        rt.engine.restart()
        rt.scorer.reset()
        rt.recorder.reset()
        # Restart: same session, same flight. The attempt in progress ends as
        # "restarted" (still data); the next attempt's events count as try 2.
        rt.event_log.restart_attempt(rt._sim_now())
        rt._publish_events()
        rt.scored["done"] = False
        rt.coach.resume()
        rt.session.pop("reference_recording", None)
        rt.engine.detector.total_circuits = int(rt.lesson.get("total_circuits", 1))
        rt.state["total_circuits"] = rt.engine.detector.total_circuits
        rt._discard_card()
        rt.state["speaking"] = False
        rt.started.clear()
        rt.state["lesson_running"] = False
        intro_shown.clear()
        rt.checkpoints.clear()
        rt.state["track"] = [[]]
        rt.state["look_at"] = None
        rt.transcript.clear()
        if is_familiarization(rt.lesson):
            await rt._set_frozen(False)
        if hasattr(rt.sim, "reset"):
            rt.sim.reset()
            await rt._apply_lesson_clock()   # mock reset returns to daytime

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
            if not rt.lesson.get("airborne_start") and rt.cfg["sim"]["adapter"] == "msfs":
                await rt._connect_msfs_with_retries()
                if not await rt._to_runway_start():
                    raise RuntimeError("Couldn't move the aircraft back to the start of the "
                                       "runway -- check MAVIS's log.")
            await start(keep_session=not new_session)

    async def relaunch_new():
        """Scorecard "Fly again": a whole new cycle -- new session, new recording."""
        await relaunch(new_session=True)

    async def mute():
        rt.state["muted"] = not rt.state.get("muted", False)

    def _sync_refs_to_state() -> None:
        rt.state.update(rt.refs.payload_for_state())

    def _apply_reference(ref_id: str) -> None:
        prof = rt.refs.get(ref_id)
        rt.session["reference"] = prof.id
        rt.state["reference"] = {"id": prof.id, "name": prof.display_name}
        rt.state["reference_track"] = prof.track
        rt.engine.monitors.apply_reference(prof)

    async def _announce_reference_change(prof) -> None:
        if prof.id == "planned":
            await rt.coach.say("Back to the planned circuit numbers from here.")
        else:
            await rt.coach.say(
                f"Following {prof.display_name} from here, I'll coach to their speeds and heights.")

    async def select_lesson(new_id: str) -> dict:
        """Switch the flying lesson from the curriculum sidebar. Rebuilds every
        lesson-derived object (adapter geometry, engine, scorer, references)
        the same way startup does, and resets the sim clock to the new lesson's
        time of day. Refused while a run is in progress."""
        if new_id == rt.lesson_id:
            return {"ok": True, "lesson_id": rt.lesson_id}
        meta = lesson_meta(rt.catalog, new_id)
        path = HERE / "lessons" / f"{new_id}.yaml"
        if (not meta or not meta.get("available") or meta.get("kind") == "reading"
                or not path.is_file()):
            return {"ok": False, "error": "lesson not available"}
        if rt.started.is_set():
            return {"ok": False, "error": "lesson in progress"}
        if is_familiarization(rt.lesson):
            await rt._set_frozen(False)
        rt.stop_speech()
        rt.coach.pause()
        rt.lesson = load_yaml(path)
        rt.lesson_id = new_id
        if rt.cfg["sim"]["adapter"] == "mock":      # mock flies the lesson's own geometry
            rt.sim = make_adapter(rt.cfg, rt.lesson)
            rt.sim.connect()
        rt.lesson = lesson_for_sim(rt.cfg, rt.sim, rt.lesson)
        phrases.set_lesson_lines(rt.lesson.get("say"))
        rt.engine = make_engine(rt.cfg, rt.lesson, rt.aircraft)
        rt.scorer = make_scorer(rt.lesson, rt.aircraft)
        rt.refs = ReferenceRegistry(rt.lesson, rt.lesson_id)
        rt.recorder.reset()
        rt.event_log.configure(*event_log_setup(rt.lesson, rt.engine))
        rt._publish_events()
        rt.attempts.clear()
        rt.checkpoints.clear()
        rt.scored["done"] = False
        rt._discard_card()
        rt.transcript.clear()
        rt.state.update(
            phase=rt.engine.phase, circuit=1,
            total_circuits=int(rt.lesson.get("total_circuits", 1)),
            track=[[]], scorecard=None, speaking=False, look_at=None, demo=None,
            lesson_id=rt.lesson_id,
            active_lesson=meta,
            reference_track=None,
            lesson=lesson_state(rt.lesson),
            **rt.refs.payload_for_state())
        rt.state["scales"]["alt_max"] = alt_scale_max(rt.lesson)
        _apply_reference("planned")
        await rt._apply_lesson_clock()
        rt.coach.resume()
        log.info("lesson -> %s (%s)", rt.lesson_id, rt.lesson.get("name", rt.lesson_id))
        return {"ok": True, "lesson_id": rt.lesson_id}

    async def configure_session(mode: str, jargon: str, reference: str = "planned",
                                reference_recording: bool = False,
                                trainee_name: str = "",
                                tone: str = "warm", free_flight: bool = False,
                                strictness: str = ""):
        rt.stop_speech()
        rt.coach.pause()
        if is_familiarization(rt.lesson):
            await rt._set_frozen(False)
        mid = normalize_mode(mode)
        policy = policy_for(mid)
        rt.session["mode"] = mid
        rt.session["policy"] = policy
        rt.session["jargon"] = "pilot" if jargon == "pilot" else "layman"
        # Hardcore tone only for Trainee/Pilot + Pilot jargon
        want_drill = (tone == "drill" and rt.session["jargon"] == "pilot"
                      and mid in ("trainee", "pilot"))
        rt.session["tone"] = "drill" if want_drill else "warm"
        rt.session["reference_recording"] = bool(reference_recording)
        rt.session["free_flight"] = bool(free_flight)
        rt.session["name"] = (trainee_name or "").strip()[:48]
        # rule-check strictness chosen for this session; empty = the trainee's, else the mode's
        rt.session["strictness"] = strictness if strictness in ("lenient", "standard", "strict") else None
        tc = int(rt.lesson.get("total_circuits", 1))
        rt.engine.detector.total_circuits = 1 if reference_recording else tc
        rt.state["total_circuits"] = rt.engine.detector.total_circuits
        phrases.configure(rt.flap_style, rt.session["jargon"],
                          guidance=policy.get("guidance", "direct"),
                          tone=rt.session["tone"])
        rt.attempts.clear()
        rt.run_gen["n"] += 1
        rt.scored["done"] = False
        rt._discard_card()
        rt.engine.restart()
        rt.scorer.reset()
        rt.checkpoints.clear()
        rt.state["track"] = [[]]
        rt.transcript.clear()
        rt.started.clear()
        rt.state["lesson_running"] = False
        intro_shown.clear()
        rt.coach.resume()
        _apply_reference(reference or "planned")
        # make the briefing now, while the menu closes and the intro video plays:
        # the first thing MAVIS says then needs no synthesis
        try:
            # only when start() will say the briefing: otherwise it would just hold up the first real line
            if ((is_familiarization(rt.lesson) and rt._demos_wanted())
                    or policy.get("coaching") == "turn_by_turn"):
                rt.prewarm_speech(rt._briefing_texts())
        except Exception as e:                # never let this stop a session starting
            log.info("speech prewarm skipped: %s", e)
        log.info("session configured: mode=%s jargon=%s tone=%s guidance=%s coaching=%s "
                 "reference=%s trainee=%s",
                 rt.session["mode"], rt.session["jargon"], rt.session["tone"],
                 policy.get("guidance"), policy.get("coaching"), rt.session["reference"],
                 rt.session["name"] or "-")

    async def set_reference(ref_id: str):
        prev = rt.session.get("reference", "planned")
        rid = ref_id or "planned"
        _apply_reference(rid)
        if prev != rid and rt.started.is_set() and rt.engine.briefing_done:
            await _announce_reference_change(rt.refs.get(rid))

    intro_shown = asyncio.Event()

    async def intro():
        """Intro video speaks the line; show matching text in chat (no TTS)."""
        if intro_shown.is_set():
            return
        if (rt.session.get("policy") or {}).get("coaching") == "silent":
            intro_shown.set()
            return
        intro_shown.set()
        code = phrases.intro_code(rt.session.get("mode", "visitor"))
        await rt.coach.say(phrases.render(code, {}), speak=False)

    quit_task: dict = {"t": None}   # keeps a reference so the task isn't GC'd

    async def quit_lesson():
        # trainee pressed Back before finishing -> practice summary, NOT posted
        if is_familiarization(rt.lesson):
            await rt._set_frozen(False)        # hand the aircraft back on the way out
        if rt.scored["done"]:
            # the flight already finished on its own and is scored: nothing more to score,
            # but leaving the lesson must still end the run -- otherwise it stays "in
            # progress", the next lesson can't be selected and the page re-enters it
            rt.started.clear()
            rt.state["lesson_running"] = False
            intro_shown.clear()
            return
        rt.scored["done"] = True
        # a Free Flight has no lesson to complete: ending it is how it finishes
        rt.event_log.end_flight(rt._sim_now(), "completed" if rt.session.get("free_flight") else "abandoned")
        rt._publish_events()
        rt.stop_speech()
        rt.coach.pause()
        rt.started.clear()
        rt.state["lesson_running"] = False
        intro_shown.clear()
        rt.transcript.clear()
        # Scoring + the LLM debrief can take a while (or fail); run them in
        # the background so POST /demo/quit returns at once -- the scorecard
        # arrives over the WS state like any other update. A failure here is
        # logged instead of turning the quit itself into an HTTP 500.
        async def _finish():
            try:
                await rt.finish_lesson(practice=True, spoken_close=False)
            except Exception:
                log.exception("finishing the lesson after quit failed")
        quit_task["t"] = asyncio.create_task(_finish())

    rt._apply_reference = _apply_reference
    rt._sync_refs_to_state = _sync_refs_to_state
    rt.configure_session = configure_session
    rt.intro = intro
    rt.mute = mute
    rt.quit_lesson = quit_lesson
    rt.relaunch = relaunch
    rt.relaunch_new = relaunch_new
    rt.restart = restart
    rt.select_lesson = select_lesson
    rt.set_reference = set_reference
    rt.start = start
