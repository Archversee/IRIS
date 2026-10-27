"""How a flight is run around the trainee: briefing, sim clock / night, MSFS
connect + runway orientation, aircraft freeze and control lock, the airborne
start, and the familiarization demonstration (MAVIS flies each step once while
it explains, then hands over).

Every long-running step re-checks rt.run_gen so a Restart / Fly again
mid-sequence makes it stand down."""
from __future__ import annotations
import asyncio
import logging
import math
import time

from adapters.sim_base import Telemetry
from coach import phrases
from core.demo import DEMO_LONG_STEPS, DESCRIBE_ONLY, NO_DEMO, DemoPilot
from core.familiarization import STEP_TITLES, is_familiarization, runway_start, start_position
from core.reference_profiles import ReferenceRegistry
from runtime.bootstrap import lesson_state, make_engine, make_scorer

log = logging.getLogger("main")     # same logger name as before the move: log lines unchanged


def build_flight_flow(rt):
    """Defines the flight-sequence helpers and publishes the ones the
    session actions and the tick loop call on rt."""
    def _lesson_text(block: dict | None) -> str:
        """Lesson-authored line in the session's jargon ({layman, pilot} variants)."""
        if not block:
            return ""
        return block.get(rt.session.get("jargon", "layman")) or block.get("layman") or ""

    def _rolling() -> bool:
        """The trainee has started the take-off (or is already flying)."""
        tel = rt.state.get("tel") or {}
        if not tel:
            return False
        if not tel.get("on_ground", True):
            return True
        # actually moving -- not just the throttle forward (parking brake on)
        gs = tel.get("gs")
        return (gs if gs is not None else tel.get("ias", 0.0)) > 8

    async def _brief(text: str) -> bool:
        """Speak one briefing line; False (and the line cut off) as soon as
        the trainee starts the take-off -- the lesson then starts at once."""
        if not text:
            return True
        line = asyncio.create_task(rt.coach.say(text))
        while not line.done():
            if _rolling():
                line.cancel()                  # waiting to speak, or speaking: either way, not any more
                rt.stop_speech()
                try:
                    await line
                except asyncio.CancelledError:
                    pass
                log.info("briefing cut short: the take-off has started")
                return False
            await asyncio.sleep(0.2)
        return True

    def _briefing_texts() -> list[str]:
        """What the briefing will say, in order (also made ahead by rt.prewarm_speech)."""
        own = _lesson_text(rt.lesson.get("briefing"))
        if is_familiarization(rt.lesson):
            alt = (rt.lesson.get("airborne_start") or {}).get("alt_msl_ft", 0)
            return [own.replace("{alt}", f"{alt:,}")]
        if rt.session.get("reference_recording"):
            out = [phrases.render("BRIEFING_1_REF", {})]
        else:
            out = [own or phrases.render("BRIEFING_1", {})]
        if not rt.lesson.get("segment"):        # whole-circuit wording; Take-off/Landing have their own
            out.append(phrases.render("BRIEFING_2", {}))
        return [t for t in out if t]

    async def _briefing_lines():
        texts = _briefing_texts()
        if is_familiarization(rt.lesson):
            for t in texts:
                await rt.coach.say(t)
            rt.engine.start_flying()
            return
        # ground starts: if the trainee rolls during the briefing, the rest of
        # it is skipped and the lesson picks up where the aircraft is
        # (not the mock: its autopilot starts rolling straight away)
        brief = (rt.coach.say if rt.lesson.get("airborne_start") or rt.cfg["sim"]["adapter"] == "mock"
                 else _brief)
        for t in texts:
            if await brief(t) is False:
                break
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
        if not await rt.loop.run_in_executor(None, rt.sim.set_local_time, h, m):
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
                await rt.loop.run_in_executor(None, rt.sim.connect)
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
        rt.engine = make_engine(rt.cfg, rt.lesson, rt.aircraft)
        rt.engine.detector.total_circuits = total
        rt.scorer = make_scorer(rt.lesson, rt.aircraft)
        rt.refs = ReferenceRegistry(rt.lesson, rt.lesson_id)
        rt.state["lesson"] = lesson_state(rt.lesson)
        rt.state.update(**rt.refs.payload_for_state())
        rt._apply_reference(rt.session.get("reference", "planned"))
        log.info("aircraft is lined up on runway %s (heading %.0f): lesson geometry reversed",
                 rt.lesson["runway"]["ident"], tel.hdg)

    def _fam_freezes() -> bool:
        """Familiarization holds the aircraft still while it explains -- not in
        silent (Exam) coaching, where nothing is explained."""
        pol = rt.session.get("policy") or {}
        return (is_familiarization(rt.lesson) and bool(rt.lesson.get("freeze_during_instructions"))
                and pol.get("coaching") != "silent")

    async def _set_frozen(on: bool) -> None:
        fn = getattr(rt.sim, "set_frozen", None)
        if fn and not await rt.loop.run_in_executor(None, fn, on):
            log.warning("sim did not confirm freeze=%s", on)

    _speakable = phrases.speakable      # to-do text as MAVIS says it

    def _demos_wanted() -> bool:
        """Fly the whole airborne lesson once as a demonstration before the
        trainee's turn? Not in Exam (silent) coaching, not when the lesson
        says `demos: false`, not on a sim that can't be flown remotely."""
        pol = rt.session.get("policy") or {}
        return (bool(rt.lesson.get("demos", True)) and pol.get("coaching") != "silent"
                and hasattr(rt.sim, "demo_control"))

    async def _fam_instruct(ev):
        """Speak one familiarization step with the aircraft frozen, then hand
        over: "You have control", unfreeze, and start the step's clock."""
        gen, eng = rt.run_gen["n"], rt.engine
        phase = ev.data.get("phase")
        try:
            await _set_frozen(True)
            await rt.coach.say(phrases.render("LESSON_NOTE", ev.data), phase=phase)
            if gen == rt.run_gen["n"] and rt.started.is_set():
                await rt.coach.say(phrases.render("FAM_YOU_HAVE_CONTROL", {}), phase=phase)
        finally:
            if gen == rt.run_gen["n"] and eng is rt.engine:
                await _set_frozen(False)
                eng.release()
                if hasattr(rt.sim, "comply"):     # mock student flies the step from here
                    rt.sim.comply(f"PHASE_ENTER_{phase}", ev.data)

    async def _lock_controls(on: bool) -> None:
        """Trainee's stick/pedals/throttle/trim locked out while MAVIS flies
        the demo, handed back after."""
        fn = getattr(rt.sim, "lock_controls", None)
        if fn and not await rt.loop.run_in_executor(None, fn, on):
            log.warning("sim did not take lock_controls(%s): the trainee's controls "
                        "may still move the aircraft during the demo", on)

    async def _fly_demo(pilot: DemoPilot, gen: int):
        """Feed the demo pilot live telemetry and send its commands to the sim."""
        last = time.time()
        while not pilot.done and gen == rt.run_gen["n"]:
            now = time.time()
            cmd = pilot.tick(rt.state.get("tel") or {}, now - last)
            last = now
            if cmd:
                await rt.loop.run_in_executor(None, rt.sim.demo_control, cmd)
            await asyncio.sleep(0.2)

    async def _fam_opening(briefing: bool):
        """Start of an airborne lesson: the briefing (turn-by-turn modes), the
        whole lesson flown once as a demonstration, then the hand-over, so the
        trainee then flies every step in one go."""
        gen = rt.run_gen["n"]
        try:
            if briefing:
                alt = (rt.lesson.get("airborne_start") or {}).get("alt_msl_ft", 0)
                await rt.coach.say(_lesson_text(rt.lesson.get("briefing")).replace("{alt}", f"{alt:,}"))
            if gen != rt.run_gen["n"]:
                return
            await _fam_demo_all(gen)
            if gen != rt.run_gen["n"]:
                return
            # the demo's closing line ("Your turn now, same steps.") is the
            # hand-over: no separate "You have control" after it.
            # Controls stay locked from the demo's start until then.
            await _lock_controls(False)
            if rt.demo_ctl["shown"]:
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
        tel0 = (await rt.loop.run_in_executor(None, rt.sim.get_telemetry)).as_dict()
        steps = [s for s in rt.engine.detector.ORDER[1:-1]
                 if s not in NO_DEMO and (rt.engine.notes.get(s) or {}).get("demo", True)]
        if not steps or not tel0 or tel0.get("on_ground", True):
            return
        mock = rt.cfg["sim"]["adapter"] == "mock"
        base_th = tel0.get("throttle", 0.7)
        # the whole demo in about two minutes (lesson demo_max_s): each step gets
        # an equal share of the flying time, less a little for the opening and
        # closing lines
        # steps that change height need longer to show than the steady ones
        weight = {s: (3.0 if s in DEMO_LONG_STEPS else 1.0) for s in steps}
        unit = (float(rt.lesson.get("demo_max_s", 120)) - 10.0) / sum(weight.values())
        rt.demo_ctl["skip"].clear()
        rt.state["demo"] = {"n": 0, "of": len(steps), "title": "Getting ready"}
        flew = skipped = False
        try:
            await rt.coach.say(phrases.render("FAM_DEMO_START", {}))
            if rt.demo_ctl["skip"].is_set() or gen != rt.run_gen["n"]:
                return
            await _lock_controls(True)          # hands off: MAVIS has control
            await _set_frozen(False)
            flew = True
            # MAVIS flies this stretch: marked as its own event so it is never mistaken for
            # (or graded as) the trainee's flying
            rt.event_log.observe("DEMO", rt._sim_now(), kind="demo")
            rt._publish_events()
            for i, step in enumerate(steps):
                tel = dict(rt.state.get("tel") or tel0)
                data = rt.engine.preview(step, Telemetry(**tel), demo=True)
                if data.get("demo_layman"):
                    # a step the autopilot can't fly: its explain-only lines
                    data = {**data, "layman": data["demo_layman"],
                            "pilot": data.get("demo_pilot") or data["demo_layman"]}
                rt.state["demo"] = {"n": i + 1, "of": len(steps),
                                 "title": data.get("title") or STEP_TITLES.get(step, step),
                                 "describe": step in DESCRIBE_ONLY}
                rt.state["look_at"] = {"items": data.get("look") or [], "phase": step, "seq": time.time()}
                pilot = DemoPilot(step, rt.engine, tel, mock=mock, base_throttle=base_th,
                                  max_s=max(4.0, unit * weight[step]))
                speak = asyncio.create_task(rt.coach.say(phrases.render("LESSON_NOTE", data), phase=step))
                fly = asyncio.create_task(_fly_demo(pilot, gen))
                skip_w = asyncio.create_task(rt.demo_ctl["skip"].wait())
                await asyncio.wait({fly, skip_w}, return_when=asyncio.FIRST_COMPLETED)
                if not rt.demo_ctl["skip"].is_set():
                    # flown: let the explanation finish (the autopilot holds it)
                    await asyncio.wait({speak, skip_w}, return_when=asyncio.FIRST_COMPLETED)
                skipped = rt.demo_ctl["skip"].is_set() or gen != rt.run_gen["n"]
                for t in (fly, skip_w) + ((speak,) if skipped else ()):
                    t.cancel()
                if skipped:
                    rt.stop_speech()
                    break
                if pilot.rescued:
                    log.warning("demo: %s abandoned near the stall (%.0f kt) -- recovered",
                                step, (rt.state.get("tel") or {}).get("ias", 0))
                rt.demo_ctl["shown"].add(step)
            skipped = skipped or rt.demo_ctl["skip"].is_set()
        finally:
            if flew and gen == rt.run_gen["n"]:
                rt.event_log.close(rt._sim_now(), "skipped" if (skipped or rt.demo_ctl["skip"].is_set()) else "completed")
                rt._publish_events()
            await rt.loop.run_in_executor(None, rt.sim.demo_control, {"ap": False, "rudder": 0.0})
            rt.state["demo"] = None
            rt.state["look_at"] = None
            if flew and gen == rt.run_gen["n"]:
                await _set_frozen(True)
                ok = await rt.loop.run_in_executor(None, lambda: rt.sim.set_position(
                    tel0["lat"], tel0["lon"], tel0["alt_msl"], tel0["hdg"], max(60.0, tel0["ias"]),
                    throttle_pct=base_th * 100.0))
                if not ok:
                    log.warning("demo: couldn't put the aircraft back at the start")
                rt.state["track"].append([])      # the trainee's own flight starts a fresh line
        if gen == rt.run_gen["n"] and rt.demo_ctl["shown"]:
            await rt.coach.say(phrases.render("FAM_DEMO_SKIPPED" if skipped else "FAM_DEMO_END", {}))

    async def _fam_finish():
        """End of the exercise: hold the aircraft while it's scored, then put
        it back at the start of the runway, stopped, ready for whatever's next."""
        gen = rt.run_gen["n"]
        await _set_frozen(True)
        try:
            await rt.finish_lesson(False)
        except Exception:
            log.exception("finishing the familiarization lesson failed")
        if gen != rt.run_gen["n"]:
            return                      # Fly again / reset already took over
        await _set_frozen(False)
        await _to_runway_start()

    async def _to_runway_start() -> bool:
        """Aircraft lined up at the start of the lesson's runway, stopped,
        throttle idle."""
        fn = getattr(rt.sim, "set_position", None)
        lat, lon, hdg = runway_start(rt.lesson)
        elev = rt.lesson["airport"]["elevation_ft"]
        ok = fn is not None and await rt.loop.run_in_executor(
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
        ok = fn is not None and await rt.loop.run_in_executor(
            None, lambda: fn(lat, lon, a["alt_msl_ft"], a["heading_true"],
                             a["ias_kt"], throttle_pct=a.get("throttle_pct")))
        if not ok:
            raise RuntimeError("Couldn't place the aircraft in the air for the "
                               "familiarization flight -- check MAVIS's log, then press Start again.")
        # wait for telemetry to show the new position, so the first exercise
        # snapshots the airborne altitude, not the ramp
        deadline = time.time() + 6.0
        while time.time() < deadline:
            tel = await rt.loop.run_in_executor(None, rt.sim.get_telemetry)
            if not tel.on_ground and tel.alt_agl > 500:
                return
            await asyncio.sleep(0.2)
        log.warning("airborne start: telemetry still not airborne after 6 s, continuing")

    async def night_sequence():
        if not rt.lesson.get("night_event"):
            return
        rt.engine.mark_night_done()
        silent = (rt.session.get("policy") or {}).get("coaching") == "silent"
        if not silent:
            await rt.coach.say(phrases.render("NIGHT_ANNOUNCE", {}))
        h, m = map(int, rt.lesson["night_event"]["local_time"].split(":"))
        if not await rt.loop.run_in_executor(None, rt.sim.set_local_time, h, m):
            log.error("sim rejected time change - use fallback keybind")
        await asyncio.sleep(2)
        if not silent:
            await rt.coach.say(phrases.render("NIGHT_AFTER", {}))

    async def skip_demo():
        rt.demo_ctl["skip"].set()

    rt._airborne_start = _airborne_start
    rt._apply_lesson_clock = _apply_lesson_clock
    rt._briefing_lines = _briefing_lines
    rt._briefing_texts = _briefing_texts
    rt._connect_msfs_with_retries = _connect_msfs_with_retries
    rt._demos_wanted = _demos_wanted
    rt._fam_finish = _fam_finish
    rt._fam_freezes = _fam_freezes
    rt._fam_instruct = _fam_instruct
    rt._fam_opening = _fam_opening
    rt._lesson_text = _lesson_text
    rt._orient_lesson_to_aircraft = _orient_lesson_to_aircraft
    rt._set_frozen = _set_frozen
    rt._speakable = _speakable
    rt._to_runway_start = _to_runway_start
    rt.night_sequence = night_sequence
    rt.skip_demo = skip_demo
