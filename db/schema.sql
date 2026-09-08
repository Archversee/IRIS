-- =====================================================================
--  Flight Review System — database schema
--  Target: Supabase Postgres 17 (plain Postgres; NO TimescaleDB)
--
--  TimescaleDB is deprecated on Supabase and removed from the PG17
--  bundle, so we use ordinary tables with time-aware indexes instead.
--  At this project's scale (10 Hz => ~36k rows per flight-hour) plain
--  Postgres is more than fast enough. If you ever outgrow it, convert
--  flight_data / eye_tracking_data to native RANGE partitions on ts
--  (managed by pg_partman) without changing the application code.
--
--  Run this in the Supabase SQL editor, or:
--    psql "$DATABASE_URL" -f db/schema.sql
-- =====================================================================

-- ---------------------------------------------------------------------
-- sessions: one row per recorded flight / review session
-- ---------------------------------------------------------------------
create table if not exists sessions (
    id           uuid primary key default gen_random_uuid(),
    name         text not null,
    pilot_name   text,
    aircraft     text,
    sim_source   text,                       -- 'MSFS', 'P3D', ...
    notes        text,
    started_at   timestamptz,                -- flight start (from data)
    ended_at     timestamptz,                -- flight end (from data)
    created_at   timestamptz not null default now(),
    video_filename    text,                  -- filename inside settings.video_dir, if a recording is linked
    video_offset_sec  double precision not null default 0  -- recording-start vs first flight-data sample, seconds
);

-- ---------------------------------------------------------------------
-- flight_data: time-series flight state (schema mirrors MSFSAdapter.py)
-- ---------------------------------------------------------------------
create table if not exists flight_data (
    session_id            uuid not null references sessions(id) on delete cascade,
    ts                    timestamptz not null,       -- timestamp_utc from the logger

    -- Flight State
    altitude_ft           double precision,
    airspeed_kt           double precision,
    vertical_speed_fpm    double precision,
    heading_true_deg      double precision,
    pitch_deg             double precision,
    bank_deg              double precision,
    yaw_rate_radps        double precision,
    fuel_total_qty_gal    double precision,

    -- Control Inputs
    throttle_pct          double precision,
    elevator_trim_pct     double precision,
    elevator_position     double precision,
    rudder_position       double precision,
    flaps_handle_pct      double precision,
    gear_handle_position  boolean,

    -- Environment
    latitude              double precision,
    longitude             double precision,
    ambient_visibility_m  double precision,
    ambient_wind_kt       double precision,
    ambient_temp_c        double precision,

    -- Sim status (polled)
    sim_running           boolean,
    sim_paused            boolean,

    primary key (session_id, ts)
);

-- ---------------------------------------------------------------------
-- eye_tracking_data: time-series gaze data (Smart Eye SEP subset)
-- Adjust columns to the exact SEP fields you export.
-- ---------------------------------------------------------------------
create table if not exists eye_tracking_data (
    session_id             uuid not null references sessions(id) on delete cascade,
    ts                     timestamptz not null,

    -- Gaze origin (world coords, metres)
    gaze_origin_x          double precision,
    gaze_origin_y          double precision,
    gaze_origin_z          double precision,

    -- Gaze direction (unit vector)
    gaze_dir_x             double precision,
    gaze_dir_y             double precision,
    gaze_dir_z             double precision,

    -- 2D intersection point on the instrument panel / screen
    gaze_point_x           double precision,
    gaze_point_y           double precision,

    -- Pupil / eyelid
    pupil_diam_left_mm     double precision,
    pupil_diam_right_mm    double precision,
    eyelid_opening_mm      double precision,
    blink                  boolean,

    -- Head pose
    head_pos_x             double precision,
    head_pos_y             double precision,
    head_pos_z             double precision,
    head_heading_deg       double precision,
    head_pitch_deg         double precision,
    head_roll_deg          double precision,

    -- Derived / annotation
    gaze_quality           double precision,   -- 0..1 confidence
    aoi                    text,               -- area of interest label
    fixation_id            integer,

    primary key (session_id, ts)
);

-- ---------------------------------------------------------------------
-- events: discrete events (crash, checklist, scenario triggers, ...)
-- ---------------------------------------------------------------------
create table if not exists events (
    id           bigint generated always as identity primary key,
    session_id   uuid not null references sessions(id) on delete cascade,
    ts           timestamptz not null,
    event_type   text not null,               -- 'system' | 'checklist' | 'scenario' | ...
    label        text not null,               -- e.g. 'Crashed', 'Gear down checklist'
    payload      jsonb                         -- arbitrary extra data
);

-- ---------------------------------------------------------------------
-- Indexes
--   The (session_id, ts) PKs already cover the common
--   "one session, time-ordered" access pattern. BRIN indexes on ts are
--   tiny and speed up wide time-range scans on big append-only tables.
-- ---------------------------------------------------------------------
create index if not exists flight_data_ts_brin  on flight_data using brin (ts);
create index if not exists eye_data_ts_brin      on eye_tracking_data using brin (ts);
create index if not exists events_session_ts_idx on events (session_id, ts);

-- ---------------------------------------------------------------------
-- Single-user note:
--   Row Level Security is left OFF for this single-user build. Before you
--   add auth / multi-user, enable RLS on every table and add policies:
--     alter table sessions enable row level security;
--     ... etc.
-- ---------------------------------------------------------------------
