-- =====================================================================
--  SOP -> rules: instructor-uploaded aircraft SOPs, LLM-drafted rules,
--  human verification, aircraft binding.
--  Applies ON TOP of db/schema.sql (+ schema_mavis_integration.sql).
--  Additive and idempotent -- safe to re-run.
--
--  WRITER: MAVIS (MAVIS/ai-instructor/sop/). This is a deliberate
--  exception to INTEGRATION_PLAN.md's Option A ("IRIS is the only
--  Postgres writer"): these tables are owned end-to-end by MAVIS and IRIS
--  only ever reads them. No IRIS-owned table is written by MAVIS.
--
--  Lifecycle:
--    sop_documents   one row per unique PDF (sha256)
--    sop_pages       extracted text per page (+ whether it came from OCR);
--                    the verification UI and the anti-hallucination quote
--                    check both read from here
--    sop_rulesets    one parse run of a document -> status
--                    processing -> draft -> verified   (or failed)
--    sop_rules       individual rules; review_status
--                    draft | approved | rejected | auto_rejected
--    sop_aircraft_bindings   verified ruleset -> MAVIS aircraft profile;
--                    at most one ACTIVE binding per profile
-- =====================================================================

create table if not exists sop_documents (
    id            uuid primary key default gen_random_uuid(),
    filename      text not null,
    sha256        text not null unique,
    page_count    int  not null,
    ocr_pages     int[] not null default '{}',     -- 1-based page numbers that went through OCR
    stored_path   text not null,                   -- MAVIS-local path of the original PDF
    uploaded_by   text,                            -- instructor callsign
    uploaded_at   timestamptz not null default now()
);

create table if not exists sop_pages (
    document_id   uuid not null references sop_documents(id) on delete cascade,
    page_no       int  not null,                   -- 1-based
    text          text not null,
    source        text not null check (source in ('text', 'ocr', 'empty')),
    ocr_confidence double precision,               -- mean tesseract confidence 0..100 (ocr pages only)
    primary key (document_id, page_no)
);

create table if not exists sop_rulesets (
    id              uuid primary key default gen_random_uuid(),
    document_id     uuid not null references sop_documents(id) on delete cascade,
    version         int  not null,                 -- 1, 2, ... per document (re-parse or revise)
    parent_id       uuid references sop_rulesets(id) on delete set null,  -- set when revised from a verified one
    status          text not null default 'processing'
                    check (status in ('processing', 'draft', 'verified', 'failed')),
    llm_model       text,                          -- e.g. 'qwen2.5:14b-instruct'
    chunks_total    int  not null default 0,
    chunks_done     int  not null default 0,
    error           text,
    created_by      text,
    created_at      timestamptz not null default now(),
    verified_by     text,
    verified_at     timestamptz,
    unique (document_id, version)
);

create table if not exists sop_rules (
    id              bigint generated always as identity primary key,
    ruleset_id      uuid not null references sop_rulesets(id) on delete cascade,
    origin          text not null default 'llm' check (origin in ('llm', 'manual')),
    review_status   text not null default 'draft'
                    check (review_status in ('draft', 'approved', 'rejected', 'auto_rejected')),
    kind            text not null
                    check (kind in ('band', 'limit', 'event', 'capture', 'unobservable')),
    title           text not null,
    phases          text[] not null default '{}',  -- MAVIS lesson phases; empty = applies in every phase
    metric          text,                          -- MAVIS Telemetry field (ias, alt_agl, flaps_deg, ...); null for unobservable
    -- all numbers below are stored in MAVIS Telemetry units (kt, ft, ft/min, deg, rpm, throttle 0..1)
    target          double precision,              -- band
    tol_hi          double precision,              -- band: allowed above target
    tol_lo          double precision,              -- band: allowed below target
    min_value       double precision,              -- limit / capture
    max_value       double precision,              -- limit / capture
    op              text check (op in ('>=', '<=', '==', '>', '<')),  -- event
    value           double precision,              -- event
    gate_phase      text,                          -- event: must hold by entry to this phase
    capture_at      text check (capture_at in ('liftoff', 'touchdown', 'phase_entry', 'phase_exit')),
    conditions      jsonb not null default '[]',   -- [{metric, op, value}] -- rule only applies while all hold
    severity        text not null default 'caution'
                    check (severity in ('advisory', 'caution', 'warning')),
    -- provenance: every LLM rule must point at the SOP text it came from
    source_quote    text,
    source_page     int,
    quote_score     double precision,              -- 0..1 match of source_quote against sop_pages
    as_written      jsonb,                         -- raw LLM numbers + unit before normalisation
    issues          text[] not null default '{}',  -- validator findings shown to the reviewer
    reviewer_note   text,
    reviewed_by     text,
    reviewed_at     timestamptz,
    created_at      timestamptz not null default now()
);
create index if not exists sop_rules_ruleset_idx on sop_rules (ruleset_id);

create table if not exists sop_aircraft_bindings (
    id                bigint generated always as identity primary key,
    aircraft_profile  text not null,               -- MAVIS profiles/aircraft/<stem>.yaml
    ruleset_id        uuid not null references sop_rulesets(id) on delete cascade,
    active            boolean not null default true,
    bound_by          text,
    bound_at          timestamptz not null default now(),
    unbound_at        timestamptz
);
-- at most one active ruleset per aircraft profile
create unique index if not exists sop_bindings_one_active
    on sop_aircraft_bindings (aircraft_profile) where active;
