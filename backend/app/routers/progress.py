"""The Progress page: how trainees are getting on across flights (post-flight only).

  GET /progress/pilots                       pilots with flights (a trainee sees only themselves)
  GET /progress/pilot?name=Alex              learning curves, scan per phase vs the expert flights, coaching,
                                             handling, plain-words notes (app/progress.py)
  GET /progress/cohort?last=5                instructors: the whole group -- most-broken rules, rules that
                                             matter most in practice, pilots side by side, which callouts help
  GET /progress/expert-flights               instructors: flights that can be marked as expert reference runs
  PUT /progress/expert-flights/{session_id}  instructors: {"expert": true|false}

Expensive parts are cached and filled in lazily, a bounded number per request:
  session_progress_stats  app/progress.session_stats() per flight (redone when the AOI zones change)
  incident_findings       the findings of each broken-rule moment (routers/incidents.py)
`pending` in a response = how many are still to be worked out (the page asks again).
"""
from __future__ import annotations

import hashlib
import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import db
from ..auth import current_user
from ..progress import (CAUSE_KIND, CAUSES, STATS_VERSION, coaching_summary, flight_label, insights,
                        learning_curve, phase_scan_table, pilot_of, rule_impact, session_stats)
from .incidents import cached_findings
from .rule_results import require_instructor

log = logging.getLogger(__name__)
router = APIRouter(prefix="/progress", tags=["progress"])

MAX_NEW_STATS = 6          # flights' statistics worked out per request
MAX_NEW_FINDINGS = 40      # broken-rule moments analysed per request

SCHEMA = """
create table if not exists session_progress_stats (
    session_id   uuid primary key references sessions(id) on delete cascade,
    version      text not null,          -- STATS_VERSION + AOI zone fingerprint
    stats        jsonb not null,
    computed_at  timestamptz not null default now()
);
create table if not exists progress_expert_flights (
    session_id   uuid primary key references sessions(id) on delete cascade,
    marked_by    text,
    marked_at    timestamptz not null default now()
)
"""

_FLIGHT = ("ts, airspeed_kt, altitude_ft, vertical_speed_fpm, bank_deg, pitch_deg, throttle_pct, "
           "elevator_position, rudder_position")
_FLIGHT_MAVIS = ", phase, aileron_position, flaps_deg, on_ground"


async def ensure_schema() -> None:
    try:
        async with db.acquire() as conn:
            for stmt in (s.strip() for s in SCHEMA.split(";")):
                if stmt:
                    await conn.execute(stmt)
    except Exception as e:      # sessions not created yet
        log.warning("progress tables not created: %s", e)


def _j(v):
    return json.loads(v) if isinstance(v, str) else v


def _me(user: dict) -> set[str]:
    return {x.strip().lower() for x in (user.get("full_name"), user.get("username")) if x and x.strip()}


def _may_see(user: dict, pilot: str) -> bool:
    return user["role"] in ("instructor", "admin") or pilot.strip().lower() in _me(user)


async def _sessions(conn) -> list[dict]:
    rows = await conn.fetch("select s.id, s.name, s.pilot_name, s.lesson_id, s.mode, s.started_at, s.created_at, "
                            "e.session_id is not null as expert from sessions s "
                            "left join progress_expert_flights e on e.session_id = s.id")
    out = []
    for r in rows:
        d = dict(r)
        d["pilot"] = pilot_of(d)
        if d["pilot"]:
            out.append(d)
    out.sort(key=lambda s: s.get("started_at") or s.get("created_at"))
    return out


async def _version(conn) -> tuple[str, list[dict]]:
    zones = [dict(z) for z in await conn.fetch("select name, x1, y1, x2, y2 from aoi_zones order by id")]
    h = hashlib.sha1(json.dumps(zones, sort_keys=True, default=str).encode()).hexdigest()[:10]
    return f"{STATS_VERSION}:{h}", zones


async def _stats(conn, sid: UUID, version: str, zones: list[dict], budget: list[int]) -> tuple[dict | None, bool]:
    """-> (session_stats() or None, still_pending)"""
    row = await conn.fetchrow("select version, stats from session_progress_stats where session_id=$1", sid)
    if row and row["version"] == version:
        return _j(row["stats"]), False
    if budget[0] <= 0:
        return None, True
    budget[0] -= 1
    try:
        flight = await conn.fetch(f"select {_FLIGHT}{_FLIGHT_MAVIS} from flight_data where session_id=$1 order by ts", sid)
    except Exception:          # CSV-only setup without MAVIS's columns
        flight = await conn.fetch(f"select {_FLIGHT} from flight_data where session_id=$1 order by ts", sid)
    eye = await conn.fetch("select ts, aoi, gaze_point_x, gaze_point_y, blink from eye_tracking_data "
                           "where session_id=$1 order by ts", sid)
    calls = await conn.fetch("select ts, code, severity, who, phase, data from coaching_events "
                             "where session_id=$1 order by ts", sid)
    stats = session_stats([dict(r) for r in flight], [dict(e) for e in eye], zones,
                          [{**dict(c), "data": _j(c["data"])} for c in calls])
    await conn.execute("insert into session_progress_stats (session_id, version, stats) values ($1,$2,$3) "
                       "on conflict (session_id) do update set version=$2, stats=$3, computed_at=now()",
                       sid, version, json.dumps(stats))
    return stats, False


async def _rules(conn, sid: UUID, budget: list[int] | None) -> tuple[dict | None, int]:
    """The flight's latest rule check -> ({signed_off, rules: {title: {verdict, severity, breaches, breach_t}},
    causes: {code: n}, breaches}, pending) or (None, 0) if it was never checked.
    budget None: don't look at causes."""
    ev = await conn.fetchrow("select id, signed_off_at from flight_evaluations where session_id=$1 "
                             "order by created_at desc limit 1", sid)
    if ev is None:
        return None, 0
    rules, causes, breaches, pending = {}, {}, 0, 0
    for r in await conn.fetch("select id, title, severity, verdict, instructor_verdict, violations "
                              "from rule_results where evaluation_id=$1 order by sort_order, id", ev["id"]):
        v = r["instructor_verdict"] or r["verdict"]
        viol = (_j(r["violations"]) or []) if v == "fail" else []
        rules[r["title"]] = {"verdict": v, "severity": r["severity"], "breaches": len(viol) if v == "fail" else 0,
                             "breach_t": [x.get("t0") for x in viol if x.get("t0") is not None]}
        breaches += len(viol)
        if budget is None:
            continue
        for idx in range(len(viol)):
            found, p = await cached_findings(conn, r["id"], idx, budget)
            pending += int(p)
            for code in {f["code"] for f in found or []}:
                causes[code] = causes.get(code, 0) + 1
    return {"signed_off": bool(ev["signed_off_at"]), "rules": rules, "causes": causes, "breaches": breaches}, pending


def _compact(st: dict | None) -> dict | None:
    """What the page charts from a flight's statistics (callouts summarised)."""
    if not st:
        return None
    calls = st.get("callouts") or []
    judged = [c for c in calls if c["helped"] is not None]
    reacts = sorted(c["reaction_s"] for c in calls if c["reaction_s"] is not None)
    return {"phases": st.get("phases") or {}, "approach": st.get("approach"), "landing": st.get("landing"),
            "eye_coverage": st.get("eye_coverage"),
            "airborne_outside": ((st.get("airborne_scan") or {}).get("share") or {}).get("outside"),
            "callouts": {"count": len(calls), "judged": len(judged), "helped": sum(1 for c in judged if c["helped"]),
                         "ignored": sum(1 for c in calls if c["reaction_s"] is None and not c["helped"]),
                         "median_reaction_s": reacts[len(reacts) // 2] if reacts else None}}


async def _expert_table(conn, version: str, zones: list[dict], budget: list[int]) -> tuple[dict, int, int]:
    """-> (phase_scan_table of the expert flights, how many expert flights, pending)"""
    ids = [r["session_id"] for r in await conn.fetch("select session_id from progress_expert_flights")]
    got, pending = [], 0
    for sid in ids:
        st, p = await _stats(conn, sid, version, zones, budget)
        pending += int(p)
        if st:
            got.append(st)
    return phase_scan_table(got), len(ids), pending


@router.get("/pilots")
async def pilots(user: dict = Depends(current_user)):
    async with db.acquire() as conn:
        sess = await _sessions(conn)
    by: dict[str, dict] = {}
    for s in sess:
        if s["expert"]:
            continue
        p = by.setdefault(s["pilot"].lower(), {"pilot": s["pilot"], "flights": 0, "last": None})
        p["flights"] += 1
        p["last"] = (s.get("started_at") or s.get("created_at")).isoformat()
    out = sorted(by.values(), key=lambda p: p["pilot"].lower())
    return [p for p in out if _may_see(user, p["pilot"])]


@router.get("/pilot")
async def pilot(name: str, user: dict = Depends(current_user)):
    if not _may_see(user, name):
        raise HTTPException(403, "You can only see your own progress")
    stat_budget, find_budget, pending = [MAX_NEW_STATS], [MAX_NEW_FINDINGS], 0
    async with db.acquire() as conn:
        sess = [s for s in await _sessions(conn) if s["pilot"].lower() == name.strip().lower() and not s["expert"]]
        if not sess:
            raise HTTPException(404, f"No flights for {name}")
        version, zones = await _version(conn)
        flights, stats = [], []
        for s in reversed(sess):                      # newest first, so the latest flights fill in first
            rules, p = await _rules(conn, s["id"], find_budget)
            st, p2 = await _stats(conn, s["id"], version, zones, stat_budget)
            pending += p + int(p2)
            ts = s.get("started_at") or s.get("created_at")
            flights.append({"id": str(s["id"]), "label": flight_label(s), "at": ts.isoformat() if ts else None,
                            "lesson_id": s.get("lesson_id"), "checked": rules is not None,
                            **(rules or {"signed_off": False, "rules": {}, "causes": {}, "breaches": 0}),
                            "stats": _compact(st)})
            stats.append(st)
        flights.reverse()
        stats.reverse()
        expert, n_expert, p = await _expert_table(conn, version, zones, stat_budget)
        pending += p
    checked = [f for f in flights if f["checked"]]
    for f in flights:
        for r in f["rules"].values():
            r.pop("breach_t", None)
    calls = [c for st in stats if st for c in st.get("callouts") or []]
    return {"pilot": sess[-1]["pilot"], "flights": flights, "pending": pending,
            "learning_curve": learning_curve(checked),
            "phase_table": {"mine": phase_scan_table([s for s in stats if s][-3:]), "expert": expert,
                            "expert_flights": n_expert},
            "coaching": coaching_summary(calls),
            "insights": insights(checked, stats, expert if n_expert else None),
            "causes": CAUSES, "cause_kind": CAUSE_KIND}


@router.get("/cohort")
async def cohort(user: dict = Depends(require_instructor), last: int = 5):
    """Each pilot's last `last` checked flights."""
    stat_budget, find_budget, pending = [MAX_NEW_STATS], [MAX_NEW_FINDINGS], 0
    async with db.acquire() as conn:
        sess = await _sessions(conn)
        version, zones = await _version(conn)
        by_pilot: dict[str, dict] = {}
        for s in reversed(sess):                            # newest first
            if s["expert"]:
                continue
            p = by_pilot.setdefault(s["pilot"].lower(), {"pilot": s["pilot"], "flights": []})
            if len(p["flights"]) >= last:
                continue
            rules, n = await _rules(conn, s["id"], find_budget)
            pending += n
            if rules is None:
                continue
            st, n = await _stats(conn, s["id"], version, zones, stat_budget)
            pending += int(n)
            p["flights"].append({**rules, "pilot": s["pilot"], "stats": st})
        expert, n_expert, n = await _expert_table(conn, version, zones, stat_budget)
        pending += n
    flights = [f for p in by_pilot.values() for f in p["flights"]]
    causes: dict[str, int] = {}
    for f in flights:
        for k, n in f["causes"].items():
            if k in CAUSES:
                causes[k] = causes.get(k, 0) + n
    pilots_out = []
    for p in by_pilot.values():
        fs = list(reversed(p["flights"]))                  # oldest first
        if not fs:
            continue
        sts = [f["stats"] for f in fs if f["stats"]]
        table = phase_scan_table(sts)
        land = [st["landing"]["vs_fpm"] for st in sts if (st.get("landing") or {}).get("vs_fpm") is not None]
        calls = [c for st in sts for c in st.get("callouts") or []]
        judged = [c for c in calls if c["helped"] is not None]
        top = max(((k, n) for f in fs for k, n in f["causes"].items() if k in CAUSES), key=lambda x: x[1], default=None)
        half = len(fs) // 2
        pilots_out.append({
            "pilot": p["pilot"], "flights": len(fs),
            "breaches_per_flight": round(sum(f["breaches"] for f in fs) / len(fs), 1),
            "breaches_trend": (round(sum(f["breaches"] for f in fs[-half:]) / half - sum(f["breaches"] for f in fs[:half]) / half, 1)
                               if half else None),
            "top_cause": CAUSES[top[0]] if top else None,
            "final_airspeed_every_s": (table.get("FINAL") or {}).get("airspeed_every_s"),
            "outside": _avg([((st.get("airborne_scan") or {}).get("share") or {}).get("outside") for st in sts]),
            "final_ias_sd": _avg([(st.get("approach") or {}).get("ias_sd") for st in sts]),
            "touchdown_fpm": _avg(land),
            "callouts_helped": round(sum(1 for c in judged if c["helped"]) / len(judged), 2) if judged else None,
        })
    pilots_out.sort(key=lambda r: r["pilot"].lower())
    impact = rule_impact(flights)
    all_calls = [c for f in flights if f["stats"] for c in f["stats"].get("callouts") or []]
    return {"pilots": pilots_out, "flights": len(flights), "last": last, "pending": pending,
            "rules": sorted(impact, key=lambda r: (-r["fail_rate"], -r["broken"]))[:15],
            "impact": impact[:10], "coaching": coaching_summary(all_calls),
            "expert": expert, "expert_flights": n_expert,
            "causes": sorted(({"code": c, "label": CAUSES[c], "kind": CAUSE_KIND[c], "count": n}
                              for c, n in causes.items()), key=lambda x: -x["count"])}


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 2) if xs else None


@router.get("/expert-flights")
async def expert_flights(user: dict = Depends(require_instructor)):
    async with db.acquire() as conn:
        sess = await _sessions(conn)
    return [{"id": str(s["id"]), "label": flight_label(s), "pilot": s["pilot"], "expert": s["expert"]}
            for s in reversed(sess)]


class ExpertIn(BaseModel):
    expert: bool


@router.put("/expert-flights/{session_id}")
async def set_expert(session_id: UUID, body: ExpertIn, user: dict = Depends(require_instructor)):
    async with db.acquire() as conn:
        if not await conn.fetchval("select 1 from sessions where id=$1", session_id):
            raise HTTPException(404, "No such session")
        if body.expert:
            await conn.execute("insert into progress_expert_flights (session_id, marked_by) values ($1,$2) "
                               "on conflict do nothing", session_id, user.get("username"))
        else:
            await conn.execute("delete from progress_expert_flights where session_id=$1", session_id)
    return {"id": str(session_id), "expert": body.expert}
