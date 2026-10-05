"""Login, own profile, and (admin-only) account management.

  POST   /auth/login            {username, password} -> {token, user}
  POST   /auth/logout
  GET    /auth/me               signed-in user + their instructor / trainees
  PATCH  /auth/me               {full_name?, email?}
  POST   /auth/me/password      {current_password, new_password}

  GET    /users                 admin: every account
  POST   /users                 admin: create
  PATCH  /users/{id}            admin: edit details / role / instructor / reset password
  DELETE /users/{id}            admin: remove

Rules: only admins create/remove accounts; a trainee has at most one
instructor (users.instructor_id), an instructor can have many trainees.
"""
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..auth import (ROLES, USER_FIELDS, current_token, current_user, hash_password,
                    issue_token, require_admin, verify_password)

router = APIRouter(tags=["accounts"])

MIN_PASSWORD = 4  # kept low on purpose -- simple local system


class LoginBody(BaseModel):
    username: str
    password: str


class ProfileUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=200)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=60)
    password: str
    full_name: str = Field(default="", max_length=120)
    email: str | None = Field(default=None, max_length=200)
    role: str
    instructor_id: UUID | None = None


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=200)
    role: str | None = None
    instructor_id: UUID | None = None
    clear_instructor: bool = False   # set instructor_id to null (None alone means "unchanged")
    password: str | None = None      # admin password reset


def _check_password(pw: str) -> None:
    if len(pw or "") < MIN_PASSWORD:
        raise HTTPException(400, f"Password must be at least {MIN_PASSWORD} characters")


async def _check_instructor(conn, instructor_id: UUID | None) -> None:
    if instructor_id is None:
        return
    role = await conn.fetchval("select role from users where id = $1", instructor_id)
    if role != "instructor":
        raise HTTPException(400, "Assigned instructor must be an account with the Instructor role")


async def _profile(conn, user_id) -> dict:
    """User row plus who they're linked to, for /auth/me and the admin list."""
    u = dict(await conn.fetchrow(f"select {USER_FIELDS} from users where id = $1", user_id))
    u["instructor"] = None
    u["trainees"] = []
    if u["instructor_id"]:
        r = await conn.fetchrow("select id, username, full_name from users where id = $1", u["instructor_id"])
        u["instructor"] = dict(r) if r else None
    if u["role"] == "instructor":
        rows = await conn.fetch(
            "select id, username, full_name from users where instructor_id = $1 order by full_name, username", user_id)
        u["trainees"] = [dict(r) for r in rows]
    return u


# ---------------------------------------------------------------------
# Signing in / own profile
# ---------------------------------------------------------------------
@router.post("/auth/login")
async def login(body: LoginBody):
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "select id, password_hash from users where lower(username) = lower($1)", body.username.strip())
    if row is None or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(401, "Incorrect username or password")
    token = await issue_token(row["id"])
    async with db.acquire() as conn:
        return {"token": token, "user": await _profile(conn, row["id"])}


@router.post("/auth/logout", status_code=204)
async def logout(token: str | None = Depends(current_token)):
    if token:
        async with db.acquire() as conn:
            await conn.execute("delete from auth_tokens where token = $1", token)


@router.get("/auth/me")
async def me(user: dict = Depends(current_user)):
    async with db.acquire() as conn:
        return await _profile(conn, user["id"])


@router.patch("/auth/me")
async def update_me(body: ProfileUpdate, user: dict = Depends(current_user)):
    fields = body.model_dump(exclude_unset=True)
    async with db.acquire() as conn:
        if fields:
            sets = ", ".join(f"{k} = ${i}" for i, k in enumerate(fields, start=2))
            await conn.execute(f"update users set {sets} where id = $1", user["id"], *fields.values())
        return await _profile(conn, user["id"])


@router.post("/auth/me/password", status_code=204)
async def change_password(body: PasswordChange, user: dict = Depends(current_user)):
    _check_password(body.new_password)
    async with db.acquire() as conn:
        stored = await conn.fetchval("select password_hash from users where id = $1", user["id"])
        if not verify_password(body.current_password, stored):
            raise HTTPException(400, "Current password is incorrect")
        await conn.execute("update users set password_hash = $2 where id = $1",
                           user["id"], hash_password(body.new_password))


# ---------------------------------------------------------------------
# Admin: account management
# ---------------------------------------------------------------------
@router.get("/users")
async def list_users(_: dict = Depends(require_admin)):
    async with db.acquire() as conn:
        ids = await conn.fetch("select id from users order by role, full_name, username")
        return [await _profile(conn, r["id"]) for r in ids]


@router.post("/users", status_code=201)
async def create_user(body: UserCreate, _: dict = Depends(require_admin)):
    if body.role not in ROLES:
        raise HTTPException(400, f"Role must be one of {', '.join(ROLES)}")
    _check_password(body.password)
    instructor_id = body.instructor_id if body.role == "trainee" else None
    async with db.acquire() as conn:
        await _check_instructor(conn, instructor_id)
        try:
            uid = await conn.fetchval(
                "insert into users (username, password_hash, full_name, email, role, instructor_id) "
                "values ($1, $2, $3, $4, $5, $6) returning id",
                body.username.strip(), hash_password(body.password), body.full_name.strip(),
                (body.email or "").strip() or None, body.role, instructor_id)
        except asyncpg.UniqueViolationError:
            raise HTTPException(409, f"Username '{body.username}' is already taken")
        return await _profile(conn, uid)


@router.patch("/users/{user_id}")
async def update_user(user_id: UUID, body: UserUpdate, admin: dict = Depends(require_admin)):
    async with db.acquire() as conn:
        async with conn.transaction():
            target = await conn.fetchrow("select id, role from users where id = $1", user_id)
            if target is None:
                raise HTTPException(404, "Account not found")
            role = body.role or target["role"]
            if role not in ROLES:
                raise HTTPException(400, f"Role must be one of {', '.join(ROLES)}")
            if target["role"] == "admin" and role != "admin":
                others = await conn.fetchval("select count(*) from users where role = 'admin' and id <> $1", user_id)
                if not others:
                    raise HTTPException(400, "Can't change the last administrator's role")

            sets, vals = [], []
            def put(col, val):
                vals.append(val)
                sets.append(f"{col} = ${len(vals) + 1}")

            if body.full_name is not None:
                put("full_name", body.full_name.strip())
            if body.email is not None:
                put("email", body.email.strip() or None)
            if body.role is not None:
                put("role", role)
            if body.password:
                _check_password(body.password)
                put("password_hash", hash_password(body.password))
            if role != "trainee":
                put("instructor_id", None)              # only trainees have an instructor
            elif body.clear_instructor:
                put("instructor_id", None)
            elif body.instructor_id is not None:
                await _check_instructor(conn, body.instructor_id)
                put("instructor_id", body.instructor_id)
            if sets:
                await conn.execute(f"update users set {', '.join(sets)} where id = $1", user_id, *vals)
            # No longer an instructor -> their trainees become unassigned.
            if target["role"] == "instructor" and role != "instructor":
                await conn.execute("update users set instructor_id = null where instructor_id = $1", user_id)
        return await _profile(conn, user_id)


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(user_id: UUID, admin: dict = Depends(require_admin)):
    if user_id == admin["id"]:
        raise HTTPException(400, "You can't remove your own account")
    async with db.acquire() as conn:
        role = await conn.fetchval("select role from users where id = $1", user_id)
        if role is None:
            raise HTTPException(404, "Account not found")
        # Trainees of a removed instructor are unassigned (FK: on delete set null).
        await conn.execute("delete from users where id = $1", user_id)
