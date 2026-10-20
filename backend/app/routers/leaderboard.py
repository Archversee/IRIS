"""
The leaderboard -- every run posted from MAVIS, kept here in Postgres
(leaderboard_entries). MAVIS itself writes nothing to disk: it publishes each
posted run in its live state and the bridge stores it (save_entry below), so
this table is the one and only all-time board.

  GET    /leaderboard?mode=visitor|trainee|pilot&q=&page=1&page_size=20
  DELETE /leaderboard/{id}   signed in: the pilot who flew it (user_id), or an admin

Deleting only marks the row (deleted_at): MAVIS re-sends every run posted
since it started, and a removed row would just be saved again.

One board per mode. Rank is by score (the run against the planned circuit),
fewer retries breaking ties, then the earlier run. It is worked out over the
mode's whole board *before* the search is applied, so a pilot found by
search keeps their real place. The search matches name, circuit or aircraft.
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import db
from ..auth import current_user
from ..catalog import lesson_title

log = logging.getLogger(__name__)

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])

MODES = ("visitor", "trainee", "pilot")
_LEGACY_MODE = {"casual": "visitor", "serious": "pilot"}

# Pilot shown as given + family name when MAVIS was told them, else the posted name.
_PILOT = "coalesce(nullif(trim(concat_ws(' ', given_name, family_name)), ''), display_name)"
_SEARCH = (f"({_PILOT} ilike $q or display_name ilike $q"
           " or coalesce(circuit, lesson_id, '') ilike $q or coalesce(aircraft, '') ilike $q)")


async def ensure_schema() -> None:
    """Table + the columns added for this board (also in
    db/schema_mavis_integration.sql), applied at startup like the other
    additive schema pieces. Older rows get their circuit name filled in from
    the lesson catalog, and legacy mode names mapped to today's."""
    async with db.acquire() as conn:
        await conn.execute("""
            create table if not exists leaderboard_entries (
                id bigint generated always as identity primary key,
                session_id uuid references sessions(id) on delete set null,
                display_name text not null, given_name text, family_name text,
                mode text, lesson_id text, scored_at timestamptz not null,
                track_score integer, speed_score integer, altitude_score integer,
                landing_score integer, smoothness_score integer,
                total integer, grade text, retries integer, capped boolean)""")
        for col in ("circuit text", "aircraft text", "expert_total integer", "expert_reference text",
                    "user_id uuid references users(id) on delete set null", "deleted_at timestamptz"):
            await conn.execute(f"alter table leaderboard_entries add column if not exists {col}")
        await conn.execute("create index if not exists leaderboard_entries_rank "
                           "on leaderboard_entries (total desc, retries, scored_at)")
        for old, new in _LEGACY_MODE.items():
            await conn.execute("update leaderboard_entries set mode = $2 where mode = $1", old, new)
        missing = await conn.fetch("select distinct lesson_id from leaderboard_entries "
                                   "where circuit is null and lesson_id is not null")
        for r in missing:
            title = lesson_title(r["lesson_id"])
            if title:
                await conn.execute("update leaderboard_entries set circuit = $2 "
                                   "where circuit is null and lesson_id = $1", r["lesson_id"], title)


async def save_entry(e: dict) -> None:
    """Store one posted run (MAVIS's ScoreCard.to_entry()). Re-sent runs --
    MAVIS republishes everything posted since it started -- are skipped."""
    scores = e.get("scores") or {}
    ts = e.get("ts")
    scored_at = (datetime.fromtimestamp(float(ts), tz=timezone.utc)
                 if isinstance(ts, (int, float)) else datetime.now(timezone.utc))
    mode = _LEGACY_MODE.get(e.get("mode") or "", e.get("mode"))
    async with db.acquire() as conn:
        await conn.execute(
            """
            insert into leaderboard_entries
                (session_id, display_name, given_name, family_name, mode, lesson_id, circuit, aircraft,
                 scored_at, track_score, speed_score, altitude_score, landing_score, smoothness_score,
                 total, grade, retries, capped, expert_total, expert_reference, user_id)
            select (select id from sessions where id::text = $1),
                   $2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,
                   (select id from users where id::text = $21)
            where not exists (
                select 1 from leaderboard_entries
                where display_name = $2 and scored_at = $9 and lesson_id is not distinct from $6
            )
            """,
            e.get("session_id") or "", e.get("name") or "Anonymous", e.get("given"), e.get("family"),
            mode, e.get("lesson"), lesson_title(e.get("lesson")) or e.get("lesson"), e.get("aircraft"),
            scored_at, scores.get("track"), scores.get("speed"), scores.get("altitude"),
            scores.get("landing"), scores.get("smoothness"), e.get("total"), e.get("grade"),
            e.get("retries"), e.get("capped"), e.get("expert_total"), e.get("expert_reference"),
            e.get("user_id") or "",
        )


@router.get("")
async def leaderboard(
    mode: str = Query("visitor", pattern="^(visitor|trainee|pilot)$"),
    q: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    args: list = []

    def arg(v) -> str:
        args.append(v)
        return f"${len(args)}"

    mode_sql = f"where mode = {arg(mode)} and deleted_at is null"
    search_sql = ""
    q = q.strip()
    if q:
        like = arg("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        search_sql = "where " + _SEARCH.replace("$q", like)
    limit, offset = arg(page_size), arg((page - 1) * page_size)
    sql = f"""
        with ranked as (
            select *, {_PILOT} as pilot,
                   row_number() over (order by total desc nulls last, retries asc nulls last,
                                               scored_at asc, id asc) as rank
              from leaderboard_entries {mode_sql}
        )
        select rank, id, session_id, user_id, pilot, coalesce(circuit, lesson_id) as circuit, aircraft,
               total, grade, expert_total, expert_reference, mode, retries, capped, scored_at,
               count(*) over () as matches
          from ranked {search_sql}
         order by rank
         limit {limit} offset {offset}
    """
    async with db.acquire() as conn:
        rows = [dict(r) for r in await conn.fetch(sql, *args)]
        matches = rows[0]["matches"] if rows else (
            # past the last page: still report how many there are
            await conn.fetchval(f"select count(*) from (select *, {_PILOT} as pilot "
                                f"from leaderboard_entries {mode_sql}) ranked {search_sql}", *args[:-2])
        )
    for r in rows:
        r.pop("matches", None)
    return {"rows": rows, "total": matches, "page": page, "page_size": page_size}


@router.delete("/{entry_id}", status_code=204)
async def delete_entry(entry_id: int, user: dict = Depends(current_user)):
    async with db.acquire() as conn:
        owner = await conn.fetchrow(
            "select user_id from leaderboard_entries where id = $1 and deleted_at is null", entry_id)
        if owner is None:
            raise HTTPException(404, "No such leaderboard entry")
        if user["role"] != "admin" and owner["user_id"] != user["id"]:
            raise HTTPException(403, "You can only delete your own scores")
        await conn.execute("update leaderboard_entries set deleted_at = now() where id = $1", entry_id)
