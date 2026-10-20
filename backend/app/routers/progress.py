"""A pilot's progress across flights, and the whole group's (post-flight only).

  GET /progress/pilots            pilots with flights (a trainee sees only themselves)
  GET /progress/pilot?name=Alex   flights oldest first: rule verdicts, breaches and their causes,
                                  scan statistics, plus plain-words insights (app/progress.py)
  GET /progress/cohort            instructors: most-broken rules and most common causes, all pilots

Expensive parts are cached and filled in lazily, a bounded number per request:
  session_scan_stats   scan statistics per session (recomputed when the AOI zones change)
  incident_summaries   the finding codes of each broken-rule moment (app/incidents.py)
`pending` in a response = how many are still to be worked out (reload to fill them in).
"""
from __future__ import annotations

import hashlib
import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from .. import db
from ..auth import current_user
from ..progress import CAUSE_KIND, CAUSES, SCAN_VERSION, flight_label, insights, pilot_of, scan_stats
from .incidents import load_incident
from .rule_results import require_instructor

log = logging.getLogger(__name__)
router = APIRouter(prefix="/progress", tags=["progress"])

MAX_NEW_SCANS = 8          # per request
MAX_NEW_INCIDENTS = 60

SCHEMA = """
create table if not exists session_scan_stats (
    session_id   uuid primary key references sessions(id) on delete cascade,
    version      text not null,          -- SCAN_VERSION + AOI zone fingerprint
    stats        jsonb not null,
    computed_at  timestamptz not null default now()
);
create table if not exists incident_summaries (
    result_id    bigint not null references rule_results(id) on delete cascade,
    idx          int not null,
    codes        text[] not null default '{}',
    computed_at  timestamptz not null default now(),
    primary key (result_id, idx)
)
"""


async def ensure_schema() -> None:
    try:
        async with db.acquire() as conn:
            for stmt in (s.strip() for s in SCHEMA.split(";")):
                if stmt:
                    await conn.execute(stmt)
    except Exception as e:      # sessions / rule_results not created yet
        log.warning("progress cache tables not created: %s", e)


def _j(v):
    return json.loads(v) if isinstance(v, str) else v


def _me(user: dict) -> set[str]:
    return {x.strip().lower() for x in (user.get("full_name"), user.get("username")) if x and x.strip()}


def _may_see(user: dict, pilot: str) -> bool:
    return user["role"] in ("instructor", "admin") or pilot.strip().lower() in _me(user)


async def _sessions(conn) -> list[dict]:
    rows = await conn.fetch("select id, name, pilot_name, lesson_id, mode, started_at, created_at from sessions")
    out = []
    for r in rows:
        d = dict(r)
        d["pilot"] = pilot_of(d)
        if d["pilot"]:
            out.append(d)
    out.sort(key=lambda s: s.get("started_at") or s.get("created_at"))
    return out


@router.get("/pilots")
async def pilots(user: dict = Depends(current_user)):
    async with db.acquire() as conn:
        sess = await _sessions(conn)
    by: dict[str, dict] = {}
    for s in sess:
        key = s["pilot"].lower()
        p = by.setdefault(key, {"pilot": s["pilot"], "flights": 0, "last": None})
        p["flights"] += 1
        p["last"] = (s.get("started_at") or s.get("created_at")).isoformat()
    out = sorted(by.values(), key=lambda p: p["pilot"].lower())
    return [p for p in out if _may_see(user, p["pilot"])]


async def _zone_fingerprint(conn) -> tuple[str, list[dict]]:
    zones = [dict(z) for z in await conn.fetch("select name, x1, y1, x2, y2 from aoi_zones order by id")]
    h = hashlib.sha1(json.dumps(zones, sort_keys=True, default=str).encode()).hexdigest()[:10]
    return f"{SCAN_VERSION}:{h}", zones


async def _scan(conn, sid: UUID, version: str, zones: list[dict], budget: list[int]) -> tuple[dict | None, bool]:
    """-> (stats or None, still_pending)"""
    row = await conn.fetchrow("select version, stats from session_scan_stats where session_id=$1", sid)
    if row and row["version"] == version:
        return _j(row["stats"]), False
    if budget[0] <= 0:
        return None, True
    budget[0] -= 1
    eye = await conn.fetch("select ts, aoi, gaze_point_x, gaze_point_y, blink from eye_tracking_data "
                           "where session_id=$1 order by ts", sid)
    if not eye:
        stats = {"version": SCAN_VERSION, "airborne": None, "approach": None, "eye_coverage": None}
    else:
        try:
            flight = await conn.fetch("select ts, phase from flight_data where session_id=$1 order by ts", sid)
        except Exception:      # no MAVIS phase column (CSV-only setup)
            flight = []
        stats = scan_stats([dict(e) for e in eye], [dict(f) for f in flight], zones)
    await conn.execute("insert into session_scan_stats (session_id, version, stats) values ($1,$2,$3) "
                       "on conflict (session_id) do update set version=$2, stats=$3, computed_at=now()",
                       sid, version, json.dumps(stats))
    return stats, False


async def _causes(conn, result_id: int, n_viol: int, budget: list[int]) -> tuple[dict[str, int], int]:
    """Finding codes over a failed rule's breaches -> ({code: count}, still_pending)."""
    rows = await conn.fetch("select idx, codes from incident_summaries where result_id=$1", result_id)
    have = {r["idx"]: list(r["codes"]) for r in rows}
    pending = 0
    for idx in range(n_viol):
        if idx in have:
            continue
        if budget[0] <= 0:
            pending += 1
            continue
        budget[0] -= 1
        try:
            a = await load_incident(result_id, idx)
            codes = [f["code"] for f in a["findings"]]
        except Exception as e:      # rule snapshot missing etc.: record it as analysed, with nothing found
            log.info("incident %s/%s not analysed: %s", result_id, idx, e)
            codes = []
        await conn.execute("insert into incident_summaries (result_id, idx, codes) values ($1,$2,$3) "
                           "on conflict do nothing", result_id, idx, codes)
        have[idx] = codes
    counts: dict[str, int] = {}
    for codes in have.values():
        for c in codes:
            counts[c] = counts.get(c, 0) + 1
    return counts, pending


async def _flight(conn, s: dict, version: str, zones: list[dict], scan_budget: list[int] | None,
                  inc_budget: list[int]):
    """scan_budget None: don't look at scan statistics at all."""
    ev = await conn.fetchrow("select id, strictness, created_at, signed_off_at from flight_evaluations "
                             "where session_id=$1 order by created_at desc limit 1", s["id"])
    rules, causes, breaches, pending = {}, {}, 0, 0
    if ev:
        for r in await conn.fetch("select id, title, severity, verdict, instructor_verdict, violations "
                                  "from rule_results where evaluation_id=$1 order by sort_order, id", ev["id"]):
            v = r["instructor_verdict"] or r["verdict"]
            rules[r["title"]] = v
            viol = _j(r["violations"]) or []
            if v == "fail" and viol:
                breaches += len(viol)
                c, p = await _causes(conn, r["id"], len(viol), inc_budget)
                pending += p
                for k, n in c.items():
                    causes[k] = causes.get(k, 0) + n
    scan = None
    if scan_budget is not None:
        scan, p = await _scan(conn, s["id"], version, zones, scan_budget)
        pending += int(p)
    ts = s.get("started_at") or s.get("created_at")
    return {"id": str(s["id"]), "label": flight_label(s), "at": ts.isoformat() if ts else None,
            "lesson_id": s.get("lesson_id"), "mode": s.get("mode"),
            "checked": bool(ev), "strictness": ev["strictness"] if ev else None,
            "signed_off": bool(ev and ev["signed_off_at"]),
            "rules": rules, "breaches": breaches, "causes": causes, "scan": scan}, pending


@router.get("/pilot")
async def pilot(name: str, user: dict = Depends(current_user)):
    if not _may_see(user, name):
        raise HTTPException(403, "You can only see your own progress")
    scan_budget, inc_budget = [MAX_NEW_SCANS], [MAX_NEW_INCIDENTS]
    async with db.acquire() as conn:
        sess = [s for s in await _sessions(conn) if s["pilot"].lower() == name.strip().lower()]
        if not sess:
            raise HTTPException(404, f"No flights for {name}")
        version, zones = await _zone_fingerprint(conn)
        flights, pending = [], 0
        for s in sess:
            f, p = await _flight(conn, s, version, zones, scan_budget, inc_budget)
            flights.append(f)
            pending += p
    checked = [f for f in flights if f["checked"]]
    return {"pilot": sess[-1]["pilot"], "flights": flights, "pending": pending,
            "rule_titles": sorted({t for f in checked for t in f["rules"]}),
            "insights": insights(checked), "causes": CAUSES, "cause_kind": CAUSE_KIND}


@router.get("/cohort")
async def cohort(user: dict = Depends(require_instructor), last: int = 5):
    """Each pilot's last `last` checked flights."""
    inc_budget = [MAX_NEW_INCIDENTS]
    async with db.acquire() as conn:
        sess = await _sessions(conn)
        version, zones = await _zone_fingerprint(conn)
        by_pilot: dict[str, list] = {}
        pending = 0
        for s in reversed(sess):                            # newest first
            lst = by_pilot.setdefault(s["pilot"].lower(), [])
            if len(lst) >= last:
                continue
            f, p = await _flight(conn, s, version, zones, None, inc_budget)   # scan stats aren't needed here
            pending += p
            if f["checked"]:
                lst.append(f)
    rules: dict[str, dict] = {}
    causes: dict[str, int] = {}
    flights = [f for lst in by_pilot.values() for f in lst]
    for f in flights:
        for t, v in f["rules"].items():
            r = rules.setdefault(t, {"title": t, "flights": 0, "failed": 0, "pilots_failed": set()})
            if v in ("pass", "fail"):
                r["flights"] += 1
            if v == "fail":
                r["failed"] += 1
        for k, n in f["causes"].items():
            if k in CAUSES:
                causes[k] = causes.get(k, 0) + n
    for p, lst in by_pilot.items():
        for f in lst:
            for t, v in f["rules"].items():
                if v == "fail":
                    rules[t]["pilots_failed"].add(p)
    rule_list = sorted(({**r, "fail_rate": round(r["failed"] / r["flights"], 3) if r["flights"] else None,
                         "pilots_failed": len(r["pilots_failed"])} for r in rules.values() if r["flights"]),
                       key=lambda r: (-(r["fail_rate"] or 0), -r["failed"]))
    return {"pilots": len([l for l in by_pilot.values() if l]), "flights": len(flights), "last": last,
            "rules": rule_list[:15], "pending": pending,
            "causes": sorted(({"code": k, "label": CAUSES[k], "kind": CAUSE_KIND[k], "count": n}
                              for k, n in causes.items()), key=lambda c: -c["count"])}
