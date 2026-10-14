-- =====================================================================
--  SOP / handbook rule checks of a flight (additive; safe to re-run).
--  The IRIS backend also applies this at startup
--  (backend/app/routers/rule_results.py: ensure_schema).
--
--  MAVIS (sop/results.py) checks a session's flight_data against the rules
--  that applied to it -- live during the flight, and again post-flight --
--  and writes one flight_evaluations row + one rule_results row per rule.
--  Re-checking (other strictness, edited rules) adds a NEW evaluation; the
--  latest one is what Review shows. An instructor's verdict on a rule is
--  carried over to later evaluations of the same session: the instructor
--  has the final say. A scoped exception to INTEGRATION_PLAN.md's "IRIS
--  is the only writer", like the sop_* tables: MAVIS writes the computed
--  rows, IRIS writes only the instructor's columns.
-- =====================================================================

-- strictness preset per trainee (MAVIS sop/evaluate.py STRICTNESS);
-- null = the MAVIS mode's default. A session's own choice wins over it.
alter table users
    add column if not exists rule_strictness text;
alter table users drop constraint if exists users_rule_strictness_check;
alter table users add constraint users_rule_strictness_check
    check (rule_strictness is null or rule_strictness in ('lenient', 'standard', 'strict'));

create table if not exists flight_evaluations (
    id               uuid primary key default gen_random_uuid(),
    session_id       uuid not null references sessions(id) on delete cascade,
    source           text not null check (source in ('live', 'postflight')),
    strictness       text not null check (strictness in ('lenient', 'standard', 'strict')),
    strictness_why   text,                     -- 'set for this session' | 'set for this trainee' | 'default for trainee mode'
    aircraft_profile text,                     -- MAVIS profiles/aircraft/<id>.yaml
    lesson_id        text,
    lesson_type      text,                     -- normal | emergency | instrument
    elevation_ft     double precision,         -- field elevation, for AGL rules
    rule_set_ids     text[] not null default '{}',
    rules            jsonb not null,           -- snapshot of the rules exactly as checked
    summary          jsonb,                    -- counts per verdict (sop/evaluate.py summarize)
    n_samples        int,
    t_first          timestamptz,
    t_last           timestamptz,
    requested_by     text,
    created_at       timestamptz not null default now(),
    -- closing the loop: the instructor has reviewed the results
    signed_off_by    text,
    signed_off_at    timestamptz,
    instructor_comment text
);
-- which aircraft rules were used and why ("Simulator reported 'X', which matches no
-- aircraft profile: no aircraft rules"). Added after the first version; safe to re-run.
alter table flight_evaluations add column if not exists aircraft_note text;

create index if not exists flight_evaluations_session on flight_evaluations (session_id, created_at desc);

create table if not exists rule_results (
    id                 bigint generated always as identity primary key,
    evaluation_id      uuid not null references flight_evaluations(id) on delete cascade,
    session_id         uuid not null references sessions(id) on delete cascade,
    rule_id            bigint,                 -- flight_rules.id; no FK, results outlive an edited/deleted rule
    sort_order         int not null default 0,
    title              text not null,
    kind               text not null,
    severity           text,
    metric             text,
    layer              text,                   -- aircraft | general (handbook)
    requirement        text,                   -- '74 kt +10/-5', 'at most 85 kt', ...
    verdict            text not null check (verdict in ('pass', 'fail', 'not_applicable',
                                         'insufficient_data', 'not_evaluated', 'instructor')),
    summary            text,
    metrics            jsonb,
    violations         jsonb not null default '[]',   -- [{t0, t1, dur_s, phase, peak, peak_text}], t = epoch s
    checks             jsonb not null default '[]',   -- event / capture moments
    source_quote       text,
    source_page        int,
    -- the instructor's final say (null = agrees with the computed verdict)
    instructor_verdict text check (instructor_verdict is null or
                                   instructor_verdict in ('pass', 'fail', 'not_applicable')),
    instructor_note    text,
    reviewed_by        text,
    reviewed_at        timestamptz
);
create index if not exists rule_results_eval on rule_results (evaluation_id, sort_order);
create index if not exists rule_results_session_rule on rule_results (session_id, rule_id);
