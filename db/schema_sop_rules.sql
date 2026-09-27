-- =====================================================================
--  SOP -> flight rules
--  Applies ON TOP of db/schema.sql (+ schema_mavis_integration.sql).
--  Additive and idempotent -- safe to re-run.
--
--  WRITER: MAVIS (MAVIS/ai-instructor/sop/). A deliberate, scoped
--  exception to INTEGRATION_PLAN.md's Option A: MAVIS writes only the
--  tables in this file; IRIS only reads them.
--
--  Flow:
--    1. Instructor uploads an SOP PDF. MAVIS reads it (OCR where needed) and
--       the local LLM drafts rules. The draft lives in a local JSON file
--       (MAVIS data/sop/drafts/) -- NOTHING is written here yet.
--    2. Instructor reviews/edits the draft on /sop/new and presses Save:
--       one transaction writes sop_documents + sop_pages + a flight_rule_sets
--       row + its flight_rules, makes the set active for the chosen
--       aircraft, and logs every rule in flight_rule_history.
--    3. /sop/rules edits saved rules in place; every create / update /
--       delete / (de)activation is logged in flight_rule_history.
--
--  All rule numbers are in MAVIS Telemetry units: kt, ft, ft/min, deg,
--  rpm, throttle 0..1 (adapters/sim_base.py).
-- =====================================================================

-- ---------------------------------------------------------------------
-- Superseded by flight_rule_sets / flight_rules / flight_rule_history
-- below (earlier draft of this feature, never merged to main). Safe to
-- drop: only ever held test data on dev machines.
-- ---------------------------------------------------------------------
drop table if exists sop_aircraft_bindings;
drop table if exists sop_rules;
drop table if exists sop_rulesets;

-- ---------------------------------------------------------------------
-- Source documents (written at Save time, never for an unsaved draft)
-- ---------------------------------------------------------------------
create table if not exists sop_documents (
    id            uuid primary key default gen_random_uuid(),
    filename      text not null,
    sha256        text not null unique,
    page_count    int  not null,
    ocr_pages     int[] not null default '{}',     -- 1-based page numbers that were read with OCR
    stored_path   text not null,                   -- MAVIS-local path of the original PDF
    uploaded_by   text,
    uploaded_at   timestamptz not null default now()
);

create table if not exists sop_pages (
    document_id    uuid not null references sop_documents(id) on delete cascade,
    page_no        int  not null,                  -- 1-based
    text           text not null,
    source         text not null check (source in ('text', 'ocr', 'empty')),
    ocr_confidence double precision,               -- mean Tesseract confidence 0..100 (OCR pages only)
    primary key (document_id, page_no)
);

-- ---------------------------------------------------------------------
-- A saved set of rules for one aircraft. At most one ACTIVE set per
-- aircraft profile -- that's the one a lesson in that aircraft uses.
-- Saving a new SOP for an aircraft deactivates (keeps) the previous set.
-- ---------------------------------------------------------------------
create table if not exists flight_rule_sets (
    id                uuid primary key default gen_random_uuid(),
    aircraft_profile  text not null,               -- MAVIS profiles/aircraft/<id>.yaml
    name              text not null,               -- e.g. 'C172 SOP rev 3'
    document_id       uuid references sop_documents(id) on delete set null,  -- null = typed in by hand
    llm_model         text,                        -- model that drafted it, e.g. 'qwen2.5:14b-instruct'
    active            boolean not null default false,
    created_by        text,
    created_at        timestamptz not null default now(),
    updated_by        text,
    updated_at        timestamptz not null default now()
);
create unique index if not exists flight_rule_sets_one_active
    on flight_rule_sets (aircraft_profile) where active;

-- ---------------------------------------------------------------------
-- One checkable rule. kind decides which value columns are used:
--   band          target, tol_hi, tol_lo          hold target ± tolerance
--   limit         min_value and/or max_value      stay within limits
--   event         op, value, gate_phase           must be true by a phase
--   capture       capture_at, min/max_value       checked at one moment
--   unobservable  (none)                          instructor assesses it
-- ---------------------------------------------------------------------
create table if not exists flight_rules (
    id              bigint generated always as identity primary key,
    rule_set_id     uuid not null references flight_rule_sets(id) on delete cascade,
    sort_order      int  not null default 0,
    enabled         boolean not null default true,  -- switch a rule off without deleting it
    origin          text not null default 'llm' check (origin in ('llm', 'manual')),
    kind            text not null
                    check (kind in ('band', 'limit', 'event', 'capture', 'unobservable')),
    title           text not null,
    phases          text[] not null default '{}',   -- MAVIS lesson phases; empty = every phase
    metric          text,                           -- Telemetry field: ias, alt_agl, vs, bank, flaps_deg, ...
    target          double precision,
    tol_hi          double precision,
    tol_lo          double precision,
    min_value       double precision,
    max_value       double precision,
    op              text check (op in ('>=', '<=', '==', '>', '<')),
    value           double precision,
    gate_phase      text,
    capture_at      text check (capture_at in ('liftoff', 'touchdown', 'phase_entry', 'phase_exit')),
    conditions      jsonb not null default '[]',    -- [{metric, op, value}]: rule applies only while all hold
    severity        text not null default 'caution'
                    check (severity in ('advisory', 'caution', 'warning')),
    -- provenance: where in the SOP this rule came from
    source_quote    text,
    source_page     int,
    quote_score     double precision,               -- 0..1 match of source_quote against sop_pages
    llm_draft       jsonb,                          -- the LLM's original proposal, before any human edit
    note            text,
    created_by      text,
    created_at      timestamptz not null default now(),
    updated_by      text,
    updated_at      timestamptz not null default now(),
    -- the kind's required values must be present
    constraint flight_rules_kind_fields check (
        (kind = 'band'    and metric is not null and target is not null
                          and (tol_hi is not null or tol_lo is not null)) or
        (kind = 'limit'   and metric is not null and (min_value is not null or max_value is not null)) or
        (kind = 'event'   and metric is not null and op is not null and value is not null) or
        (kind = 'capture' and metric is not null and capture_at is not null
                          and (min_value is not null or max_value is not null)) or
        (kind = 'unobservable')
    )
);
create index if not exists flight_rules_set_idx on flight_rules (rule_set_id, sort_order);

-- ---------------------------------------------------------------------
-- Audit trail: every change to a saved rule or rule set.
-- before/after are full JSON snapshots of the rule (null on create/delete).
-- ---------------------------------------------------------------------
create table if not exists flight_rule_history (
    id            bigint generated always as identity primary key,
    rule_set_id   uuid not null references flight_rule_sets(id) on delete cascade,
    rule_id       bigint,                          -- no FK: history outlives a deleted rule
    action        text not null
                  check (action in ('create', 'update', 'delete', 'activate', 'deactivate', 'rename')),
    changed_by    text,
    changed_at    timestamptz not null default now(),
    before        jsonb,
    after         jsonb
);
create index if not exists flight_rule_history_set_idx on flight_rule_history (rule_set_id, changed_at desc);
create index if not exists flight_rule_history_rule_idx on flight_rule_history (rule_id, changed_at desc);
