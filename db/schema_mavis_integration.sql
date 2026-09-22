-- =====================================================================
--  MAVIS <-> IRIS integration — additive schema migration
--  Applies ON TOP of db/schema.sql. Nothing here removes or renames an
--  existing column; every field an old client relies on keeps working.
--  Target: local Postgres 17 (was Supabase pooler; same DDL, new host).
--  STATUS: PROPOSED — not applied yet, pending plan approval.
-- =====================================================================

-- ---------------------------------------------------------------------
-- sessions: MAVIS now mints session_id (uuid) at lesson start and is the
-- source of truth for lesson/mode metadata. These columns are nullable
-- so rows created via the existing manual Upload flow (no MAVIS involved)
-- are unaffected.
-- ---------------------------------------------------------------------
alter table sessions
    add column if not exists lesson_id            text,        -- MAVIS lesson_id, e.g. 'lesson01_pattern'
    add column if not exists mode                  text,        -- visitor | trainee | pilot | exam
    add column if not exists instructor_callsign   text,
    add column if not exists source                text not null default 'manual';
    -- source: 'manual' (existing Upload-page flow) | 'mavis' (session minted by MAVIS)

-- ---------------------------------------------------------------------
-- flight_data: MAVIS's Telemetry has a few fields IRIS's SimConnect-mirrored
-- columns don't carry (flap angle vs. handle %, engine RPM, aileron
-- position, ground contact, and the lesson-phase/geometry MAVIS computes
-- every tick). Added as nullable extras — existing readers ignore them,
-- MAVIS-sourced rows populate what they can, non-MAVIS rows leave them null.
-- ---------------------------------------------------------------------
alter table flight_data
    add column if not exists phase            text,             -- MAVIS lesson phase at this sample (e.g. 'DOWNWIND')
    add column if not exists along_ft         double precision, -- MAVIS runway-centerline geometry: along-track
    add column if not exists cross_ft         double precision, -- MAVIS runway-centerline geometry: cross-track
    add column if not exists rpm              double precision,
    add column if not exists aileron_position double precision,
    add column if not exists flaps_deg        double precision, -- MAVIS reads flap surface angle, not handle %
    add column if not exists on_ground        boolean;

-- ---------------------------------------------------------------------
-- coaching_events: MAVIS's CoachEvent stream + transcript, one row per
-- spoken/logged coaching moment. New table — lets Review correlate a
-- coaching callout with what the trainee was looking at (eye_tracking_data)
-- and the flight state (flight_data) at the same instant.
-- ---------------------------------------------------------------------
create table if not exists coaching_events (
    id          bigint generated always as identity primary key,
    session_id  uuid not null references sessions(id) on delete cascade,
    ts          timestamptz not null,
    code        text,               -- CoachEvent.code, e.g. 'ROTATE_NOW' (null for plain chat/Q&A turns)
    severity    text,
    who         text,               -- 'coach' | 'trainee'
    phase       text,
    message     text not null,      -- the rendered/spoken text (or trainee's transcribed question)
    data        jsonb,              -- CoachEvent.data payload, if any
    created_at  timestamptz not null default now()
);
create index if not exists coaching_events_session_ts on coaching_events (session_id, ts);

-- ---------------------------------------------------------------------
-- scorecards: MAVIS's deterministic 5-metric ScoreCard for a completed run.
-- One row per finished attempt (a session may be rescored later, hence a
-- plain id PK rather than session_id as PK).
-- ---------------------------------------------------------------------
create table if not exists scorecards (
    id              bigint generated always as identity primary key,
    session_id      uuid not null references sessions(id) on delete cascade,
    lesson_id       text,
    mode            text,
    track_score     integer,
    speed_score     integer,
    altitude_score  integer,
    landing_score   integer,
    smoothness_score integer,
    total           integer,
    grade           text,          -- 'S'|'A'|'B'|'C'|'D'|'F'
    retries         integer,
    capped          boolean,
    created_at      timestamptz not null default now()
);
create index if not exists scorecards_session on scorecards (session_id);

-- ---------------------------------------------------------------------
-- leaderboard_entries: Postgres mirror of MAVIS's data/leaderboard.json,
-- so IRIS's frontend (which never touches MAVIS's filesystem) can render
-- a leaderboard screen. MAVIS's JSON file remains its own local/offline
-- source of truth per the "fully local" invariant; this table is a
-- read-side copy written by the integration bridge (see plan §Coupling).
-- session_id is nullable because MAVIS's demo-seeded rows have no session.
-- ---------------------------------------------------------------------
create table if not exists leaderboard_entries (
    id              bigint generated always as identity primary key,
    session_id      uuid references sessions(id) on delete set null,
    display_name    text not null,
    given_name      text,
    family_name     text,
    mode            text,
    lesson_id       text,
    scored_at       timestamptz not null,
    track_score     integer,
    speed_score     integer,
    altitude_score  integer,
    landing_score   integer,
    smoothness_score integer,
    total           integer,
    grade           text,
    retries         integer,
    capped          boolean
);
create index if not exists leaderboard_entries_lesson on leaderboard_entries (lesson_id, total desc);
