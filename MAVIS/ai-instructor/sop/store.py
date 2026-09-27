"""Persistence in the shared Postgres (db/schema_sop_rules.sql).

Nothing is written here until the instructor presses Save on a reviewed
draft (drafts live in local JSON files, see drafts.py). After that, saved
rules are edited in place and every change is logged in flight_rule_history.

MAVIS owns these tables end-to-end; IRIS only reads them. The pool is
created lazily, so MAVIS still boots (and flies) when Postgres is down --
the endpoints that need it answer 503 until it's back.
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from uuid import UUID

log = logging.getLogger(__name__)

# columns of flight_rules the API reads/writes
RULE_FIELDS = ("kind", "title", "phases", "metric", "target", "tol_hi", "tol_lo",
               "min_value", "max_value", "op", "value", "gate_phase", "capture_at",
               "conditions", "severity", "source_quote", "source_page", "quote_score",
               "note", "enabled")
_JSONB = {"conditions", "llm_draft", "before", "after"}


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
            raise DBUnavailable(f"Postgres connection lost: {e}") from e

    async def close(self):
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def ping(self) -> bool:
        try:
            async with self.acquire() as c:
                return bool(await c.fetchval("select to_regclass('public.flight_rules') is not null"))
        except Exception:
            return False

    # ------------------------------------------------------------ save a reviewed draft
    async def save_draft(self, draft: dict, pages: list[dict], rules: list[dict],
                         aircraft: str, name: str, by: str | None) -> str:
        """One transaction: document + pages (reused if this PDF was saved
        before), a new rule set with its rules, made the aircraft's active set."""
        async with self.acquire() as c, c.transaction():
            doc_id = await c.fetchval("select id from sop_documents where sha256=$1", draft["sha256"])
            if doc_id is None:
                doc_id = await c.fetchval(
                    "insert into sop_documents (filename, sha256, page_count, ocr_pages, stored_path, uploaded_by) "
                    "values ($1,$2,$3,$4,$5,$6) returning id",
                    draft["filename"], draft["sha256"], len(pages),
                    [p["page_no"] for p in pages if p["source"] == "ocr"], draft["pdf_path"], by)
                await c.executemany(
                    "insert into sop_pages (document_id, page_no, text, source, ocr_confidence) values ($1,$2,$3,$4,$5)",
                    [(doc_id, p["page_no"], p["text"], p["source"], p.get("ocr_confidence")) for p in pages])
            rs = await c.fetchval(
                "insert into flight_rule_sets (aircraft_profile, name, document_id, llm_model, created_by, updated_by) "
                "values ($1,$2,$3,$4,$5,$5) returning id", aircraft, name, doc_id, draft.get("llm_model"), by)
            for i, r in enumerate(rules):
                row = await self._insert_rule(c, rs, r, i, by)
                await self._log(c, rs, row["id"], "create", by, None, row)
            await self._activate(c, rs, aircraft, by)
        return str(rs)

    # ------------------------------------------------------------ rule sets
    async def list_sets(self, aircraft: str | None = None) -> list[dict]:
        async with self.acquire() as c:
            rows = await c.fetch("""
                select s.*, d.filename, d.page_count,
                       count(r.id) as n_rules,
                       count(r.id) filter (where r.enabled) as n_enabled
                  from flight_rule_sets s
                  left join sop_documents d on d.id = s.document_id
                  left join flight_rules r on r.rule_set_id = s.id
                 where ($1::text is null or s.aircraft_profile = $1)
                 group by s.id, d.id
                 order by s.active desc, s.updated_at desc""", aircraft)
        return [_clean(r) for r in rows]

    async def get_set(self, rs: UUID) -> dict | None:
        async with self.acquire() as c:
            head = await c.fetchrow("""
                select s.*, d.filename, d.page_count, d.ocr_pages
                  from flight_rule_sets s left join sop_documents d on d.id = s.document_id
                 where s.id = $1""", rs)
            if not head:
                return None
            rules = await c.fetch("select * from flight_rules where rule_set_id=$1 order by sort_order, id", rs)
        out = _clean(head)
        out["rules"] = [_clean(r) for r in rules]
        return out

    async def rename_set(self, rs: UUID, name: str, by: str | None) -> bool:
        async with self.acquire() as c, c.transaction():
            old = await c.fetchval("select name from flight_rule_sets where id=$1 for update", rs)
            if old is None:
                return False
            await c.execute("update flight_rule_sets set name=$2, updated_by=$3, updated_at=now() where id=$1",
                            rs, name, by)
            await self._log(c, rs, None, "rename", by, {"name": old}, {"name": name})
        return True

    async def set_active(self, rs: UUID, active: bool, by: str | None) -> bool:
        async with self.acquire() as c, c.transaction():
            row = await c.fetchrow("select aircraft_profile, active from flight_rule_sets where id=$1 for update", rs)
            if not row:
                return False
            if active:
                await self._activate(c, rs, row["aircraft_profile"], by)
            elif row["active"]:
                await c.execute("update flight_rule_sets set active=false, updated_by=$2, updated_at=now() "
                                "where id=$1", rs, by)
                await self._log(c, rs, None, "deactivate", by, None, None)
        return True

    async def delete_set(self, rs: UUID) -> bool:
        async with self.acquire() as c:
            res = await c.execute("delete from flight_rule_sets where id=$1 and not active", rs)
        return res.endswith("1")

    async def _activate(self, c, rs, aircraft: str, by):
        prev = await c.fetch("select id from flight_rule_sets where aircraft_profile=$1 and active and id<>$2",
                             aircraft, rs)
        for p in prev:
            await c.execute("update flight_rule_sets set active=false, updated_by=$2, updated_at=now() where id=$1",
                            p["id"], by)
            await self._log(c, p["id"], None, "deactivate", by, None, {"replaced_by": str(rs)})
        already = await c.fetchval("select active from flight_rule_sets where id=$1", rs)
        if not already:
            await c.execute("update flight_rule_sets set active=true, updated_by=$2, updated_at=now() where id=$1",
                            rs, by)
            await self._log(c, rs, None, "activate", by, None, {"aircraft_profile": aircraft})

    # ------------------------------------------------------------ rules
    async def get_rule(self, rule_id: int) -> dict | None:
        async with self.acquire() as c:
            row = await c.fetchrow("select * from flight_rules where id=$1", rule_id)
        return _clean(row) if row else None

    async def add_rule(self, rs: UUID, rule: dict, by: str | None) -> dict:
        async with self.acquire() as c, c.transaction():
            n = await c.fetchval("select coalesce(max(sort_order),-1)+1 from flight_rules where rule_set_id=$1", rs)
            row = await self._insert_rule(c, rs, {**rule, "origin": "manual"}, n, by)
            await self._log(c, rs, row["id"], "create", by, None, row)
            await self._touch(c, rs, by)
        return row

    async def update_rule(self, rule_id: int, fields: dict, by: str | None) -> dict | None:
        fields = {k: v for k, v in fields.items() if k in RULE_FIELDS}
        async with self.acquire() as c, c.transaction():
            before = await c.fetchrow("select * from flight_rules where id=$1 for update", rule_id)
            if not before:
                return None
            if not fields:
                return _clean(before)
            sets = [f"{k} = ${i + 2}" for i, k in enumerate(fields)]
            row = await c.fetchrow(
                f"update flight_rules set {', '.join(sets)}, updated_by=${len(fields) + 2}, updated_at=now() "
                "where id=$1 returning *", rule_id, *[_db_val(k, v) for k, v in fields.items()], by)
            b, a = _clean(before), _clean(row)
            await self._log(c, before["rule_set_id"], rule_id, "update", by, b, a)
            await self._touch(c, before["rule_set_id"], by)
        return a

    async def delete_rule(self, rule_id: int, by: str | None) -> bool:
        async with self.acquire() as c, c.transaction():
            row = await c.fetchrow("delete from flight_rules where id=$1 returning *", rule_id)
            if not row:
                return False
            await self._log(c, row["rule_set_id"], rule_id, "delete", by, _clean(row), None)
            await self._touch(c, row["rule_set_id"], by)
        return True

    async def history(self, rs: UUID, rule_id: int | None = None, limit: int = 200) -> list[dict]:
        async with self.acquire() as c:
            rows = await c.fetch(
                "select * from flight_rule_history where rule_set_id=$1 and ($2::bigint is null or rule_id=$2) "
                "order by changed_at desc, id desc limit $3", rs, rule_id, limit)
        return [_clean(r) for r in rows]

    async def page_texts(self, doc_id) -> tuple[dict[int, str], set[int]]:
        if not doc_id:
            return {}, set()
        async with self.acquire() as c:
            rows = await c.fetch("select page_no, text, source from sop_pages where document_id=$1", UUID(str(doc_id)))
        return ({r["page_no"]: r["text"] for r in rows}, {r["page_no"] for r in rows if r["source"] == "ocr"})

    async def get_page(self, doc_id: UUID, page_no: int) -> dict | None:
        async with self.acquire() as c:
            row = await c.fetchrow("select * from sop_pages where document_id=$1 and page_no=$2", doc_id, page_no)
        return dict(row) if row else None

    async def get_document(self, doc_id: UUID) -> dict | None:
        async with self.acquire() as c:
            row = await c.fetchrow("select * from sop_documents where id=$1", doc_id)
        return dict(row) if row else None

    async def active_rules(self, profile: str) -> dict | None:
        """What a lesson loads: the enabled rules of the aircraft's active set."""
        async with self.acquire() as c:
            s = await c.fetchrow("""
                select s.id, s.name, s.updated_at, d.filename from flight_rule_sets s
                  left join sop_documents d on d.id = s.document_id
                 where s.aircraft_profile=$1 and s.active""", profile)
            if not s:
                return None
            rules = await c.fetch("select * from flight_rules where rule_set_id=$1 and enabled "
                                  "order by sort_order, id", s["id"])
        return {"rule_set_id": str(s["id"]), "name": s["name"], "filename": s["filename"],
                "updated_at": s["updated_at"].isoformat(), "rules": [_clean(r) for r in rules]}

    # ------------------------------------------------------------ helpers
    async def _insert_rule(self, c, rs, r: dict, order: int, by) -> dict:
        cols = [k for k in RULE_FIELDS if k in r] + ["origin", "llm_draft", "sort_order", "created_by", "updated_by"]
        vals = [_db_val(k, r.get(k)) for k in RULE_FIELDS if k in r] + \
               [r.get("origin", "manual"), r.get("llm_draft"), order, by, by]
        row = await c.fetchrow(
            f"insert into flight_rules (rule_set_id, {', '.join(cols)}) values "
            f"($1, {', '.join(f'${i + 2}' for i in range(len(cols)))}) returning *", rs, *vals)
        return _clean(row)

    async def _touch(self, c, rs, by):
        await c.execute("update flight_rule_sets set updated_by=$2, updated_at=now() where id=$1", rs, by)

    async def _log(self, c, rs, rule_id, action, by, before, after):
        await c.execute("insert into flight_rule_history (rule_set_id, rule_id, action, changed_by, before, after) "
                        "values ($1,$2,$3,$4,$5,$6)", rs if isinstance(rs, UUID) else UUID(str(rs)),
                        rule_id, action, by, before, after)


def _db_val(k: str, v):
    if k == "phases":
        return list(v or [])
    if k == "conditions":
        return list(v or [])
    if k == "enabled":
        return True if v is None else bool(v)
    return v


def _clean(row) -> dict:
    d = dict(row)
    for k, v in list(d.items()):
        if isinstance(v, UUID):
            d[k] = str(v)
        elif hasattr(v, "isoformat"):
            d[k] = v.isoformat()
    return d
