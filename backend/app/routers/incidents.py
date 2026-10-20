"""The 30 s before and after a broken rule (app/incidents.py does the analysis).

  GET   /sessions/{id}/incidents                             every broken-rule moment of the flight, oldest
                                                            first, with what its analysis found (cached)
  GET   /rule-results/{result_id}/incidents/{index}           the analysis + its latest explanation
  POST  /rule-results/{result_id}/incidents/{index}/explain   ask the reasoning model (instructors)
  PATCH /incident-explanations/{id}                         instructor: {rating: helpful|not_helpful, note}
      index = which time the rule was broken (rule_results.violations[index]).
      Reading: any signed-in user, like the rule results themselves.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..auth import current_user
from ..incident_explain import explain
from ..incidents import POST_S, PRE_S, analyse
from .rule_results import require_instructor

log = logging.getLogger(__name__)
router = APIRouter(tags=["incidents"])

MAX_NEW_FINDINGS = 20      # analyses worked out per request; `pending` says how many are left

FINDINGS_SCHEMA = """
create table if not exists incident_findings (
    result_id    bigint not null references rule_results(id) on delete cascade,
    idx          int not null,
    findings     jsonb,                  -- [{code, kind, text}], or null when it couldn't be analysed
    computed_at  timestamptz not null default now(),
    primary key (result_id, idx)
)
"""


async def ensure_schema() -> None:
    try:
        async with db.acquire() as conn:
            await conn.execute(FINDINGS_SCHEMA)
    except Exception as e:      # rule_results not created yet
        log.warning("incident_findings not created: %s", e)

_FLIGHT_COLS = ("ts, airspeed_kt, altitude_ft, vertical_speed_fpm, heading_true_deg, pitch_deg, bank_deg, "
                "throttle_pct, elevator_position, rudder_position, flaps_handle_pct")
_FLIGHT_MAVIS = ", phase, rpm, aileron_position, flaps_deg"
_EYE_COLS = "ts, aoi, gaze_point_x, gaze_point_y, blink, pupil_diam_left_mm, pupil_diam_right_mm"


def _j(v):
    return json.loads(v) if isinstance(v, str) else v


def _ts(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


async def load_incident(result_id: int, index: int) -> dict:
    async with db.acquire() as conn:
        rr = await conn.fetchrow("select * from rule_results where id=$1", result_id)
        if rr is None:
            raise HTTPException(404, "No such rule result")
        violations = _j(rr["violations"]) or []
        if not 0 <= index < len(violations):
            raise HTTPException(404, "This rule wasn't broken that many times")
        ev = await conn.fetchrow("select rules, strictness, elevation_ft from flight_evaluations where id=$1",
                                 rr["evaluation_id"])
        rules = _j(ev["rules"]) or [] if ev else []
        rule = next((r for r in rules if rr["rule_id"] is not None and r.get("id") == rr["rule_id"]), None) \
            or next((r for r in rules if r.get("title") == rr["title"]), None)
        if rule is None:
            raise HTTPException(409, "The rule this result was checked against isn't in the check's snapshot")
        v = violations[index]
        sid = rr["session_id"]
        lo, hi = _ts(float(v["t0"]) - PRE_S - 1), _ts(float(v["t1"]) + POST_S + 1)
        try:
            flight = await conn.fetch(f"select {_FLIGHT_COLS}{_FLIGHT_MAVIS} from flight_data "
                                      "where session_id=$1 and ts between $2 and $3 order by ts", sid, lo, hi)
        except Exception:      # schema_mavis_integration.sql not applied (CSV-only setup)
            flight = await conn.fetch(f"select {_FLIGHT_COLS} from flight_data "
                                      "where session_id=$1 and ts between $2 and $3 order by ts", sid, lo, hi)
        eye = await conn.fetch(f"select {_EYE_COLS} from eye_tracking_data "
                               "where session_id=$1 and ts between $2 and $3 order by ts", sid, lo, hi)
        zones = await conn.fetch("select name, x1, y1, x2, y2 from aoi_zones")
        try:
            coaching = await conn.fetch("select ts, who, code, message from coaching_events "
                                        "where session_id=$1 and ts between $2 and $3 order by ts", sid, lo, hi)
        except Exception:
            coaching = []
        baseline = await conn.fetchval(
            "select percentile_cont(0.5) within group (order by (coalesce(pupil_diam_left_mm, pupil_diam_right_mm)"
            " + coalesce(pupil_diam_right_mm, pupil_diam_left_mm)) / 2) from eye_tracking_data "
            "where session_id=$1 and not coalesce(blink, false) "
            "and coalesce(pupil_diam_left_mm, pupil_diam_right_mm) is not null", sid)
        try:
            c = await conn.fetchrow("select eye_clock_offset_s, eye_clock_spread_s, eye_clock_corrected, "
                                    "eye_clock_checked_at from sessions where id=$1", sid)
            clock = {"offset_s": c["eye_clock_offset_s"], "spread_s": c["eye_clock_spread_s"],
                     "corrected": c["eye_clock_corrected"],
                     "checked_at": c["eye_clock_checked_at"].isoformat() if c["eye_clock_checked_at"] else None}
        except Exception:      # backend not restarted since the eye-clock columns were added
            clock = {}
    out = analyse(rule, v, [dict(r) for r in flight], [dict(r) for r in eye], [dict(z) for z in zones],
                  [dict(x) for x in coaching], strictness=ev["strictness"] if ev else None,
                  elevation_ft=ev["elevation_ft"] if ev else None,
                  pupil_baseline_mm=round(baseline, 3) if baseline else None, clock=clock,
                  other_violations=[x for i, x in enumerate(violations) if i != index])
    out["result_id"], out["index"], out["session_id"] = result_id, index, str(sid)
    return out


def _expl(row) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for k in ("created_at", "rated_at"):
        d[k] = d[k].isoformat() if d.get(k) else None
    return d


async def _latest_explanation(result_id: int, index: int):
    try:
        async with db.acquire() as conn:
            return _expl(await conn.fetchrow(
                "select * from incident_explanations where result_id=$1 and idx=$2 order by created_at desc limit 1",
                result_id, index))
    except Exception:          # table not there yet
        return None


async def cached_findings(conn, result_id: int, idx: int, budget: list[int]) -> tuple[list[dict] | None, bool]:
    """A broken-rule moment's findings, from the cache or worked out now if
    budget[0] allows. -> (findings, or None if it couldn't be analysed; still_pending)"""
    row = await conn.fetchrow("select findings from incident_findings where result_id=$1 and idx=$2", result_id, idx)
    if row:
        return _j(row["findings"]), False
    if budget[0] <= 0:
        return None, True
    budget[0] -= 1
    try:
        a = await load_incident(result_id, idx)
        found = [{"code": f["code"], "kind": f["kind"], "text": f["text"]} for f in a["findings"]]
    except Exception as e:      # rule snapshot missing etc.: remember it, don't retry every time
        log.info("incident %s/%s not analysed: %s", result_id, idx, e)
        found = None
    await conn.execute("insert into incident_findings (result_id, idx, findings) values ($1,$2,$3) "
                       "on conflict do nothing", result_id, idx, json.dumps(found) if found is not None else None)
    return found, False


@router.get("/sessions/{session_id}/incidents")
async def session_incidents(session_id: UUID, user: dict = Depends(current_user)):
    budget, pending, out = [MAX_NEW_FINDINGS], 0, []
    async with db.acquire() as conn:
        ev = await conn.fetchrow("select id from flight_evaluations where session_id=$1 "
                                 "order by created_at desc limit 1", session_id)
        if ev is None:
            return {"checked": False, "incidents": [], "pending": 0}
        try:
            explained = {(r["result_id"], r["idx"]) for r in await conn.fetch(
                "select distinct e.result_id, e.idx from incident_explanations e "
                "join rule_results r on r.id = e.result_id where r.evaluation_id=$1", ev["id"])}
        except Exception:      # table not there yet
            explained = set()
        for r in await conn.fetch("select id, title, severity, verdict, instructor_verdict, violations "
                                  "from rule_results where evaluation_id=$1 order by sort_order, id", ev["id"]):
            if (r["instructor_verdict"] or r["verdict"]) != "fail":
                continue
            for idx, v in enumerate(_j(r["violations"]) or []):
                found, p = await cached_findings(conn, r["id"], idx, budget)
                pending += int(p)
                out.append({"result_id": r["id"], "index": idx, "title": r["title"], "severity": r["severity"],
                            "t0": v.get("t0"), "t1": v.get("t1"), "dur_s": v.get("dur_s"), "phase": v.get("phase"),
                            "peak_text": v.get("peak_text"), "pending": p, "findings": found,
                            "explained": (r["id"], idx) in explained})
    out.sort(key=lambda x: x["t0"] or 0)
    return {"checked": True, "incidents": out, "pending": pending}


@router.get("/rule-results/{result_id}/incidents/{index}")
async def incident(result_id: int, index: int, user: dict = Depends(current_user)):
    out = await load_incident(result_id, index)
    out["explanation"] = await _latest_explanation(result_id, index)
    return out


@router.post("/rule-results/{result_id}/incidents/{index}/explain")
async def explain_incident(result_id: int, index: int, user: dict = Depends(require_instructor)):
    a = await load_incident(result_id, index)
    try:
        e = await explain(a)
    except RuntimeError as err:
        raise HTTPException(503, str(err))
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "insert into incident_explanations (result_id, idx, model, prompt_version, reasoned, text, "
            "unsupported_numbers, requested_by) values ($1,$2,$3,$4,$5,$6,$7,$8) returning *",
            result_id, index, e["model"], e["prompt_version"], e["reasoned"], e["text"], e["unsupported_numbers"],
            user.get("full_name") or user.get("username"))
    return _expl(row)


class RatingIn(BaseModel):
    rating: str | None = None                       # helpful | not_helpful | null (clear)
    note: str | None = Field(default=None, max_length=1000)


@router.patch("/incident-explanations/{expl_id}")
async def rate(expl_id: int, body: RatingIn, user: dict = Depends(require_instructor)):
    if body.rating not in (None, "helpful", "not_helpful"):
        raise HTTPException(400, "rating must be helpful, not_helpful or null")
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "update incident_explanations set rating=$2, rating_note=$3, rated_by=$4, rated_at=now() "
            "where id=$1 returning *", expl_id, body.rating, (body.note or "").strip() or None,
            user.get("full_name") or user.get("username"))
    if row is None:
        raise HTTPException(404, "No such explanation")
    return _expl(row)
