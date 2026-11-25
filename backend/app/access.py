"""Who may see which flight.

A flight belongs to the account that flew it (sessions.pilot_user_id):

  trainee     their own flights
  instructor  their assigned trainees' flights, the ones they flew themselves, and
              flights that belong to no account (a guest, a typed-in name), so
              somebody can give those to a trainee
  admin       every flight

The flight MAVIS is on right now can be watched (Live Monitor) and ended or
discarded by any signed-in account: the tablet and the sim PC drive the same
flight, and may not be signed in as the same person.

LiveAdapter.py has no account. What it calls stays open: GET /sessions/{id}
(only whether it exists and has ended, without a login), POST .../recordings,
POST .../eye-clock and the stream sockets.
"""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import Depends, HTTPException

from . import db
from .auth import current_user, optional_user

log = logging.getLogger(__name__)

NOT_YOURS = "This flight belongs to another pilot."
# an account's name as it is shown; {u} = the users table's alias
ACCOUNT_NAME = "coalesce(nullif(trim({u}.full_name), ''), {u}.username)"


async def ensure_schema() -> None:
    """sessions.pilot_user_id. When it is first added, flights already there are
    given to the account their pilot's name means -- only where exactly one
    account has that name; the rest stay without one."""
    from .progress import pilot_of
    try:
        async with db.acquire() as conn:
            async with conn.transaction():
                if await conn.fetchval("select 1 from information_schema.columns where table_schema = current_schema() "
                                       "and table_name = 'sessions' and column_name = 'pilot_user_id'"):
                    return
                await conn.execute("alter table sessions add column pilot_user_id uuid "
                                   "references users(id) on delete set null")
                await conn.execute("create index if not exists sessions_pilot_user on sessions (pilot_user_id)")
                by_name: dict[str, set] = {}
                for u in await conn.fetch("select id, username, full_name from users"):
                    for n in (u["username"], u["full_name"]):
                        if n and n.strip():
                            by_name.setdefault(n.strip().lower(), set()).add(u["id"])
                given = 0
                for s in await conn.fetch("select id, name, pilot_name from sessions"):
                    ids = by_name.get((pilot_of(dict(s)) or "").strip().lower(), set())
                    if len(ids) == 1:
                        await conn.execute("update sessions set pilot_user_id = $2 where id = $1", s["id"], next(iter(ids)))
                        given += 1
                log.info("sessions.pilot_user_id added: %d existing flights given to their pilot's account", given)
    except Exception:
        log.exception("sessions.pilot_user_id couldn't be added -- flights can't be listed until this is fixed")


def visible_sql(user: dict, arg: str, s: str = "s") -> str:
    """SQL condition: may this user see the session `s`? `arg` ($n) is the user's id."""
    if user["role"] == "admin":
        return f"{arg}::uuid is not null"
    mine = f"{s}.pilot_user_id = {arg}::uuid"
    if user["role"] == "instructor":
        return (f"({mine} or {s}.pilot_user_id is null or exists (select 1 from users vp "
                f"where vp.id = {s}.pilot_user_id and vp.instructor_id = {arg}::uuid))")
    return f"({mine})"


def is_current(session_id) -> bool:
    """Is this the session MAVIS is on?"""
    from . import mavis_bridge
    return mavis_bridge.current_session_id() == str(session_id)


async def check(session_id: UUID, user: dict, *, live_ok: bool = False) -> None:
    """404 / 403 unless the user may see it. live_ok: also while it's being flown."""
    async with db.acquire() as conn:
        row = await conn.fetchrow(f"select s.ended_at, {visible_sql(user, '$2')} as ok from sessions s where s.id = $1",
                                  session_id, user["id"])
    if row is None:
        raise HTTPException(404, "Session not found")
    if row["ok"] or (live_ok and row["ended_at"] is None and is_current(session_id)):
        return
    raise HTTPException(403, NOT_YOURS)


async def viewer(session_id: UUID, user: dict = Depends(current_user)) -> dict:
    """Dependency: signed in, and may read this session."""
    await check(session_id, user, live_ok=True)
    return user


async def editor(session_id: UUID, user: dict = Depends(current_user)) -> dict:
    """Dependency: signed in, and this session is one of theirs to change."""
    await check(session_id, user)
    return user


async def uploader(session_id: UUID, user: dict | None = Depends(optional_user)) -> None:
    """Dependency: as editor, but the sim PC's own adapter scripts have no account."""
    if user is not None:
        await check(session_id, user)


async def check_result(result_id: int, user: dict) -> None:
    """The rule result's session (404 is left to the caller)."""
    async with db.acquire() as conn:
        sid = await conn.fetchval("select session_id from rule_results where id = $1", result_id)
    if sid is not None:
        await check(sid, user, live_ok=True)


async def pilots_for(conn, user: dict) -> list[dict]:
    """The trainees this user can fly / save a flight for."""
    where, args = "role = 'trainee'", []
    if user["role"] == "instructor":
        where, args = "role = 'trainee' and instructor_id = $1", [user["id"]]
    elif user["role"] != "admin":
        where, args = "id = $1", [user["id"]]
    return [dict(r) for r in await conn.fetch(
        f"select id, username, full_name from users where {where} order by full_name, username", *args)]


async def set_pilot(conn, fields: dict, user: dict, current: dict | None) -> None:
    """Settle who the pilot is in a session's create / update `fields`.
    Trainee: always themselves, whatever was sent. Instructor: one of their
    trainees, themselves, or a typed name (no account). Admin: anybody."""
    if user["role"] == "trainee":
        fields.pop("pilot_name", None)
        fields.pop("pilot_user_id", None)
        if current is None:
            fields["pilot_user_id"] = user["id"]
            fields["pilot_name"] = user.get("full_name") or user["username"]
        return
    uid = fields.get("pilot_user_id")
    if uid is not None:
        if current is None or uid != current["pilot_user_id"]:
            allowed = user["role"] == "admin" or uid == user["id"] or any(
                p["id"] == uid for p in await pilots_for(conn, user))
            if not allowed:
                raise HTTPException(403, "You can only choose one of your own trainees.")
        name = await conn.fetchval(f"select {ACCOUNT_NAME.format(u='u')} from users u where u.id = $1", uid)
        if name is None:
            raise HTTPException(400, "That account no longer exists.")
        fields["pilot_name"] = name
    elif "pilot_name" in fields and "pilot_user_id" not in fields and current is not None:
        # a name alone, changed: no longer the account's flight
        if (fields["pilot_name"] or "").strip().lower() != (current["pilot_name"] or "").strip().lower():
            fields["pilot_user_id"] = None
