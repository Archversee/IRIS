"""SOP / handbook rule checks of a session: read them, and let the
instructor have the final say.

MAVIS computes the checks (MAVIS/ai-instructor/sop/results.py) and writes
flight_evaluations + rule_results; the frontend asks MAVIS to (re-)check via
/mavis/sop/evaluate/{session_id}. IRIS writes only the instructor's part,
because it knows who is signed in and their role.

  GET   /sessions/{id}/rule-results            latest check (any signed-in user)
  PATCH /rule-results/{id}                     instructor: {instructor_verdict, instructor_note}
  POST  /sessions/{id}/rule-results/sign-off   instructor: {signed_off, comment}
  GET   /users/{id}/rule-strictness            the trainee's strictness preset
  PUT   /users/{id}/rule-strictness            admin, or the trainee's own instructor: {strictness | null}
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..auth import current_user

log = logging.getLogger(__name__)
router = APIRouter(tags=["rule-results"])

_SCHEMA_SQL = Path(__file__).resolve().parents[3] / "db" / "schema_rule_results.sql"
STRICTNESS = ("lenient", "standard", "strict")
STRICTNESS_LABELS = {"lenient": "Lenient", "standard": "Standard (ACS-like)", "strict": "Strict"}  # MAVIS sop/evaluate.py
OVERRIDES = ("pass", "fail", "not_applicable")


async def ensure_schema() -> None:
    """Create the rule-results tables if missing (idempotent). Skipped if the
    file isn't there; MAVIS then reports the tables as missing."""
    if not _SCHEMA_SQL.is_file():
        log.warning("rule results: %s not found -- tables not created", _SCHEMA_SQL)
        return
    sql = "\n".join(re.sub(r"--.*$", "", l) for l in _SCHEMA_SQL.read_text(encoding="utf-8").splitlines())
    async with db.acquire() as conn:
        for stmt in (s.strip() for s in sql.split(";")):
            if stmt:
                await conn.execute(stmt)


def require_instructor(user: dict = Depends(current_user)) -> dict:
    if user["role"] not in ("instructor", "admin"):
        raise HTTPException(403, "Instructors only")
    return user


def _who(user: dict) -> str:
    return user.get("full_name") or user.get("username") or "instructor"


EVAL_JSONB = ("rules", "summary")                       # flight_evaluations
RESULT_JSONB = ("metrics", "violations", "checks")      # rule_results (its summary is text)


def _row(r, jsonb: tuple = ()) -> dict:
    """asyncpg row -> JSON-ready dict. IRIS's pool has no jsonb codec, so
    jsonb columns arrive as text and are decoded here."""
    d = {}
    for k, v in dict(r).items():
        if k in jsonb and isinstance(v, str):
            v = json.loads(v)
        d[k] = str(v) if isinstance(v, UUID) else v.isoformat() if hasattr(v, "isoformat") else v
    return d


def _summary(results: list[dict]) -> dict:
    out = {"total": len(results), "by_verdict": {}, "failed_by_severity": {}}
    for x in results:
        v = x["final_verdict"]
        out["by_verdict"][v] = out["by_verdict"].get(v, 0) + 1
        if v == "fail":
            s = x.get("severity") or "caution"
            out["failed_by_severity"][s] = out["failed_by_severity"].get(s, 0) + 1
    return out


@router.get("/sessions/{session_id}/rule-results")
async def latest(session_id: UUID, user: dict = Depends(current_user)):
    async with db.acquire() as conn:
        ev = await conn.fetchrow("select * from flight_evaluations where session_id=$1 "
                                 "order by created_at desc limit 1", session_id)
        if ev is None:
            raise HTTPException(404, "This session hasn't been checked against its rules yet")
        rows = await conn.fetch("select * from rule_results where evaluation_id=$1 order by sort_order, id", ev["id"])
        n_evals = await conn.fetchval("select count(*) from flight_evaluations where session_id=$1", session_id)
    out = _row(ev, EVAL_JSONB)
    out.pop("rules", None)                  # the snapshot is for re-checking, not for display
    results = []
    for r in rows:
        d = _row(r, RESULT_JSONB)
        d["final_verdict"] = d.get("instructor_verdict") or d["verdict"]
        results.append(d)
    out["results"] = results
    out["summary"] = _summary(results)
    out["n_evaluations"] = n_evals
    out["strictness_label"] = STRICTNESS_LABELS.get(out.get("strictness"), out.get("strictness"))
    out["viewer_role"] = user["role"]
    return out


class VerdictIn(BaseModel):
    instructor_verdict: str | None = None          # null = agree with the computed verdict
    instructor_note: str | None = Field(default=None, max_length=1000)


@router.patch("/rule-results/{result_id}")
async def set_verdict(result_id: int, body: VerdictIn, user: dict = Depends(require_instructor)):
    if body.instructor_verdict is not None and body.instructor_verdict not in OVERRIDES:
        raise HTTPException(400, f"instructor_verdict must be one of {', '.join(OVERRIDES)} or null")
    note = (body.instructor_note or "").strip() or None
    async with db.acquire() as conn:
        row = await conn.fetchrow("""
            update rule_results set instructor_verdict=$2, instructor_note=$3,
                   reviewed_by=$4, reviewed_at=now()
             where id=$1 returning *""", result_id, body.instructor_verdict, note, _who(user))
    if row is None:
        raise HTTPException(404, "No such rule result")
    d = _row(row, RESULT_JSONB)
    d["final_verdict"] = d.get("instructor_verdict") or d["verdict"]
    return d


class SignOffIn(BaseModel):
    signed_off: bool = True
    comment: str | None = Field(default=None, max_length=4000)


@router.post("/sessions/{session_id}/rule-results/sign-off")
async def sign_off(session_id: UUID, body: SignOffIn, user: dict = Depends(require_instructor)):
    async with db.acquire() as conn:
        ev = await conn.fetchrow("select id from flight_evaluations where session_id=$1 "
                                 "order by created_at desc limit 1", session_id)
        if ev is None:
            raise HTTPException(404, "This session hasn't been checked against its rules yet")
        if body.signed_off:
            row = await conn.fetchrow("""
                update flight_evaluations set signed_off_by=$2, signed_off_at=now(), instructor_comment=$3
                 where id=$1 returning signed_off_by, signed_off_at, instructor_comment""",
                ev["id"], _who(user), (body.comment or "").strip() or None)
        else:
            row = await conn.fetchrow("""
                update flight_evaluations set signed_off_by=null, signed_off_at=null
                 where id=$1 returning signed_off_by, signed_off_at, instructor_comment""", ev["id"])
    return _row(row)


class StrictnessIn(BaseModel):
    strictness: str | None = None


@router.get("/users/{user_id}/rule-strictness")
async def get_strictness(user_id: UUID, user: dict = Depends(current_user)):
    async with db.acquire() as conn:
        v = await conn.fetchrow("select rule_strictness from users where id=$1", user_id)
    if v is None:
        raise HTTPException(404, "No such user")
    return {"strictness": v["rule_strictness"]}


@router.put("/users/{user_id}/rule-strictness")
async def put_strictness(user_id: UUID, body: StrictnessIn, user: dict = Depends(require_instructor)):
    if body.strictness is not None and body.strictness not in STRICTNESS:
        raise HTTPException(400, f"strictness must be one of {', '.join(STRICTNESS)} or null")
    async with db.acquire() as conn:
        t = await conn.fetchrow("select role, instructor_id from users where id=$1", user_id)
        if t is None:
            raise HTTPException(404, "No such user")
        if t["role"] != "trainee":
            raise HTTPException(400, "Strictness is set for trainees")
        if user["role"] != "admin" and t["instructor_id"] != user["id"]:
            raise HTTPException(403, "Only this trainee's instructor (or an admin) can change it")
        await conn.execute("update users set rule_strictness=$2 where id=$1", user_id, body.strictness)
    return {"strictness": body.strictness}
