"""Check a recorded session against its rules and store the results
(db/schema_rule_results.sql). The checking itself is sop/evaluate.py.

Which rules, and how strictly:

  * The rules a flight is checked against are SNAPSHOT into the evaluation,
    so later edits never silently change an old flight's result. Re-checking
    reuses the previous snapshot unless `use_current_rules` asks for the rule
    set as it is now.
  * Live (main.py, at the end of a lesson) passes the exact rules, aircraft
    and lesson it flew with.
  * A session with no evaluation yet (e.g. a CSV uploaded in IRIS) gets the
    aircraft from sessions.aircraft (matched like the sim's TITLE), else the
    configured profile; the lesson from sessions.lesson_id, else the
    configured lesson. A flight without MAVIS phases is only checked against
    rules that apply in every phase.
  * Strictness: the one asked for (= this session's), else the one this
    session was last checked with if it was chosen for the session, else the
    trainee's (users.rule_strictness, matched on the session's pilot name),
    else the MAVIS mode's default.

The instructor's verdicts (IRIS writes rule_results.instructor_*) are carried
over to every later evaluation of the same session.
"""
from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

import yaml

from .evaluate import (STRICTNESS, evaluate, pick_strictness, sample_from_flight_row, summarize)
from .runtime import match_profile
from .schema import APP_ROOT, LESSON_TYPES, aircraft_profiles, resolve_ref

log = logging.getLogger(__name__)

SCHEMA_MISSING = ("The rule-results tables are missing - start the IRIS backend once (it creates them) or run "
                  "psql -U postgres -d postgres -f db/schema_rule_results.sql")
_FLIGHT_COLS = ("ts, altitude_ft, airspeed_kt, vertical_speed_fpm, heading_true_deg, pitch_deg, bank_deg, "
                "throttle_pct, flaps_handle_pct")
_MAVIS_COLS = ", phase, rpm, flaps_deg, on_ground"
# rule fields kept in the snapshot (enough to re-check and to show where it came from)
_SNAP = ("id", "rule_set_id", "layer", "handbook", "kind", "title", "phases", "metric", "target", "tol_hi",
         "tol_lo", "min_value", "max_value", "op", "value", "gate_phase", "capture_at", "conditions", "severity",
         "aoi", "gaze_measure", "source_quote", "source_page", "ref_speed", "ref_factor", "ref_resolved",
         "lesson_types")


def snapshot(rules: list[dict]) -> list[dict]:
    return [{k: r.get(k) for k in _SNAP if r.get(k) is not None} for r in rules]


def load_lesson(lesson_id: str | None, default_path: str | None) -> tuple[str | None, dict]:
    for p in ([APP_ROOT / "lessons" / f"{lesson_id}.yaml"] if lesson_id else []) + \
             ([APP_ROOT / str(default_path)] if default_path else []):
        if p.is_file():
            try:
                return p.stem, yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except Exception:
                pass
    return lesson_id, {}


class ResultStore:
    def __init__(self, store, cfg: dict | None = None):
        self.store = store                     # sop/store.py RuleStore (shares its pool)
        self.cfg = cfg or {}

    # ------------------------------------------------------------ reads
    async def latest(self, session_id: UUID) -> dict | None:
        async with self.store.acquire() as c:
            ev = await c.fetchrow("select * from flight_evaluations where session_id=$1 "
                                  "order by created_at desc limit 1", session_id)
            if not ev:
                return None
            rows = await c.fetch("select * from rule_results where evaluation_id=$1 order by sort_order, id", ev["id"])
        return _out(ev, rows)

    async def _session(self, c, session_id: UUID) -> dict:
        row = await c.fetchrow("select * from sessions where id=$1", session_id)
        if not row:
            raise LookupError(f"No session {session_id}")
        return dict(row)

    async def _trainee_strictness(self, c, name: str | None) -> str | None:
        if not name:
            return None
        try:
            return await c.fetchval(
                "select rule_strictness from users where role='trainee' and rule_strictness is not null "
                "and (lower(full_name)=lower($1) or lower(username)=lower($1)) limit 1", name.strip())
        except Exception:                      # users table / column not there: no per-trainee setting
            return None

    async def _samples(self, c, session_id: UUID) -> tuple[list[dict], bool]:
        """flight_data rows, oldest first. has_phase = MAVIS columns present and filled."""
        try:
            rows = await c.fetch(f"select {_FLIGHT_COLS}{_MAVIS_COLS} from flight_data "
                                 "where session_id=$1 order by ts", session_id)
        except Exception:                      # schema_mavis_integration.sql not applied
            rows = await c.fetch(f"select {_FLIGHT_COLS} from flight_data where session_id=$1 order by ts", session_id)
        rows = [dict(r) for r in rows]
        return rows, any(r.get("phase") for r in rows)

    # ------------------------------------------------------------ rules for a session with no evaluation yet
    async def _resolve_rules(self, sess: dict) -> dict:
        aircraft_text = (sess.get("aircraft") or "").strip()
        profiles = aircraft_profiles()
        override = ((self.cfg.get("sop") or {}).get("aircraft_override") or "").strip()
        if override:
            pid = override
        elif aircraft_text:
            pid, _ = match_profile({"title": aircraft_text}, profiles)
        else:
            pid = Path(str(self.cfg.get("aircraft_profile") or "")).stem or None
        lesson_id, lesson = load_lesson(sess.get("lesson_id"), self.cfg.get("lesson"))
        lt = lesson.get("rules_lesson_type") if lesson.get("rules_lesson_type") in LESSON_TYPES else "normal"
        act = await self.store.active_rules(pid, lt) or {"rules": [], "layers": []}
        speeds = next((a["profile"].get("v_speeds") or {} for a in profiles if a["id"] == pid), {})
        rules = [x for x in (resolve_ref(r, speeds) for r in act["rules"]) if x is not None]
        return {"aircraft_profile": pid, "lesson_id": lesson_id, "lesson_type": lt,
                "elevation_ft": (lesson.get("airport") or {}).get("elevation_ft"),
                "rule_set_ids": [x["rule_set_id"] for x in act.get("layers") or []],
                "rules": snapshot(rules)}

    # ------------------------------------------------------------ evaluate + store
    async def evaluate_session(self, session_id: UUID, *, strictness: str | None = None,
                               context: dict | None = None, use_current_rules: bool = False,
                               source: str = "postflight", requested_by: str | None = None) -> dict:
        """context (live): {aircraft_profile, lesson_id, lesson_type, elevation_ft,
        rule_set_ids, rules}. Returns the stored evaluation with its results."""
        if strictness is not None and strictness not in STRICTNESS:
            raise ValueError(f"strictness must be one of {', '.join(STRICTNESS)}")
        async with self.store.acquire() as c:
            sess = await self._session(c, session_id)
            try:
                prev = await c.fetchrow("select * from flight_evaluations where session_id=$1 "
                                        "order by created_at desc limit 1", session_id)
            except Exception as e:
                raise RuntimeError(SCHEMA_MISSING) from e
            trainee = await self._trainee_strictness(c, sess.get("pilot_name") or sess.get("name"))
            rows, has_phase = await self._samples(c, session_id)
        if context is None:
            # reuse the flight's own rule snapshot -- unless it is empty (checked while no
            # rule set was active): then take the rules as they are now
            if prev is not None and not use_current_rules and prev["rules"]:
                context = {k: prev[k] for k in ("aircraft_profile", "lesson_id", "lesson_type", "elevation_ft",
                                                "rule_set_ids", "rules")}
            else:
                context = await self._resolve_rules(sess)
        if strictness:
            why = "set for this session"
        elif prev is not None and prev["strictness_why"] == "set for this session":
            strictness, why = prev["strictness"], "set for this session"
        else:
            strictness, why = pick_strictness(None, trainee, sess.get("mode"))

        rules = list(context.get("rules") or [])
        if not has_phase:     # no MAVIS phases: a phase-scoped rule can't be placed in time
            for r in rules:
                if r.get("phases") or r.get("gate_phase") or r.get("capture_at") in ("phase_entry", "phase_exit"):
                    r["_needs_phase"] = True
        flaps_full = _flaps_full(context.get("aircraft_profile"))
        samples = [sample_from_flight_row(r, context.get("elevation_ft"), flaps_full) for r in rows]
        results = evaluate([r for r in rules if not r.get("_needs_phase")], samples, strictness)
        for r in rules:                                   # keep rule order; mark the ones we couldn't place
            if r.get("_needs_phase"):
                r.pop("_needs_phase")
                results.append(_no_phase_result(r))
        pos = {r.get("id"): i for i, r in enumerate(rules)}
        results.sort(key=lambda x: pos.get(x["rule_id"], len(rules)))

        return await self.save(session_id, source=source, strictness=strictness, why=why, context=context,
                               results=results, n_samples=len(samples),
                               t_first=rows[0]["ts"] if rows else None, t_last=rows[-1]["ts"] if rows else None,
                               requested_by=requested_by)

    async def strictness_for(self, name: str | None, mode: str | None,
                             session_choice: str | None = None) -> tuple[str, str]:
        """Live: which preset a flight that's starting uses (session > trainee > mode)."""
        trainee = None
        if not session_choice:
            try:
                async with self.store.acquire() as c:
                    trainee = await self._trainee_strictness(c, name)
            except Exception:
                trainee = None
        return pick_strictness(session_choice, trainee, mode)

    async def save(self, session_id: UUID, *, source: str, strictness: str, why: str, context: dict,
                   results: list[dict], n_samples: int, t_first=None, t_last=None,
                   requested_by: str | None = None) -> dict:
        """Store one evaluation + its results. The instructor's verdicts on the
        session's previous evaluation are carried over (they have the final say)."""
        t_first, t_last = _ts(t_first), _ts(t_last)
        async with self.store.acquire() as c:
            async with c.transaction():
                try:
                    prev = await c.fetchrow("select id from flight_evaluations where session_id=$1 "
                                            "order by created_at desc limit 1", session_id)
                except Exception as e:
                    raise RuntimeError(SCHEMA_MISSING) from e
                carried = {}
                if prev is not None:
                    for x in await c.fetch("select rule_id, title, instructor_verdict, instructor_note, reviewed_by, "
                                           "reviewed_at from rule_results where evaluation_id=$1 and "
                                           "(instructor_verdict is not null or instructor_note is not null)", prev["id"]):
                        carried[(x["rule_id"], x["title"])] = x
                summary = summarize(results)
                ev = await c.fetchrow("""
                    insert into flight_evaluations (session_id, source, strictness, strictness_why, aircraft_profile,
                        lesson_id, lesson_type, elevation_ft, rule_set_ids, rules, summary, n_samples, t_first, t_last,
                        requested_by)
                    values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15) returning *""",
                    session_id, source, strictness, why, context.get("aircraft_profile"), context.get("lesson_id"),
                    context.get("lesson_type"), context.get("elevation_ft"),
                    [str(x) for x in context.get("rule_set_ids") or []], context.get("rules") or [], summary,
                    n_samples, t_first, t_last, requested_by)
                for i, res in enumerate(results):
                    keep = carried.get((res["rule_id"], res["title"]))
                    await c.execute("""
                        insert into rule_results (evaluation_id, session_id, rule_id, sort_order, title, kind, severity,
                            metric, layer, requirement, verdict, summary, metrics, violations, checks, source_quote,
                            source_page, instructor_verdict, instructor_note, reviewed_by, reviewed_at)
                        values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21)""",
                        ev["id"], session_id, res["rule_id"], i, res["title"] or "(untitled rule)", res["kind"],
                        res["severity"], res["metric"], res.get("layer"), res["requirement"], res["verdict"],
                        res["summary"], res["metrics"], res["violations"], res["checks"], res.get("source_quote"),
                        res.get("source_page"),
                        keep["instructor_verdict"] if keep else None, keep["instructor_note"] if keep else None,
                        keep["reviewed_by"] if keep else None, keep["reviewed_at"] if keep else None)
        log.info("checked session %s against %d rule(s) (%s, %s): %s", session_id, len(results), strictness,
                 source, summary["by_verdict"])
        return await self.latest(session_id)


def _ts(v):
    """epoch seconds (live) or datetime (flight_data) -> datetime | None."""
    if v is None or hasattr(v, "isoformat"):
        return v
    from datetime import datetime, timezone
    return datetime.fromtimestamp(float(v), tz=timezone.utc)


def _flaps_full(profile_id: str | None) -> float:
    for a in aircraft_profiles():
        if a["id"] == profile_id:
            det = ((a["profile"].get("flaps") or {}).get("detents_deg") or [30])
            return float(max(det))
    return 30.0


def _no_phase_result(r: dict) -> dict:
    from .evaluate import rule_text
    return {"rule_id": r.get("id"), "title": r.get("title"), "kind": r.get("kind"),
            "severity": r.get("severity") or "caution", "metric": r.get("metric"), "phases": r.get("phases") or [],
            "layer": r.get("layer"), "requirement": rule_text(r), "verdict": "insufficient_data",
            "summary": "This flight has no lesson phases (not flown with MAVIS), so a rule for a phase can't be placed.",
            "metrics": {}, "violations": [], "checks": [],
            "source_quote": r.get("source_quote"), "source_page": r.get("source_page")}


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _out(ev, rows) -> dict:
    e = {k: (str(v) if isinstance(v, UUID) else _iso(v)) for k, v in dict(ev).items()}
    res = []
    for r in rows:
        d = {k: (str(v) if isinstance(v, UUID) else _iso(v)) for k, v in dict(r).items()}
        d["final_verdict"] = d.get("instructor_verdict") or d["verdict"]
        res.append(d)
    e["results"] = res
    e["summary"] = summarize(res)            # with the instructor's verdicts applied
    e["strictness_label"] = STRICTNESS.get(e["strictness"], {}).get("label")
    return e
