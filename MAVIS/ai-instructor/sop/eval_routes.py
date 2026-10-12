"""FastAPI routes for checking flights against their rules (sop/results.py).
Mounted next to the SOP routes at /sop, but needs only asyncpg (not the PDF
tools), so flights can be checked on any machine that can reach Postgres.

    GET  /sop/strictness                  presets, their numbers, and each mode's default
    POST /sop/evaluate/{session_id}       check (or re-check) a session's flight
                                          body: {strictness?, use_current_rules?}
    GET  /sop/evaluations/{session_id}    the latest check, with the instructor's verdicts applied

The instructor's verdicts and sign-off are written by IRIS (backend/app/
routers/rule_results.py), which knows who is signed in and their role.
"""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from .evaluate import MODE_STRICTNESS, STRICTNESS, STRICTNESS_HELP
from .results import ResultStore
from .store import DBUnavailable, RuleStore

log = logging.getLogger(__name__)


class EvaluateIn(BaseModel):
    strictness: str | None = None
    use_current_rules: bool = False


def build_eval_router(cfg: dict | None, store: RuleStore | None = None) -> tuple[APIRouter, ResultStore]:
    cfg = cfg or {}
    store = store or RuleStore((cfg.get("database") or {}).get("url"))
    results = ResultStore(store, cfg)
    r = APIRouter(prefix="/sop", tags=["sop-evaluate"])

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
