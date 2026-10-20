"""The tick loop's steps (main.py ticker()): plain synchronous functions the
loop calls in a fixed order every tick -- no awaits, no callbacks. Background
work (forced repeats, the familiarization hold, finishing a lesson) is only
started here with asyncio.create_task, exactly where it always was.

Deterministic by design: phase detection and the monitors run in
rt.engine.tick(), in the loop itself; these steps route what it returns."""
from __future__ import annotations
import asyncio
import logging
import time

from coach import phrases
from core.events import CoachEvent
from core.familiarization import PART_CUES, is_familiarization
from core.modes import allow_coach_event
from runtime.checkpoints import GATED, MAX_ATTEMPTS

log = logging.getLogger("main")     # same logger name as before the move: log lines unchanged

# Live ground track size cap (points per attempt, ~1/s). Past TRACK_MAX the
# older part is thinned 2:1 while the newest TRACK_KEEP_FULL stay full-rate.
TRACK_MAX = 3000
TRACK_KEEP_FULL = 1000


def _jump(a, b):   # crude position delta in degrees (~0.01 deg = 1.1 km)
    return abs(a.lat - b.lat) + abs(a.lon - b.lon)


def force_redo_due(rt, pol, prev_phase, tel) -> bool:
    # trainee/pilot: if a graded leg just ended below tolerance, send
    # them back to re-fly it (bounded) instead of progressing. Never in Free
    # Flight: there is no set track to be held to, and MAVIS must not move
    # the aircraft on its own there.
    return (pol.get("force_repeat")
              and not rt.session.get("free_flight")
              and not rt.session.get("reference_recording")
              and rt.engine.phase != prev_phase
              and prev_phase in GATED
              and not rt.phase_passed(prev_phase, tel)
              and rt.attempts.get(prev_phase, 0) < MAX_ATTEMPTS)


def dispatch_events(rt, pol, events, tel, force_redo) -> None:
    """Route this tick's events: mode filter, familiarization cue / hold, then
    coach.submit, the mock student, and a checkpoint at each PHASE_ENTER_."""
    for ev in events:
        if force_redo and ev.code.startswith("PHASE_ENTER_"):
            continue   # don't announce the new leg; we're reverting
        if rt.session.get("free_flight") and ev.severity != "safety":
            # Free Flight has no lesson to coach: the circuit detector of the
            # lesson loaded underneath still runs (events, scoring), but none
            # of its calls -- turns, "go around", "rotate" -- are spoken
            continue
        if ev.code == "PHASE_ENTER_DEBRIEF":
            continue   # finish_lesson/_await_spoken_close owns this line
        if ev.code.startswith("PHASE_ENTER_"):
            # the trainee may be ahead of MAVIS: what's left of the old leg goes
            rt.coach.new_phase(ev.data.get("phase"))
        if (not allow_coach_event(pol, ev.code, ev.severity)
                and ev.code not in (rt.lesson.get("always_say") or [])
                # "now come back" calls: every mode but Exam (silent)
                and not (ev.code in PART_CUES and pol.get("coaching") != "silent")):
            continue
        if ev.code == "LESSON_NOTE" and is_familiarization(rt.lesson):
            rt.state["look_at"] = {"items": ev.data.get("look") or [],
                                "phase": ev.data.get("phase"), "seq": time.time()}
            if ev.data.get("phase") in rt.demo_ctl["shown"]:
                # watched in the demo: a short cue, no hold -- keep the momentum
                title = ev.data.get("title") or ev.data.get("phase")
                action = (ev.data.get("todo") or [""])[0]
                try:
                    action = action.format(**ev.data)    # "Bank left {med_bank_lo}-..."
                except (KeyError, IndexError, ValueError):
                    pass
                cue = phrases.render("FAM_STEP_CUE", {
                    "title": title, "title_lc": title[:1].lower() + title[1:],
                    "action": rt._speakable(action).rstrip(".")})
                ev = CoachEvent("LESSON_NOTE", ev.severity,
                                {**ev.data, "layman": cue, "pilot": cue}, cooldown_s=0)
            elif rt._fam_freezes() and ev.data.get("freeze", True):
                rt.engine.hold()       # judge nothing until "you have control"
                asyncio.create_task(rt._fam_instruct(ev))
                continue
            if hasattr(rt.sim, "comply"):
                # mock student: fly the step even when the mode
                # filters out its PHASE_ENTER announcement
                rt.sim.comply(f"PHASE_ENTER_{ev.data.get('phase')}", ev.data)
        # every call belongs to the leg / step it was made in (coach.new_phase),
        # except the end-of-turn call: the next leg often starts while it's
        # being said, and it must never be dropped as stale
        if ev.code != "ROLL_OUT_LEG":
            ev.data.setdefault("phase", rt.engine.phase)
        rt.coach.submit(ev)
        if hasattr(rt.sim, "comply"):   # mock student obeys callouts
            rt.sim.comply(ev.code, ev.data)
        if ev.code.startswith("PHASE_ENTER_"):   # checkpoint each phase
            rt.checkpoints[ev.data["phase"]] = {
                "tel": tel.as_dict(), "circuit": rt.engine.circuit,
                "blob": rt.sim.save_state() if hasattr(rt.sim, "save_state") else None}


def score_tick(rt, tel, ctrl, prev_on_ground):
    """Scoring: sample the tick, capture touchdowns, finalize at debrief.
    Returns prev_on_ground for the next tick."""
    if rt.engine.briefing_done:
        geom_d = ({"along": rt.engine.geom.along, "cross": rt.engine.geom.cross}
                  if rt.engine.geom else None)
        if not getattr(rt.engine, "held", False):   # frozen for an explanation: not flying
            rt.scorer.sample(tel.as_dict(), geom_d, ctrl.as_dict(), rt.engine.phase)
        rt.recorder.sample(tel.as_dict(), ctrl.as_dict(), geom_d, rt.engine.phase)
        if not getattr(rt.engine, "held", False):
            try:
                rt._check_rules_live(tel)
            except Exception:       # a rule check must never stop the lesson
                log.exception("live rule check failed -- rule checks off for this run")
                rt.live_rules["ev"] = None
        # the to-do list / phase tracker decides event boundaries: when it
        # moves on, the previous event ends and the next starts, at this tick
        if rt.session.get("free_flight"):
            rt.free_tracker.tick(tel)
        changed = rt.event_log.observe(rt._flight_phase(), tel.t, rt.engine.circuit,
                                    getattr(rt.engine, "skipped", ()),
                                    kind="stall" if rt._flight_phase() == "STALL" else None)
        if rt.engine.phase == "DEBRIEF" and not rt.scored["done"]:
            rt.event_log.end_flight(tel.t, "completed")
            changed = True
        if changed:
            rt._publish_events()
        if prev_on_ground is False and tel.on_ground \
                and rt.engine.phase in ("FLARE_TOUCHDOWN", "FINAL", "ROLLOUT"):
            rt.scorer.on_landing(tel.as_dict(), geom_d)
        prev_on_ground = tel.on_ground
        if rt.engine.phase == "DEBRIEF" and not rt.scored["done"]:
            rt.scored["done"] = True
            if is_familiarization(rt.lesson) or rt.lesson.get("end_reposition"):
                # practice over: freeze, score, then back to the runway
                # (airborne lessons, and the take-off lesson)
                asyncio.create_task(rt._fam_finish())
            else:
                asyncio.create_task(rt.finish_lesson(False))
    return prev_on_ground


def publish_state(rt, tel, ctrl) -> None:
    rt.state.update(
        tel=tel.as_dict(), ctrl=ctrl.as_dict(), phase=rt._flight_phase(),
        circuit=rt.engine.circuit, total_circuits=rt.engine.detector.total_circuits,
        targets=rt.engine.targets(),
        night=not (6.0 < tel.local_time_h < 18.5),
        transcript=rt.transcript[-60:], llm_ok=rt.coach.llm_ok,
        fam_todo=rt.engine.todo() if rt.started.is_set() and hasattr(rt.engine, "todo") else None,
        geom={"along": rt.engine.geom.along, "cross": rt.engine.geom.cross}
             if rt.engine.geom else None)


def append_track(rt, tel, t0, spike, last_track):
    """Add this tick to the live ground track. Returns last_track."""
    # Ground track keeps the whole flight (taxi, take-off roll and
    # rollout included), like IRIS's post-flight ground track. Long
    # flights are thinned rather than trimmed: past TRACK_MAX points
    # the older part is halved, so the full path stays on the map
    # (just coarser at the start) and the payload stays bounded.
    if not rt.scored["done"] and t0 - last_track > 1.0 and not spike \
            and abs(tel.lat) > 0.01 and rt.state.get("demo"):
        # MAVIS flying the demonstration: its own line (dotted on the map),
        # never part of the trainee's track
        demo = rt.state.setdefault("demo_track", [])
        if not demo or abs(tel.lat - demo[-1][0]) + abs(tel.lon - demo[-1][1]) > 2e-5:
            demo.append([tel.lat, tel.lon])
            if len(demo) > TRACK_MAX:
                demo[:] = demo[:-TRACK_KEEP_FULL:2] + demo[-TRACK_KEEP_FULL:]
        return t0
    if not rt.scored["done"] and t0 - last_track > 1.0 and not spike \
            and abs(tel.lat) > 0.01:
        seg = rt.state["track"][-1]
        d = (abs(tel.lat - seg[-1][0]) + abs(tel.lon - seg[-1][1])) if seg else 1.0
        if d >= 0.02:     # real reposition (> ~2 km/sample): new segment, no false line across the jump
            if seg:
                rt.state["track"].append([])
                seg = rt.state["track"][-1]
            seg.append([tel.lat, tel.lon])
            last_track = t0
        elif d > 2e-5:    # skip samples while parked (< ~2 m moved)
            seg.append([tel.lat, tel.lon])
            if len(seg) > TRACK_MAX:
                seg[:] = seg[:-TRACK_KEEP_FULL:2] + seg[-TRACK_KEEP_FULL:]
            last_track = t0
    return last_track
