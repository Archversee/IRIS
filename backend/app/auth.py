"""Simple account/login support: salted PBKDF2 password hashes in `users`,
random bearer tokens in `auth_tokens` (see db/schema_auth.sql).

Deliberately small -- no OAuth/JWT. The frontend sends the token as
`Authorization: Bearer <token>`; routes that need a signed-in user depend
on `current_user`, admin-only routes on `require_admin`. The rest of the
API (MAVIS bridge, LiveAdapter, ingest) is unchanged and not gated.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, Header, HTTPException

from . import db
from .config import settings

log = logging.getLogger(__name__)

ROLES = ("trainee", "instructor", "admin")
TOKEN_TTL = timedelta(days=7)
_PBKDF2_ITER = 200_000
_SCHEMA_SQL = Path(__file__).resolve().parents[2] / "db" / "schema_auth.sql"

# Columns safe to send to the browser (never password_hash).
USER_FIELDS = "id, username, full_name, email, role, instructor_id, created_at"


# ---------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITER)
    return f"pbkdf2${_PBKDF2_ITER}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------
# Startup: tables + first admin
# ---------------------------------------------------------------------
async def ensure_schema() -> None:
    """Create the auth tables if missing, and a first admin account if
    there's no admin yet (credentials from settings; change the password
    from the Profile page after first login)."""
    sql = "\n".join(l for l in _SCHEMA_SQL.read_text(encoding="utf-8").splitlines()
                    if not l.lstrip().startswith("--"))
    async with db.acquire() as conn:
        for stmt in (s.strip() for s in sql.split(";")):
            if stmt:
                await conn.execute(stmt)
        has_admin = await conn.fetchval("select 1 from users where role = 'admin' limit 1")
        if not has_admin:
            await conn.execute(
                "insert into users (username, password_hash, full_name, role) values ($1, $2, $3, 'admin') "
                "on conflict do nothing",
                settings.admin_username, hash_password(settings.admin_password), "Administrator")
            log.warning("auth: created first admin account '%s' -- sign in and change its password",
                        settings.admin_username)


# ---------------------------------------------------------------------
# Tokens + request dependencies
# ---------------------------------------------------------------------
async def issue_token(user_id) -> str:
    token = secrets.token_urlsafe(32)
    async with db.acquire() as conn:
        await conn.execute("delete from auth_tokens where expires_at < now()")
        await conn.execute(
            "insert into auth_tokens (token, user_id, expires_at) values ($1, $2, $3)",
            token, user_id, datetime.now(timezone.utc) + TOKEN_TTL)
    return token


def _bearer(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return None


async def current_user(authorization: str | None = Header(default=None)) -> dict:
    token = _bearer(authorization)
    if not token:
        raise HTTPException(401, "Not signed in")
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            f"select {', '.join('u.' + c.strip() for c in USER_FIELDS.split(','))} "
            "from auth_tokens t join users u on u.id = t.user_id "
            "where t.token = $1 and t.expires_at > now()", token)
    if row is None:
        raise HTTPException(401, "Session expired -- please sign in again")
    return dict(row)


async def current_token(authorization: str | None = Header(default=None)) -> str | None:
    return _bearer(authorization)


async def require_admin(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(403, "Administrators only")
    return user
