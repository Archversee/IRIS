"""Review → Debrief: the whole-flight summary, and the job that writes every
AI note for a flight in the background (post-flight only).

  GET   /sessions/{id}/debrief          the latest summary, the facts behind it, the job's progress
  GET   /sessions/{id}/debrief/status   just the job's progress (cheap, for polling)
  POST  /sessions/{id}/debrief/run      start the job: {"auto": true} from Save / opening Review (honours
                                        debrief.auto_on_save, fills in only what's missing),
                                        {"redo_summary": true} / {"all_notes": true} instructors
  PATCH /flight-summaries/{id}          instructors: {rating: helpful|not_helpful|null, note}

Started by itself when a flight ends (routers/sessions.py) and whenever Review
is opened with something missing, so nobody has to press a button. The job
writes the summary first (again if the rule check or the flight's end is newer
than it), then a note for every broken-rule moment that has none yet
(debrief.max_auto_notes, -1 = all), one model call at a time across the
backend. A note that fails is tried once more, then skipped; the rest still
get written. It waits a little for the rule check if MAVIS hasn't written it
yet. Job progress lives in memory: after a backend restart, opening Review
starts it again (what was written is kept).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..auth import current_user
from ..flight_summary import PROMPT_VERSION, build_facts, build_messages, split_sections
from ..incident_explain import ModelTimeout, ask_model, debrief_settings, unsupported_numbers
from ..progress import pilot_of
from .incidents import cached_findings, write_note
from .progress import _stats, _version
from .rule_results import require_instructor

log = logging.getLogger(__name__)
router = APIRouter(tags=["debrief"])

WAIT_FOR_CHECK_S = 120.0       # how long the job waits for MAVIS's rule check to appear

SCHEMA = """
create table if not exists flight_summaries (
    id             bigint generated always as identity primary key,
    session_id     uuid not null references sessions(id) on delete cascade,
    model          text,
    prompt_version text,
    reasoned       boolean,
    text           text not null,
    facts          jsonb,
    unsupported_numbers text[] not null default '{}',
    requested_by   text,
    created_at     timestamptz not null default now(),
    rating         text check (rating in ('helpful', 'not_helpful')),
    rating_note    text,
    rated_by       text,
    rated_at       timestamptz
);
create index if not exists flight_summaries_session on flight_summaries (session_id, created_at desc)
"""


async def ensure_schema() -> None:
    try:
        async with db.acquire() as conn:
            for stmt in (s.strip() for s in SCHEMA.split(";")):
                if stmt:
                    await conn.execute(stmt)
    except Exception as e:      # sessions not created yet
        log.warning("flight_summaries not created: %s", e)


def _j(v):
    return json.loads(v) if isinstance(v, str) else v


def _row(r) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    d["facts"] = _j(d.get("facts"))
    for k in ("created_at", "rated_at"):
        d[k] = d[k].isoformat() if d.get(k) else None
    d["session_id"] = str(d["session_id"])
    d["sections"] = split_sections(d["text"])
    return d


# ------------------------------------------------------------------ facts
async def _facts(conn, sid: UUID) -> dict:
    s = await conn.fetchrow("select id, name, pilot_name, lesson_id, started_at, created_at from sessions where id=$1", sid)
    if s is None:
        raise HTTPException(404, "No such session")
    span = await conn.fetchrow("select min(ts) a, max(ts) b from flight_data where session_id=$1", sid)
    t_start = span["a"].timestamp() if span and span["a"] else None
    session = {"name": (s["name"] or "").rsplit(" – ", 1)[0] or s["lesson_id"], "pilot": pilot_of(dict(s)),
               "flight_s": (span["b"] - span["a"]).total_seconds() if span and span["a"] else None}
    ev = await conn.fetchrow("select id, strictness, signed_off_at, instructor_comment from flight_evaluations "
                             "where session_id=$1 order by created_at desc limit 1", sid)
    rules, incidents = [], []
    if ev:
        budget = [40]
        for r in await conn.fetch("select id, title, severity, verdict, instructor_verdict, violations from rule_results "
                                  "where evaluation_id=$1 order by sort_order, id", ev["id"]):
            v = r["instructor_verdict"] or r["verdict"]
            viol = (_j(r["violations"]) or []) if v == "fail" else []
            rules.append({"title": r["title"], "severity": r["severity"], "verdict": v, "breaches": len(viol)})
            for idx, x in enumerate(viol):
                found, _ = await cached_findings(conn, r["id"], idx, budget)
                incidents.append({"title": r["title"], "t_s": (x["t0"] - t_start) if t_start and x.get("t0") else None,
                                  "phase": x.get("phase"), "peak_text": x.get("peak_text"), "dur_s": x.get("dur_s"),
                                  "findings": found})
    incidents.sort(key=lambda i: i["t_s"] or 0)
    version, zones = await _version(conn)
    stats, _ = await _stats(conn, sid, version, zones, [1])
    previous = await _previous(conn, dict(s))
    return build_facts(session, {"strictness": ev["strictness"], "signed_off": bool(ev["signed_off_at"]),
                                 "instructor_comment": ev["instructor_comment"]} if ev else None,
                       rules, incidents, stats, previous)


async def _previous(conn, s: dict) -> dict | None:
    """The same pilot's checked flight before this one, in brief."""
    pilot = pilot_of(s)
    if not pilot:
        return None
    when = s.get("started_at") or s.get("created_at")
    for o in await conn.fetch("select id, name, pilot_name, started_at, created_at from sessions "
                              "where coalesce(started_at, created_at) < $1 order by coalesce(started_at, created_at) desc "
                              "limit 50", when):
        if (pilot_of(dict(o)) or "").lower() != pilot.lower():
            continue
        ev = await conn.fetchrow("select id from flight_evaluations where session_id=$1 order by created_at desc limit 1",
                                 o["id"])
        if not ev:
            continue
        broken, times = [], 0
        for r in await conn.fetch("select title, verdict, instructor_verdict, violations from rule_results "
                                  "where evaluation_id=$1", ev["id"]):
            if (r["instructor_verdict"] or r["verdict"]) == "fail":
                broken.append(r["title"])
                times += len(_j(r["violations"]) or [])
        t = o["started_at"] or o["created_at"]
        return {"date": t.strftime("%d %b"), "rules_broken": broken, "times_broken": times}
    return None


async def write_summary(sid: UUID, by: str) -> dict:
    async with db.acquire() as conn:
        facts = await _facts(conn, sid)
    text, model, reasoned = await ask_model(build_messages(facts))
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "insert into flight_summaries (session_id, model, prompt_version, reasoned, text, facts, unsupported_numbers, "
            "requested_by) values ($1,$2,$3,$4,$5,$6,$7,$8) returning *", sid, model, PROMPT_VERSION, reasoned, text,
            json.dumps(facts), unsupported_numbers(text, facts), by)
    return _row(row)


# ------------------------------------------------------------------ the job
_jobs: dict[str, dict] = {}           # session id -> progress
RETRY_AFTER_ERROR_S = 300.0           # an automatic start doesn't retry a failed job sooner than this


def _job(sid: UUID) -> dict | None:
    return _jobs.get(str(sid))


async def _missing_notes(sid: UUID, limit: int) -> list[tuple[int, int]]:
    async with db.acquire() as conn:
        ev = await conn.fetchrow("select id from flight_evaluations where session_id=$1 order by created_at desc limit 1", sid)
        if not ev:
            return []
        try:
            have = {(r["result_id"], r["idx"]) for r in await conn.fetch(
                "select distinct e.result_id, e.idx from incident_explanations e join rule_results r on r.id = e.result_id "
                "where r.evaluation_id=$1", ev["id"])}
        except Exception:      # table not there yet
            have = set()
        out = []
        for r in await conn.fetch("select id, verdict, instructor_verdict, violations from rule_results "
                                  "where evaluation_id=$1 order by sort_order, id", ev["id"]):
            if (r["instructor_verdict"] or r["verdict"]) != "fail":
                continue
            for idx, _ in enumerate(_j(r["violations"]) or []):
                if (r["id"], idx) not in have:
                    out.append((r["id"], idx))
        return out[:limit] if limit >= 0 else out


async def _summary_needed(conn, sid: UUID) -> bool:
    """No summary yet, or the latest one was written before the flight's latest
    rule check / before it ended (written early, or the pilot pressed Fly again)."""
    last = await conn.fetchval("select max(created_at) from flight_summaries where session_id=$1", sid)
    if last is None:
        return True
    newer = await conn.fetchval(
        "select greatest((select max(created_at) from flight_evaluations where session_id=$1), "
        "(select ended_at from sessions where id=$1))", sid)
    return newer is not None and newer > last


async def _still_flying(conn, sid: UUID) -> bool:
    try:
        return bool(await conn.fetchval("select source = 'mavis' and ended_at is null from sessions where id=$1", sid))
    except Exception:          # no `source` column (CSV-only setup): nothing is ever live
        return False


async def work_left(sid: UUID) -> bool:
    async with db.acquire() as conn:
        if await _still_flying(conn, sid):
            return False
        if await _summary_needed(conn, sid):
            return True
    return bool(await _missing_notes(sid, 1))


async def _run(sid: UUID, job: dict, redo_summary: bool, max_notes: int) -> None:
    try:
        job["state"] = "waiting for the rule check"
        t_end = time.monotonic() + WAIT_FOR_CHECK_S
        while True:
            async with db.acquire() as conn:
                checked = await conn.fetchval("select 1 from flight_evaluations where session_id=$1 limit 1", sid)
            if checked or time.monotonic() > t_end:
                break
            await asyncio.sleep(5)
        async with db.acquire() as conn:
            summary = redo_summary or await _summary_needed(conn, sid)
        notes = await _missing_notes(sid, max_notes)
        job["total"] = int(summary) + len(notes)
        job["done"] = job["failed"] = 0
        if summary:
            job["state"] = "writing the flight summary"
            await _attempt(job, lambda: write_summary(sid, job["by"]))
        for n, (rid, idx) in enumerate(notes, 1):
            job["state"] = f"writing broken-rule note {n} of {len(notes)}"
            await _attempt(job, lambda rid=rid, idx=idx: write_note(rid, idx, None, None, job["by"]))
        if job["failed"]:
            job["state"] = "error"
            job["error"] = (f"{job['failed']} of {job['total']} couldn't be written (last: {job['error']}). "
                            "Open Review again or press the button to try those again.")
        else:
            job["state"], job["error"] = "done", None
    except _Unreachable as e:          # no point trying the rest
        job["state"], job["error"] = "error", str(e)
    except Exception as e:             # noqa: BLE001 -- never let a background job die silently
        log.exception("debrief job for %s failed", sid)
        job["state"], job["error"] = "error", f"{type(e).__name__}: {e}"
    finally:
        job["finished_at"] = time.time()


class _Unreachable(Exception):
    pass


async def _attempt(job: dict, fn) -> None:
    """One summary or note: tried twice; a failure is counted and the job carries on,
    unless the model can't be reached at all."""
    for attempt in (1, 2):
        try:
            await fn()
            job["done"] += 1
            return
        except ModelTimeout as e:
            err = str(e)
        except RuntimeError as e:
            if "isn't reachable" in str(e) or "No model set" in str(e):
                raise _Unreachable(str(e)) from e
            err = str(e)
        except HTTPException as e:     # e.g. the rule snapshot is missing for this moment: skip it
            err = str(e.detail)
            break
        log.warning("debrief: attempt %s failed: %s", attempt, err)
    job["failed"] += 1
    job["error"] = err


def start_job(sid: UUID, by: str, redo_summary: bool = False, max_notes: int | None = None) -> dict:
    cur = _job(sid)
    if cur and cur["state"] not in ("done", "error"):
        return cur
    d = debrief_settings()
    job = {"state": "queued", "done": 0, "failed": 0, "total": None, "error": None, "by": by,
           "started_at": time.time(), "model": d["model"]}
    _jobs[str(sid)] = job
    job["task"] = asyncio.get_running_loop().create_task(
        _run(sid, job, redo_summary, d["max_auto_notes"] if max_notes is None else max_notes))
    return job


async def auto_start(sid: UUID) -> dict | None:
    """Start the job by itself (the flight ended / Review was opened) when debrief.auto_on_save
    is on and something is missing. -> the job, or None if there was nothing to do."""
    if not debrief_settings()["auto_on_save"]:
        return None
    cur = _job(sid)
    if cur and cur["state"] not in ("done", "error"):
        return cur
    if cur and cur["state"] == "error" and time.time() - cur.get("finished_at", 0) < RETRY_AFTER_ERROR_S:
        return None
    try:
        if not await work_left(sid):
            return None
    except Exception as e:             # noqa: BLE001 -- tables not there yet etc.
        log.info("debrief auto start for %s skipped: %s", sid, e)
        return None
    return start_job(sid, "auto")


def on_session_ended(sid: UUID) -> None:
    """routers/sessions.py, the moment ended_at is set: write the debrief in the background."""
    async def go():
        try:
            await auto_start(sid)
        except Exception:              # noqa: BLE001
            log.exception("debrief auto start for %s failed", sid)
    asyncio.get_running_loop().create_task(go())


def _public(job: dict | None) -> dict | None:
    return {k: v for k, v in job.items() if k != "task"} if job else None


# ------------------------------------------------------------------ API
@router.get("/sessions/{session_id}/debrief")
async def get_debrief(session_id: UUID, user: dict = Depends(current_user)):
    async with db.acquire() as conn:
        summary = _row(await conn.fetchrow("select * from flight_summaries where session_id=$1 "
                                           "order by created_at desc limit 1", session_id))
        facts = await _facts(conn, session_id)
    missing = await _missing_notes(session_id, -1)
    d = debrief_settings()
    return {"summary": summary, "facts": facts, "job": _public(_job(session_id)),
            "notes_missing": len(missing), "model": d["model"], "auto_on_save": d["auto_on_save"]}


@router.get("/sessions/{session_id}/debrief/status")
async def debrief_status(session_id: UUID, user: dict = Depends(current_user)):
    """Cheap: just the background job's progress (Review polls it)."""
    return {"job": _public(_job(session_id))}


class RunIn(BaseModel):
    auto: bool = False             # Save / opening Review: only if debrief.auto_on_save, only what's missing
    redo_summary: bool = False     # instructors: write a fresh summary
    all_notes: bool = False        # instructors: every missing note, ignoring max_auto_notes


@router.post("/sessions/{session_id}/debrief/run")
async def run_debrief(session_id: UUID, body: RunIn, user: dict = Depends(current_user)):
    instructor = user["role"] in ("instructor", "admin")
    if (body.redo_summary or body.all_notes or not body.auto) and not instructor:
        raise HTTPException(403, "Instructors only")
    async with db.acquire() as conn:
        if not await conn.fetchval("select 1 from sessions where id=$1", session_id):
            raise HTTPException(404, "No such session")
    if body.auto:
        if not debrief_settings()["auto_on_save"]:
            return {"started": False, "reason": "debrief.auto_on_save is off in config.yaml"}
        job = await auto_start(session_id)
        return {"started": job is not None, "job": _public(job or _job(session_id))}
    job = start_job(session_id, user.get("full_name") or user.get("username"),
                    redo_summary=body.redo_summary, max_notes=-1 if body.all_notes else None)
    return {"started": True, "job": _public(job)}


class RatingIn(BaseModel):
    rating: str | None = None
    note: str | None = Field(default=None, max_length=1000)


@router.patch("/flight-summaries/{summary_id}")
async def rate_summary(summary_id: int, body: RatingIn, user: dict = Depends(require_instructor)):
    if body.rating not in (None, "helpful", "not_helpful"):
        raise HTTPException(400, "rating must be helpful, not_helpful or null")
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "update flight_summaries set rating=$2, rating_note=$3, rated_by=$4, rated_at=now() where id=$1 returning *",
            summary_id, body.rating, (body.note or "").strip() or None, user.get("full_name") or user.get("username"))
    if row is None:
        raise HTTPException(404, "No such summary")
    return _row(row)
