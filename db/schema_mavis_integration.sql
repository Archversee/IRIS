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
    add column if not exists source                text not null default 'manual',
    -- source: 'manual' (existing Upload-page flow) | 'mavis' (session minted by MAVIS)
    -- recording_paths: absolute paths of the OBS recordings LiveAdapter.py made
    -- for this session; deleted with it (backend/app/videos.py). Applied at startup too.
    add column if not exists recording_paths       text[];

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
    add column if not exists on_ground        boolean,
    -- MAVIS reads rudder/aileron trim next to the elevator trim flight_data already has.
    -- -100..100 %; an aircraft with no such trim reports a constant 0.
    add column if not exists rudder_trim_pct  double precision,
    add column if not exists aileron_trim_pct double precision;

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

-- ---------------------------------------------------------------------
-- flight_events: start/end time of each graded event in a session -- one
-- row per to-do step (familiarization: straight & level, left turn, ...) or
-- circuit phase (take-off roll ... rollout). Written by the MAVIS bridge
-- from MAVIS's EventLog (core/event_log.py); the boundaries are decided by
-- MAVIS's lesson engine, so event n ends at the instant event n+1 starts.
-- start_ts/end_ts are on the same clock as flight_data.ts. end_ts is null
-- while the event is still in progress.
-- score/grade/detail are for the SOP scoring pass (nothing writes them yet):
-- the dashboard colours an event by grade when set.
-- A step re-flown after a retry is a new row (attempt 2, ...); the abandoned
-- attempt keeps outcome 'retried'.
-- ---------------------------------------------------------------------
create table if not exists flight_events (
    session_id  uuid not null references sessions(id) on delete cascade,
    seq         integer not null,        -- order within the session, from 0 (continues across flights)
    flight      integer not null default 1,  -- which flight of the session (session_flights.flight)
    event_id    text not null,           -- lesson step/phase id, e.g. 'TURN_LEFT', 'DOWNWIND'
    label       text not null,           -- display name, e.g. 'Gentle left turn'
    kind        text,                    -- 'exercise' (to-do step) | 'phase' (circuit phase) | 'demo' (MAVIS flying; not the trainee's, never graded)
    phase       text,
    circuit     integer,
    attempt     integer not null default 1,
    start_ts    timestamptz not null,
    end_ts      timestamptz,
    outcome     text,                    -- completed | skipped (timed out) | retried | abandoned
    score       integer,
    grade       text,
    detail      jsonb,
    primary key (session_id, seq)
);

-- Added after flight_events was first created (idempotent either way):
alter table flight_events add column if not exists flight integer not null default 1;

-- ---------------------------------------------------------------------
-- session_flights: one row per flight in a session. "Fly again" and Restart keep the
-- same session_id, so a session can hold several flights, each from its own
-- curriculum. curriculum = 'lesson' (a catalog lesson: lesson_id/title/module say
-- which) or 'free_flight' (Free Flight menu; lesson_id is then only whatever lesson
-- MAVIS had loaded underneath, not what was flown). flight_events.flight points here.
-- outcome: completed | abandoned (ended early) | restarted.
-- ---------------------------------------------------------------------
create table if not exists session_flights (
    session_id  uuid not null references sessions(id) on delete cascade,
    flight      integer not null,
    lesson_id   text,
    title       text not null,           -- 'Flight Familiarization', 'Your First Circuit', 'Free Flight', ...
    curriculum  text not null default 'lesson',
    module      text,                    -- catalog group, e.g. 'Basic Flight'
    mode        text,
    start_ts    timestamptz not null,
    end_ts      timestamptz,
    outcome     text,
    primary key (session_id, flight)
);
