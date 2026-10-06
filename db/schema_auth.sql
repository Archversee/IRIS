-- =====================================================================
--  Accounts + login (additive; safe to re-run).
--  The backend also applies this automatically at startup
--  (backend/app/auth.py: ensure_schema), so running it by hand is optional.
--
--  Three roles: trainee, instructor, admin. Only admins create/remove
--  accounts. A trainee has at most ONE instructor (users.instructor_id);
--  an instructor can have many trainees (all rows pointing at them).
-- =====================================================================

create table if not exists users (
    id             uuid primary key default gen_random_uuid(),
    username       text not null,
    password_hash  text not null,
    full_name      text not null default '',
    email          text,
    role           text not null check (role in ('trainee', 'instructor', 'admin')),
    -- trainees only: their one instructor (must be a user with role 'instructor';
    -- enforced by the API). Deleting the instructor just unassigns them.
    instructor_id  uuid references users(id) on delete set null,
    created_at     timestamptz not null default now()
);
create unique index if not exists users_username_lower on users (lower(username));
create index if not exists users_instructor on users (instructor_id);

-- Login tokens: one row per signed-in browser. Deleting a user signs them out.
create table if not exists auth_tokens (
    token       text primary key,
    user_id     uuid not null references users(id) on delete cascade,
    created_at  timestamptz not null default now(),
    expires_at  timestamptz not null
);
create index if not exists auth_tokens_user on auth_tokens (user_id);
