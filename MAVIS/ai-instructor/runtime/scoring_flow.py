"""End of a run: the scorecard, the spoken debrief and the leaderboard (spec §14).

Every LLM debrief has a deterministic fallback line; finish_lesson() is the
single owner of the spoken close (the tick loop never submits
PHASE_ENTER_DEBRIEF itself)."""
from __future__ import annotations
import asyncio
import logging
import re
import time
from pathlib import Path

from coach import phrases
from coach.llm_coach import clip_spoken
from core.familiarization import is_familiarization
from core.modes import policy_for, allow_coach_event
from runtime.bootstrap import HERE

log = logging.getLogger("main")     # same logger name as before the move: log lines unchanged


def build_scoring_flow(rt):
    """Defines finish_lesson() and the scorecard / debrief / leaderboard helpers
    and publishes the ones other code calls on rt."""
    # ---------- lesson scoring + leaderboard (spec §14) ----------
    def _card_state(card, posted, debrief_pending: bool = False):
        ref_id = rt.session.get("reference", "planned")
        prof = rt.refs.get(ref_id)
        return {"seq": rt.score_seq["n"], "name": card.name, "mode": card.mode,
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
        if not rt.coach.llm_ok:
            return fallback
        who = (rt.session.get("name") or "").strip()
        who_line = (f"The trainee's name is {who}. Use that name at most once in the whole debrief."
                    if who else "Address the trainee only as 'you'. Do not use any proper name.")
        what = rt.lesson.get("debrief_summary") or "circuit"
        sysp = (phrases.PERSONA + f"\nWrite a spoken debrief of the trainee's {what} from "
                "these 0-100 scores: two short sentences, 30 words at most. Name one strength "
                "and one thing to work on. No lists; don't read out the numbers. " + who_line)
        try:
            out = await rt.coach._ollama(
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
        if not rt.coach.llm_ok:
            return fallback
        who = (rt.session.get("name") or "").strip()
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
            out = await rt.coach._ollama(
                sysp, f"scores={sc} finished_lesson={reached} total={card.total} grade={card.grade}", 10.0)
            out = (out or "").strip()
            if 10 < len(out) < 400:
                return _sanitize_debrief(out, who)
        except Exception:
            pass
        return _sanitize_debrief(fallback, who)

    def _debrief_spoken() -> bool:
        return any(t.get("phase") == "DEBRIEF" and t.get("who") == "instructor"
                   for t in rt.transcript)

    def _coach_active() -> bool:
        return bool(rt.coach.queue.qsize() or rt.state.get("speaking") or rt.coach.busy)

    async def _await_spoken_close():
        """Let the landing callout finish, then the debrief line, then a short beat."""
        pol = rt.session.get("policy") or policy_for(rt.session.get("mode", "visitor"))
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
            own = rt._lesson_text(rt.lesson.get("closing"))    # e.g. Take-off / Landing's own close
            await rt.coach.say(own or phrases.render(code, {
                "phase": "DEBRIEF", "circuit": rt.engine.circuit}), phase="DEBRIEF")
        # Phase 3, wait until TTS actually finishes
        while time.time() < deadline and _coach_active():
            await asyncio.sleep(0.1)
        await asyncio.sleep(2.0)

    async def finish_lesson(practice: bool = False, spoken_close: bool = True):
        gen = rt.run_gen["n"]
        if spoken_close:
            await _await_spoken_close()
        await _halt_lesson()
        pol = rt.session.get("policy") or policy_for(rt.session.get("mode", "visitor"))
        if pol.get("auto_practice_summary"):
            practice = True
        if hasattr(rt.scorer, "skipped"):         # familiarization: timed-out steps cost points
            rt.scorer.skipped = set(getattr(rt.engine, "skipped", ()))
        card = rt.scorer.finalize(
            rt.session.get("name", ""), rt.session["mode"], rt.lesson_id, practice,
            score_caps=bool(pol.get("score_caps")))
        # §15 groundwork: hold this run's recording (for reference re-score later) + save last run
        rt.pending["recording"] = rt.recorder.to_dict(meta={
            "lesson": rt.lesson_id, "aircraft": Path(rt.cfg["aircraft_profile"]).stem,
            "mode": rt.session["mode"], "reference": rt.session.get("reference", "planned"),
            "practice": practice, "name": rt.session.get("name", ""),
            "events": rt.event_log.to_list()})
        try:
            rt.recorder.save(HERE / "data" / "recordings" / "last_run.json")
        except Exception as e:
            log.debug("recording save failed: %s", e)
        rt.pending["card"], rt.pending["posted"] = card, False
        await rt._finish_rule_checks()
        rt.score_seq["n"] += 1
        if gen != rt.run_gen["n"]:
            return
        rt.state["scorecard"] = _card_state(card, posted=False, debrief_pending=True)
        card.debrief = await _make_debrief(card)
        if gen != rt.run_gen["n"]:
            return
        rt.state["scorecard"] = _card_state(card, posted=rt.pending["posted"], debrief_pending=False)

    async def _halt_lesson():
        """Freeze coaching + phase progression once a run is over or abandoned."""
        rt.stop_speech()
        rt.coach.pause()
        rt.engine.briefing_done = False

    def _post_card(name: str = "", given: str = "", family: str = "") -> None:
        """Post the held scorecard once; with no name given or set up, as Anonymous."""
        card = rt.pending.get("card")
        if not card or card.practice or rt.pending["posted"]:
            return
        # unnamed (finished without answering): the trainee name the session was set up with
        card.name = (name or rt.session.get("name") or "").strip()[:24] or "Anonymous"
        card.given = (given or "").strip()[:32]
        card.family = (family or "").strip()[:32]
        card.aircraft = rt.aircraft.get("name", "")
        card.session_id = str(rt.state.get("session_id") or "")
        card.user_id = rt.session.get("pilot_user_id") or ""
        rt.board.add(card.to_entry())
        rt.pending["posted"] = True
        rt.state["leaderboard"] = rt.board.snapshot()
        rt.state["leaderboard_posts"] = rt.board.posts()
        rt.state["scorecard"] = _card_state(card, posted=True)

    def _discard_card() -> None:
        """Every completed run reaches the board: a card the trainee never
        answered for is posted as Anonymous before it is cleared."""
        _post_card()
        rt.state["scorecard"] = None
        rt.pending["card"] = None
        rt.pending["posted"] = False

    async def score_submit(name: str, given: str = "", family: str = ""):
        _post_card(name, given, family)

    async def score_drop():
        """The trainee threw the run away (IRIS's Finish & discard): clear the
        held scorecard WITHOUT posting it -- _discard_card would post it."""
        rt.state["scorecard"] = None
        rt.pending["card"] = None
        rt.pending["posted"] = False

    rt._discard_card = _discard_card
    rt.finish_lesson = finish_lesson
    rt.score_submit = score_submit
    rt.score_drop = score_drop
