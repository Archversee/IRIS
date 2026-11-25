"""Sessions that have been deleted (Finish & discard, or the Sessions page).

Deleting a session doesn't stop its producers: MAVIS keeps publishing the same
session_id until the next Start, and LiveAdapter.py keeps forwarding gaze to it.
Their rows would all violate the sessions foreign key, so the writers
(mavis_bridge, routers/stream.py) check here and drop them instead.

Also kept in the database: after a backend restart mavis_bridge would otherwise
take the id MAVIS still publishes for a new session and create it again.
"""
import logging

from . import db

log = logging.getLogger(__name__)

_ids: set[str] = set()


def add(session_id) -> None:
    _ids.add(str(session_id))


def discard(session_id) -> None:
    _ids.discard(str(session_id))


def contains(session_id) -> bool:
    return str(session_id) in _ids


async def remember(session_id) -> None:
    """Keep a deletion across restarts. Best-effort: the session is already gone."""
    try:
        async with db.acquire() as conn:
            await conn.execute("insert into deleted_sessions (session_id) values ($1) on conflict do nothing",
                               session_id)
    except Exception as e:
        log.warning("couldn't record that session %s was deleted: %s", session_id, e)


async def ensure_schema() -> None:
    """Create the table and load it. Before mavis_bridge starts (main.py)."""
    try:
        async with db.acquire() as conn:
            await conn.execute("create table if not exists deleted_sessions ("
                               "session_id uuid primary key, deleted_at timestamptz not null default now())")
            _ids.update(str(r["session_id"]) for r in await conn.fetch("select session_id from deleted_sessions"))
    except Exception as e:
        log.warning("couldn't load the deleted sessions: %s", e)
