"""FastAPI routes for checking flights against their rules (sop/results.py).
Mounted next to the SOP routes at /sop, but needs only asyncpg (not the PDF
tools), so flights can be checked on any machine that can reach Postgres.

    GET  /sop/strictness                  presets, their numbers, and each mode's default
    POST /sop/evaluate/{session_id}       check (or re-check) a session's flight
                                          body: {strictness?, use_current_rules?}
    GET  /sop/evaluations/{session_id}    the latest check, with the instructor's verdicts applied
    GET  /sop/lesson-plans?aircraft=      every flying lesson: is it checked, how many rules (sop/lesson_plans.py)
    GET  /sop/lesson-plans/{lesson_id}    that lesson's rule list: every usable rule, recommended (+why), included
    PATCH /sop/lesson-plans/{lesson_id}   {checks_on?, include?: [rule ids], exclude?: [...], reset?, aircraft?}
                                          Free Flight is always checked against every active rule.

The instructor's verdicts and sign-off are written by IRIS (backend/app/
routers/rule_results.py), which knows who is signed in and their role.
"""
from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from core.lesson_catalog import load_catalog

from .evaluate import MODE_STRICTNESS, STRICTNESS, STRICTNESS_HELP
from .lesson_plans import LessonPlans, flying_lessons
from .results import ResultStore
from .store import DBUnavailable, RuleStore

log = logging.getLogger(__name__)


class LessonPlanIn(BaseModel):
    checks_on: bool | None = None
    include: list[int] | None = None
    exclude: list[int] | None = None
    reset: bool = False
    aircraft: str | None = None


class EvaluateIn(BaseModel):
    strictness: str | None = None
    use_current_rules: bool = False


def build_eval_router(cfg: dict | None, store: RuleStore | None = None) -> tuple[APIRouter, ResultStore]:
    cfg = cfg or {}
    store = store or RuleStore((cfg.get("database") or {}).get("url"))
    results = ResultStore(store, cfg)
    plans = LessonPlans(store)
    r = APIRouter(prefix="/sop", tags=["sop-evaluate"])

    def default_aircraft() -> str | None:
        """The aircraft the curriculum is flown in, unless the page picks another."""
        return ((cfg.get("sop") or {}).get("aircraft_override") or "").strip() or \
            (Path(str(cfg.get("aircraft_profile") or "")).stem or None)

    async def db(coro):
        try:
            return await coro
        except DBUnavailable as e:
            raise HTTPException(503, f"Database unavailable: {e}")
        except LookupError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))
        except RuntimeError as e:
            raise HTTPException(503, str(e))

    @r.get("/strictness")
    async def strictness():
        return {"presets": STRICTNESS, "mode_defaults": MODE_STRICTNESS, "help": STRICTNESS_HELP}

    @r.get("/lesson-plans")
    async def lesson_plans(aircraft: str | None = None):
        ac = aircraft or default_aircraft()
        return {"aircraft_id": ac, "lessons": await db(plans.summaries(load_catalog(), ac))}

    @r.get("/lesson-plans/{lesson_id}")
    async def lesson_plan(lesson_id: str, aircraft: str | None = None):
        if lesson_id not in {x["id"] for x in flying_lessons(load_catalog())}:
            raise HTTPException(404, f"No flying lesson '{lesson_id}'")
        return await db(plans.plan(lesson_id, aircraft or default_aircraft()))

    @r.patch("/lesson-plans/{lesson_id}")
    async def edit_lesson_plan(lesson_id: str, body: LessonPlanIn, x_sop_editor: str | None = Header(None)):
        if lesson_id not in {x["id"] for x in flying_lessons(load_catalog())}:
            raise HTTPException(404, f"No flying lesson '{lesson_id}'")
        return await db(plans.update(lesson_id, by=x_sop_editor, checks_on=body.checks_on, include=body.include,
                                     exclude=body.exclude, reset=body.reset,
                                     aircraft_id=body.aircraft or default_aircraft()))

    @r.post("/evaluate/{session_id}")
    async def evaluate(session_id: UUID, body: EvaluateIn | None = None,
                       x_sop_editor: str | None = Header(None)):
        body = body or EvaluateIn()
        return await db(results.evaluate_session(session_id, strictness=body.strictness,
                                                 use_current_rules=body.use_current_rules,
                                                 requested_by=x_sop_editor))

    @r.get("/evaluations/{session_id}")
    async def latest(session_id: UUID):
        out = await db(results.latest(session_id))
        if out is None:
            raise HTTPException(404, "This session hasn't been checked against its rules yet")
        return out

    return r, results
