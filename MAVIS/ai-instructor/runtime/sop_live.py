"""SOP / handbook rules checked live during a flight.

The SOP for the aircraft actually being flown is resolved at every lesson
start (sop/runtime.py) and its rules run through the same evaluator IRIS's
Review re-runs post-flight (sop/evaluate.py), so the two never disagree.
The tick loop calls rt._check_rules_live(tel) once per tick."""
from __future__ import annotations
import logging
import time
import uuid

from core.events import CoachEvent

log = logging.getLogger("main")     # same logger name as before the move: log lines unchanged


def build_sop_live(rt):
    """Sets up the SOP runtime and the live rule checks (at startup, in the
    same order as before) and publishes their actions on rt."""
    # SOP for the aircraft actually being flown (sop/runtime.py): resolved at
    # every lesson start from the sim's aircraft; rules kept in sop_rt.active.
    try:
        from sop.runtime import SopRuntime
        sop_rt = SopRuntime(rt.cfg, rt.cfg["aircraft_profile"])
    except ImportError as e:
        log.info("SOP rules disabled (%s)", e)
        sop_rt = None
    rt.state["sop"] = None

    async def _load_sop():
        if sop_rt is None:
            rt.state["sop_live"] = _rules_off("SOP rules are disabled in MAVIS (see the startup log).")
            return
        ident = None
        if hasattr(rt.sim, "aircraft_ident"):
            try:
                ident = await rt.loop.run_in_executor(None, rt.sim.aircraft_ident)
            except Exception as e:
                log.info("aircraft ident failed: %s", e)
        rt.state["sop"] = await sop_rt.load(ident)
        log.info("SOP for this flight: %s", rt.state["sop"])
        try:
            await _start_rule_checks()
        except Exception as e:          # runs as a background task: never fail silently
            log.exception("starting live rule checks failed")
            live_rules["ev"] = None
            rt.state["sop_live"] = _rules_off(f"Rule checks failed to start: {e}")

    # SOP / handbook rules checked live (sop/evaluate.py) -- the same engine
    # IRIS's Review re-runs post-flight, so the two never disagree. Cautions
    # and advisories are shown; a broken WARNING rule is also spoken
    # (not in exam mode, at most once per rule every RULE_REPEAT_S).
    RULE_REPEAT_S = 30.0
    live_rules: dict = {"ev": None, "ctx": None, "spoken": {}}
    rt.state["sop_live"] = None
    try:
        from sop.evaluate import RuleEvaluator, sample_from_telemetry, summarize
        from sop.results import ResultStore, aircraft_note, snapshot
        from sop import check_settings
        rule_results = ResultStore(sop_rt.store, rt.cfg) if sop_rt is not None else None
    except ImportError as e:
        log.info("live rule checks disabled (%s)", e)
        rule_results = None

    def _rules_disabled() -> dict:
        """Switched off on purpose (SOP Rules page): the live views show nothing."""
        log.info("live rule checks switched off for circuit flights (SOP Rules page)")
        return {"status": "disabled", "reason": "Rule checks are switched off for circuit flights.",
                "n_rules": 0, "active": [], "recent": [], "n_findings": 0, "summary": None}

    def _rules_off(reason: str) -> dict:
        """state["sop_live"] when nothing is being checked: the live views say why."""
        log.warning("live rule checks off: %s", reason)
        return {"status": "off", "reason": reason, "n_rules": 0, "active": [], "recent": [],
                "n_findings": 0, "summary": None}

    def _why_no_rules() -> str:
        sop = rt.state.get("sop") or {}
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
        rt.state["sop_live"] = None
        if rule_results is None:
            rt.state["sop_live"] = _rules_off("Rule checks are disabled in MAVIS (needs asyncpg; see the startup log).")
            return
        # an instructor can switch checks off for circuit flights; Free Flight is always checked
        if not check_settings.checks_on_for(rt.cfg, bool(rt.session.get("free_flight"))):
            rt.state["sop_live"] = _rules_disabled()
            return
        act = sop_rt.active if sop_rt is not None else None
        if not act or not act.get("rules"):
            rt.state["sop_live"] = _rules_off(_why_no_rules())
            return
        strictness, why = await rule_results.strictness_for(
            rt.session.get("name"), rt.session.get("mode"), rt.session.get("strictness"))
        rules = act["rules"]
        live_rules["ctx"] = {
            "aircraft_profile": (rt.state.get("sop") or {}).get("aircraft_id"), "lesson_id": rt.lesson_id,
            "lesson_type": (rt.state.get("sop") or {}).get("lesson_type"),
            "elevation_ft": (rt.lesson.get("airport") or {}).get("elevation_ft"),
            "rule_set_ids": [x["rule_set_id"] for x in act.get("layers") or []], "rules": snapshot(rules),
            "aircraft_note": aircraft_note(rt.state.get("sop")), "strictness": strictness, "why": why}
        live_rules["ev"] = RuleEvaluator(rules, strictness)
        rt.state["sop_live"] = {"status": "checking", "strictness": strictness, "strictness_why": why,
                             "aircraft_note": live_rules["ctx"]["aircraft_note"],
                             "n_rules": len(rules),
                             "active": [], "recent": [], "n_findings": 0, "summary": None}
        log.info("checking %d rule(s) live, %s (%s)", len(rules), strictness, why)

    def _check_rules_live(tel) -> None:
        ev = live_rules["ev"]
        sl = rt.state.get("sop_live")
        if ev is None or sl is None:
            return
        found = ev.step(sample_from_telemetry(tel.as_dict(), rt._flight_phase()))
        sl["active"] = ev.active()
        if not found:
            return
        pol = rt.session.get("policy") or {}
        now = time.time()
        for f in found:
            sl["n_findings"] += 1
            sl["recent"] = ([f] + sl["recent"])[:8]
            if f["severity"] != "warning" or pol.get("coaching") == "silent":
                continue
            if now - live_rules["spoken"].get(f["rule_id"], 0.0) < RULE_REPEAT_S:
                continue
            live_rules["spoken"][f["rule_id"]] = now
            rt.coach.submit(CoachEvent("SOP_RULE", severity="safety", data={**f, "phase": rt._flight_phase()}))

    async def _finish_rule_checks() -> None:
        """End of a run: the live results go to the state (scorecard view) and,
        for an IRIS session, into flight_evaluations (Review re-checks from
        flight_data whenever asked)."""
        ev, ctx = live_rules["ev"], live_rules["ctx"]
        live_rules["ev"] = None
        if ev is None or ctx is None:
            return
        res = ev.results()
        if rt.state.get("sop_live") is not None:
            rt.state["sop_live"].update(active=[], summary=summarize(res))
        sid = rt.session.get("session_id")
        if not sid or rule_results is None:
            return
        try:
            await rule_results.save(uuid.UUID(sid), source="live", strictness=ctx["strictness"], why=ctx["why"],
                                    context=ctx, results=res, n_samples=ev.n_samples,
                                    t_first=ev.t_first, t_last=ev.t_last)
        except Exception as e:      # IRIS not running (no sessions row) / DB down: live view still has it
            log.warning("couldn't store this flight's rule checks: %s", e)

    rt._check_rules_live = _check_rules_live
    rt._finish_rule_checks = _finish_rule_checks
    rt._load_sop = _load_sop
    rt.live_rules = live_rules
