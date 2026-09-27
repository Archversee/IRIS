"""Stage 4: persistence in the shared Postgres (db/schema_sop_rules.sql).

MAVIS owns the sop_* tables end-to-end; IRIS only reads them. The pool is
created lazily, so MAVIS still boots (and flies) when Postgres is down --
the SOP endpoints just answer 503 until it's back.
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from uuid import UUID

log = logging.getLogger(__name__)

RULE_FIELDS = ("kind", "title", "phases", "metric", "target", "tol_hi", "tol_lo",
               "min_value", "max_value", "op", "value", "gate_phase", "capture_at",
               "conditions", "severity", "source_quote", "source_page", "quote_score",
               "as_written", "issues", "reviewer_note")


class DBUnavailable(RuntimeError):
    pass


class RuleStore:
    def __init__(self, dsn: str | None = None):
        self.dsn = os.environ.get("DATABASE_URL") or dsn or \
            "postgresql://postgres:postgres@localhost:5432/postgres"
        self._pool = None

    async def _init_conn(self, conn):
        await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")

    async def pool(self):
        if self._pool is None:
            try:
                import asyncpg
                self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=4,
                                                       init=self._init_conn, timeout=5)
            except Exception as e:
                self._pool = None
                raise DBUnavailable(f"Postgres not reachable: {e}") from e
        return self._pool

    @asynccontextmanager
    async def acquire(self):
        import asyncpg
        pool = await self.pool()
        try:
            async with pool.acquire(timeout=5) as conn:
                yield conn
        except (OSError, asyncpg.PostgresConnectionError, asyncpg.InterfaceError,
                asyncpg.CannotConnectNowError, TimeoutError) as e:
            # Postgres went away after the pool was created -- same 503 path as
            # "never reachable"; the pool reconnects by itself once it's back.
            raise DBUnavailable(f"Postgres connection lost: {e}") from e

    async def close(self):
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def ping(self) -> bool:
        try:
            async with self.acquire() as c:
                return bool(await c.fetchval("select to_regclass('public.sop_rules') is not null"))
        except Exception:
            return False

    # ------------------------------------------------------------ documents
    async def find_document(self, sha: str) -> dict | None:
        async with self.acquire() as c:
            row = await c.fetchrow("select * from sop_documents where sha256 = $1", sha)
        return dict(row) if row else None

    async def create_document(self, filename, sha, page_count, ocr_pages, stored_path, by, pages) -> UUID:
        async with self.acquire() as c, c.transaction():
            doc_id = await c.fetchval(
                "insert into sop_documents (filename, sha256, page_count, ocr_pages, stored_path, uploaded_by) "
                "values ($1,$2,$3,$4,$5,$6) returning id",
                filename, sha, page_count, ocr_pages, stored_path, by)
            await c.executemany(
                "insert into sop_pages (document_id, page_no, text, source, ocr_confidence) values ($1,$2,$3,$4,$5)",
                [(doc_id, p.page_no, p.text, p.source, p.ocr_confidence) for p in pages])
        return doc_id

    async def page_texts(self, doc_id: UUID) -> tuple[dict[int, str], set[int]]:
        async with self.acquire() as c:
            rows = await c.fetch("select page_no, text, source from sop_pages where document_id = $1", doc_id)
        return ({r["page_no"]: r["text"] for r in rows},
                {r["page_no"] for r in rows if r["source"] == "ocr"})

    async def get_page(self, doc_id: UUID, page_no: int) -> dict | None:
        async with self.acquire() as c:
            row = await c.fetchrow("select * from sop_pages where document_id=$1 and page_no=$2", doc_id, page_no)
        return dict(row) if row else None

    async def get_document(self, doc_id: UUID) -> dict | None:
        async with self.acquire() as c:
            row = await c.fetchrow("select * from sop_documents where id=$1", doc_id)
        return dict(row) if row else None

    # ------------------------------------------------------------ rulesets
    async def create_ruleset(self, doc_id: UUID, by: str | None, model: str | None,
                             parent: UUID | None = None, status: str = "processing") -> UUID:
        async with self.acquire() as c, c.transaction():
            await c.execute("select pg_advisory_xact_lock(hashtext($1::text))", str(doc_id))
            ver = await c.fetchval("select coalesce(max(version),0)+1 from sop_rulesets where document_id=$1", doc_id)
            return await c.fetchval(
                "insert into sop_rulesets (document_id, version, parent_id, status, llm_model, created_by) "
                "values ($1,$2,$3,$4,$5,$6) returning id", doc_id, ver, parent, status, model, by)

    async def set_progress(self, rs: UUID, done: int, total: int):
        async with self.acquire() as c:
            await c.execute("update sop_rulesets set chunks_done=$2, chunks_total=$3 where id=$1", rs, done, total)

    async def set_status(self, rs: UUID, status: str, error: str | None = None):
        async with self.acquire() as c:
            await c.execute("update sop_rulesets set status=$2, error=$3 where id=$1", rs, status, error)

    async def insert_rules(self, rs: UUID, checked: list, origin: str = "llm"):
        cols = RULE_FIELDS + ("review_status", "origin")
        sql = (f"insert into sop_rules (ruleset_id, {', '.join(cols)}) values "
               f"($1, {', '.join(f'${i + 2}' for i in range(len(cols)))})")
        rows = []
        for ch in checked:
            r = dict(ch.rule)
            r["issues"] = ch.issues + ([f"Auto-rejected: {ch.reject_reason}"] if ch.reject_reason else [])
            r["review_status"] = ch.status
            r["origin"] = origin
            rows.append((rs, *[_db_val(k, r.get(k)) for k in cols]))
        if rows:
            async with self.acquire() as c:
                await c.executemany(sql, rows)

    async def list_rulesets(self) -> list[dict]:
        async with self.acquire() as c:
            rows = await c.fetch("""
                select rs.*, d.filename, d.page_count, d.ocr_pages,
                       count(r.*) filter (where r.review_status='draft')         as n_draft,
                       count(r.*) filter (where r.review_status='approved')      as n_approved,
                       count(r.*) filter (where r.review_status='rejected')      as n_rejected,
                       count(r.*) filter (where r.review_status='auto_rejected') as n_auto_rejected,
                       (select array_agg(b.aircraft_profile) from sop_aircraft_bindings b
                         where b.ruleset_id = rs.id and b.active)                as bound_to
                  from sop_rulesets rs
                  join sop_documents d on d.id = rs.document_id
                  left join sop_rules r on r.ruleset_id = rs.id
                 group by rs.id, d.id
                 order by rs.created_at desc""")
        return [_clean(r) for r in rows]

    async def get_ruleset(self, rs: UUID) -> dict | None:
        async with self.acquire() as c:
            head = await c.fetchrow("""
                select rs.*, d.filename, d.page_count, d.ocr_pages,
                       (select array_agg(b.aircraft_profile) from sop_aircraft_bindings b
                         where b.ruleset_id = rs.id and b.active) as bound_to
                  from sop_rulesets rs join sop_documents d on d.id = rs.document_id
                 where rs.id = $1""", rs)
            if not head:
                return None
            rules = await c.fetch("select * from sop_rules where ruleset_id=$1 "
                                  "order by source_page nulls last, id", rs)
        out = _clean(head)
        out["rules"] = [_clean(r) for r in rules]
        return out

    async def delete_ruleset(self, rs: UUID) -> bool:
        async with self.acquire() as c:
            res = await c.execute("delete from sop_rulesets where id=$1 and status in ('draft','failed','processing') "
                                  "and not exists (select 1 from sop_aircraft_bindings where ruleset_id=$1 and active)", rs)
        return res.endswith("1")

    # ------------------------------------------------------------ rules (review)
    async def ruleset_status(self, c, rs: UUID) -> str | None:
        return await c.fetchval("select status from sop_rulesets where id=$1", rs)

    async def get_rule(self, rule_id: int) -> dict | None:
        async with self.acquire() as c:
            row = await c.fetchrow("select * from sop_rules where id=$1", rule_id)
        return _clean(row) if row else None

    async def update_rule(self, rule_id: int, fields: dict, by: str | None) -> dict | None:
        """Only draft rulesets are editable (verified ones are frozen; revise instead)."""
        allowed = {k: v for k, v in fields.items() if k in RULE_FIELDS + ("review_status",)}
        async with self.acquire() as c, c.transaction():
            rs = await c.fetchval("select ruleset_id from sop_rules where id=$1 for update", rule_id)
            if rs is None or await self.ruleset_status(c, rs) != "draft":
                return None
            sets = [f"{k} = ${i + 2}" for i, k in enumerate(allowed)]
            sets += [f"reviewed_by = ${len(allowed) + 2}", "reviewed_at = now()"]
            row = await c.fetchrow(f"update sop_rules set {', '.join(sets)} where id=$1 returning *",
                                   rule_id, *[_db_val(k, v) for k, v in allowed.items()], by)
        return _clean(row)

    async def add_manual_rule(self, rs: UUID, rule: dict, issues: list[str], by: str | None) -> dict | None:
        cols = [k for k in RULE_FIELDS if k in rule or k == "issues"]
        async with self.acquire() as c, c.transaction():
            if await self.ruleset_status(c, rs) != "draft":
                return None
            vals = [_db_val(k, issues if k == "issues" else rule.get(k)) for k in cols]
            row = await c.fetchrow(
                f"insert into sop_rules (ruleset_id, origin, review_status, reviewed_by, reviewed_at, {', '.join(cols)}) "
                f"values ($1, 'manual', 'draft', ${len(cols) + 2}, now(), "
                f"{', '.join(f'${i + 2}' for i in range(len(cols)))}) returning *", rs, *vals, by)
        return _clean(row)

    async def bulk_review(self, rs: UUID, ids: list[int], status: str, by: str | None) -> int:
        async with self.acquire() as c, c.transaction():
            if await self.ruleset_status(c, rs) != "draft":
                return 0
            res = await c.execute(
                "update sop_rules set review_status=$3, reviewed_by=$4, reviewed_at=now() "
                "where ruleset_id=$1 and id = any($2::bigint[]) and review_status <> 'auto_rejected'",
                rs, ids, status, by)
        return int(res.split()[-1])

    async def verify_ruleset(self, rs: UUID, by: str | None) -> tuple[bool, str]:
        async with self.acquire() as c, c.transaction():
            st = await c.fetchval("select status from sop_rulesets where id=$1 for update", rs)
            if st != "draft":
                return False, f"ruleset is {st}, not draft"
            counts = await c.fetchrow(
                "select count(*) filter (where review_status='draft') d, "
                "count(*) filter (where review_status='approved') a from sop_rules where ruleset_id=$1", rs)
            if counts["d"]:
                return False, f"{counts['d']} rule(s) still need a decision"
            if not counts["a"]:
                return False, "approve at least one rule"
            await c.execute("update sop_rulesets set status='verified', verified_by=$2, verified_at=now() "
                            "where id=$1", rs, by)
        return True, "verified"

    async def revise_ruleset(self, rs: UUID, by: str | None) -> UUID | None:
        """Clone a verified ruleset into a new draft version (the only way to
        change rules after verification)."""
        async with self.acquire() as c:
            src = await c.fetchrow("select * from sop_rulesets where id=$1", rs)
        if not src or src["status"] != "verified":
            return None
        new = await self.create_ruleset(src["document_id"], by, src["llm_model"], parent=rs, status="draft")
        cols = ", ".join(RULE_FIELDS + ("origin", "review_status", "reviewed_by", "reviewed_at"))
        async with self.acquire() as c:
            await c.execute(f"insert into sop_rules (ruleset_id, {cols}) select $2, {cols} "
                            "from sop_rules where ruleset_id=$1 and review_status <> 'auto_rejected'", rs, new)
        return new

    # ------------------------------------------------------------ aircraft binding
    async def approved_rules(self, rs: UUID) -> list[dict]:
        async with self.acquire() as c:
            rows = await c.fetch("select * from sop_rules where ruleset_id=$1 and review_status='approved' "
                                 "order by id", rs)
        return [_clean(r) for r in rows]

    async def bind(self, rs: UUID, profile: str, by: str | None) -> tuple[bool, str]:
        async with self.acquire() as c, c.transaction():
            st = await c.fetchval("select status from sop_rulesets where id=$1", rs)
            if st != "verified":
                return False, "only a verified ruleset can be bound"
            await c.execute("update sop_aircraft_bindings set active=false, unbound_at=now() "
                            "where aircraft_profile=$1 and active", profile)
            await c.execute("insert into sop_aircraft_bindings (aircraft_profile, ruleset_id, bound_by) "
                            "values ($1,$2,$3)", profile, rs, by)
        return True, "bound"

    async def unbind(self, profile: str) -> bool:
        async with self.acquire() as c:
            res = await c.execute("update sop_aircraft_bindings set active=false, unbound_at=now() "
                                  "where aircraft_profile=$1 and active", profile)
        return res.endswith("1")

    async def bindings(self) -> list[dict]:
        async with self.acquire() as c:
            rows = await c.fetch("""
                select b.*, rs.version, d.filename from sop_aircraft_bindings b
                  join sop_rulesets rs on rs.id = b.ruleset_id
                  join sop_documents d on d.id = rs.document_id
                 where b.active order by b.aircraft_profile""")
        return [_clean(r) for r in rows]

    async def active_rules(self, profile: str) -> dict | None:
        """What the runtime loads: approved rules of the active, verified
        ruleset bound to this aircraft profile (None if nothing is bound)."""
        async with self.acquire() as c:
            rs = await c.fetchrow("""
                select rs.id, rs.version, d.filename from sop_aircraft_bindings b
                  join sop_rulesets rs on rs.id = b.ruleset_id and rs.status = 'verified'
                  join sop_documents d on d.id = rs.document_id
                 where b.aircraft_profile = $1 and b.active""", profile)
        if not rs:
            return None
        return {"ruleset_id": str(rs["id"]), "version": rs["version"], "filename": rs["filename"],
                "rules": await self.approved_rules(rs["id"])}


def _db_val(k: str, v):
    if k in ("phases", "issues"):
        return list(v or [])
    if k == "conditions":
        return list(v or [])
    return v


def _clean(row) -> dict:
    d = dict(row)
    for k, v in list(d.items()):
        if isinstance(v, UUID):
            d[k] = str(v)
        elif hasattr(v, "isoformat"):
            d[k] = v.isoformat()
    return d
