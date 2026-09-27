"""FastAPI routes for the SOP wizard (mounted into MAVIS's app at /sop).

Every write needs the instructor area unlocked (same PIN session as the rest
of the instructor tools). The library listing (/meta, /status, /rulesets,
/bindings) and GET /sop/active/{profile} are read-only and open: the
curriculum page shows them to everyone, and the runtime -- and IRIS -- use
/active to load an aircraft's verified rules.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from uuid import UUID

import asyncpg  # noqa: F401  -- hard deps: fail at import so web/server.py can
import pymupdf  # noqa: F401  -- skip mounting /sop instead of failing per request
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from . import extract, validate
from .llm_parse import OllamaParser
from .pipeline import JobManager
from .schema import (CAPTURE_AT, KINDS, METRICS, OPS, SEVERITIES, aircraft_profiles,
                     known_phases)
from .store import DBUnavailable, RuleStore

log = logging.getLogger(__name__)

NOT_FLYING_PHASES = {"BRIEFING", "DEBRIEF"}


class RulePatch(BaseModel):
    kind: str | None = None
    title: str | None = None
    phases: list[str] | None = None
    metric: str | None = None
    target: float | None = None
    tol_hi: float | None = None
    tol_lo: float | None = None
    min_value: float | None = None
    max_value: float | None = None
    op: str | None = None
    value: float | None = None
    gate_phase: str | None = None
    capture_at: str | None = None
    conditions: list[dict] | None = None
    severity: str | None = None
    source_quote: str | None = None
    source_page: int | None = None
    reviewer_note: str | None = None
    review_status: str | None = None


class BulkReview(BaseModel):
    ids: list[int]
    status: str


class BindBody(BaseModel):
    aircraft_profile: str


def build_sop_router(state: dict, cfg: dict | None, app_root: Path) -> tuple[APIRouter, JobManager, RuleStore]:
    cfg = cfg or {}
    sop_cfg = cfg.get("sop") or {}
    coach_cfg = cfg.get("coach") or {}
    store = RuleStore((cfg.get("database") or {}).get("url"))
    parser = OllamaParser(sop_cfg.get("ollama_url") or coach_cfg.get("ollama_url", "http://localhost:11434"),
                          sop_cfg.get("model") or coach_cfg.get("model", "qwen2.5:14b-instruct"),
                          timeout_s=float(sop_cfg.get("llm_timeout_s", 300)),
                          num_ctx=int(sop_cfg.get("num_ctx", 8192)))
    tess = sop_cfg.get("tesseract_cmd") or None

    def is_flying() -> bool:
        # main.py publishes state["flying"] every tick; the phase is only a
        # fallback (it stays mid-circuit after the trainee quits a lesson)
        if "flying" in state:
            return bool(state["flying"])
        return str(state.get("phase") or "BRIEFING") not in NOT_FLYING_PHASES

    jobs = JobManager(store, parser, app_root / sop_cfg.get("pdf_dir", "data/sop"), is_flying,
                      tesseract_cmd=tess, ocr_lang=sop_cfg.get("ocr_lang", "eng"),
                      chunk_chars=int(sop_cfg.get("chunk_chars", 6000)))
    r = APIRouter(prefix="/sop", tags=["sop"])

    def who() -> str | None:
        if not state.get("instructor_unlocked"):
            raise HTTPException(403, "Unlock the instructor area with your PIN first")
        return (state.get("instructor") or {}).get("callsign")

    async def db(coro):
        try:
            return await coro
        except DBUnavailable as e:
            raise HTTPException(503, f"Rules database unavailable: {e}")

    # ------------------------------------------------------------ status / meta
    @r.get("/status")
    async def status():
        db_ok, llm_ok = await asyncio.gather(store.ping(), parser.available())
        return {"db": db_ok, "llm": llm_ok, "model": parser.model,
                "ocr": await asyncio.get_running_loop().run_in_executor(None, extract.ocr_available, tess),
                "busy": jobs.busy, "flying": is_flying()}

    @r.get("/meta")
    async def meta():
        return {
            "phases": known_phases(), "kinds": list(KINDS), "ops": list(OPS),
            "severities": list(SEVERITIES), "capture_at": list(CAPTURE_AT),
            "metrics": [{"id": k, "label": v[3], "unit": v[1], "range": v[2]} for k, v in METRICS.items()],
            "aircraft": [{"id": a["id"], "name": a["name"],
                          "v_speeds": (a["profile"].get("v_speeds") or {}),
                          "flaps": (a["profile"].get("flaps") or {}).get("detents_deg"),
                          "sim_match": a["profile"].get("sim_match") or []}
                         for a in aircraft_profiles()],
        }

    # ------------------------------------------------------------ stage 1
    @r.post("/upload")
    async def upload(file: UploadFile = File(...)):
        by = who()
        if is_flying():
            raise HTTPException(409, "Finish or quit the current flight before processing an SOP")
        if not await store.ping():
            raise HTTPException(503, "Rules database unavailable - is Postgres running and "
                                     "db/schema_sop_rules.sql applied?")
        data = await file.read()
        try:
            job = jobs.start_upload(Path(file.filename or "sop.pdf").name, data, by)
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))
        return job.as_dict()

    @r.get("/jobs/{job_id}")
    async def job(job_id: str):
        j = jobs.get(job_id)
        if not j:
            raise HTTPException(404, "No such job (MAVIS may have restarted) - check the ruleset list")
        return j.as_dict()

    @r.post("/documents/{doc_id}/reparse")
    async def reparse(doc_id: UUID):
        by = who()
        doc = await db(store.get_document(doc_id))
        if not doc:
            raise HTTPException(404, "No such document")
        try:
            return jobs.start_reparse(str(doc_id), doc["filename"], by).as_dict()
        except RuntimeError as e:
            raise HTTPException(409, str(e))

    @r.get("/documents/{doc_id}/pages/{page_no}")
    async def page_text(doc_id: UUID, page_no: int):
        who()
        p = await db(store.get_page(doc_id, page_no))
        if not p:
            raise HTTPException(404, "No such page")
        return {"page_no": p["page_no"], "text": p["text"], "source": p["source"],
                "ocr_confidence": p["ocr_confidence"]}

    @r.get("/documents/{doc_id}/pages/{page_no}/image")
    async def page_image(doc_id: UUID, page_no: int, q: str = ""):
        """PNG of the original page, with the quote highlighted where the page has
        a text layer -- lets the reviewer check scanned pages against the source."""
        who()
        doc = await db(store.get_document(doc_id))
        if not doc or not Path(doc["stored_path"]).is_file():
            raise HTTPException(404, "Original PDF not found")

        def render() -> bytes:
            import pymupdf
            with pymupdf.open(doc["stored_path"]) as pdf:
                if not 1 <= page_no <= pdf.page_count:
                    raise HTTPException(404, "No such page")
                page = pdf[page_no - 1]
                words = q.split()
                for n in (len(words), 12, 8, 5):          # longest findable prefix
                    if n <= 0 or n > len(words):
                        continue
                    hits = page.search_for(" ".join(words[:n]))
                    if hits:
                        for h in hits[:3]:
                            page.add_highlight_annot(h)
                        break
                return page.get_pixmap(dpi=110).tobytes("png")
        png = await asyncio.get_running_loop().run_in_executor(None, render)
        return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------ rulesets
    # Read-only library views are open (the curriculum's Aircraft SOPs page
    # shows them to everyone); every change still needs the instructor PIN.
    @r.get("/rulesets")
    async def rulesets():
        return await db(store.list_rulesets())

    @r.get("/rulesets/{rs}")
    async def ruleset(rs: UUID):
        who()
        out = await db(store.get_ruleset(rs))
        if not out:
            raise HTTPException(404, "No such ruleset")
        return out

    @r.delete("/rulesets/{rs}")
    async def delete_ruleset(rs: UUID):
        who()
        if not await db(store.delete_ruleset(rs)):
            raise HTTPException(409, "Only unbound draft/failed rulesets can be deleted")
        return {"ok": True}

    # ------------------------------------------------------------ stage 2 (review)
    async def _recheck(rule: dict) -> tuple[list[str], str | None]:
        """Re-run the deterministic checks on an edited rule."""
        issues, fatal = validate.check_rule(rule, known_phases())
        if rule.get("source_quote"):
            doc_pages = await store.page_texts(UUID(rule["_document_id"])) if rule.get("_document_id") else ({}, set())
            p_issues, p_fatal = validate.check_provenance(rule, *doc_pages)
            issues += p_issues
            if rule.get("origin") == "llm":
                fatal = fatal or p_fatal
        elif rule.get("origin") == "llm":
            fatal = fatal or "no source quote - can't be traced to the SOP"
        if rule.get("kind") == "band" and rule.get("tol_hi") is None and rule.get("tol_lo") is None:
            fatal = fatal or "set a tolerance (or change the rule to a limit)"
        return issues, fatal

    @r.patch("/rules/{rule_id}")
    async def patch_rule(rule_id: int, body: RulePatch):
        by = who()
        cur = await db(store.get_rule(rule_id))
        if not cur:
            raise HTTPException(404, "No such rule")
        rs = await db(store.get_ruleset(UUID(cur["ruleset_id"])))
        changes = body.model_dump(exclude_unset=True)
        want = changes.pop("review_status", None)
        if want is not None and want not in ("draft", "approved", "rejected"):
            raise HTTPException(400, "review_status must be draft, approved or rejected")
        merged = {**cur, **changes, "_document_id": rs["document_id"]}
        if changes or want in ("approved", "draft"):
            issues, fatal = await _recheck(merged)
            if want == "approved" and fatal:
                raise HTTPException(422, f"Can't approve yet: {fatal}")
            if want == "draft" and cur["review_status"] == "auto_rejected" and fatal:
                raise HTTPException(422, f"Still failing: {fatal} - edit the rule first")
            changes["issues"] = issues + ([f"Needs fixing: {fatal}"] if fatal else [])
            for k in ("phases", "source_page", "quote_score"):
                if k in merged and merged[k] != cur.get(k):
                    changes[k] = merged[k]
        if want is not None:
            changes["review_status"] = want
        elif changes and cur["review_status"] == "approved":
            changes["review_status"] = "draft"        # an edit needs re-approval
        out = await db(store.update_rule(rule_id, changes, by))
        if out is None:
            raise HTTPException(409, "This ruleset is verified and frozen - use Revise to change it")
        return out

    @r.post("/rulesets/{rs}/rules")
    async def add_rule(rs: UUID, body: RulePatch):
        by = who()
        head = await db(store.get_ruleset(rs))
        if not head:
            raise HTTPException(404, "No such ruleset")
        rule = body.model_dump(exclude_unset=True)
        rule.pop("review_status", None)
        rule.setdefault("title", "New rule")
        rule.setdefault("kind", "limit")
        rule.setdefault("phases", [])
        rule.setdefault("conditions", [])
        rule.setdefault("severity", "caution")
        rule["origin"] = "manual"
        rule["_document_id"] = head["document_id"]
        issues, fatal = await _recheck(rule)
        if fatal and rule["kind"] not in KINDS:
            raise HTTPException(400, fatal)
        rule.pop("_document_id"); rule.pop("origin")
        out = await db(store.add_manual_rule(rs, rule, issues + ([f"Needs fixing: {fatal}"] if fatal else []), by))
        if out is None:
            raise HTTPException(409, "This ruleset is verified and frozen - use Revise to change it")
        return out

    @r.post("/rulesets/{rs}/review")
    async def bulk_review(rs: UUID, body: BulkReview):
        """Bulk approve/reject. Approve only takes rules that pass every check."""
        by = who()
        if body.status not in ("approved", "rejected"):
            raise HTTPException(400, "status must be approved or rejected")
        ids = body.ids
        skipped = []
        if body.status == "approved":
            head = await db(store.get_ruleset(rs))
            if not head:
                raise HTTPException(404, "No such ruleset")
            by_id = {x["id"]: x for x in head["rules"]}
            ok_ids = []
            for i in ids:
                rule = by_id.get(i)
                if not rule:
                    continue
                _, fatal = await _recheck({**rule, "_document_id": head["document_id"]})
                (skipped.append({"id": i, "reason": fatal}) if fatal else ok_ids.append(i))
            ids = ok_ids
        n = await db(store.bulk_review(rs, ids, body.status, by))
        return {"updated": n, "skipped": skipped}

    @r.post("/rulesets/{rs}/verify")
    async def verify(rs: UUID):
        by = who()
        ok, msg = await db(store.verify_ruleset(rs, by))
        if not ok:
            raise HTTPException(409, msg)
        return {"ok": True}

    @r.post("/rulesets/{rs}/revise")
    async def revise(rs: UUID):
        by = who()
        new = await db(store.revise_ruleset(rs, by))
        if not new:
            raise HTTPException(409, "Only a verified ruleset can be revised")
        return {"ruleset_id": str(new)}

    # ------------------------------------------------------------ stage 3 (bind)
    def _profile(pid: str) -> dict:
        for a in aircraft_profiles():
            if a["id"] == pid:
                return a
        raise HTTPException(404, f"No aircraft profile '{pid}'")

    @r.get("/rulesets/{rs}/aircraft-check/{profile}")
    async def aircraft_check(rs: UUID, profile: str):
        who()
        a = _profile(profile)
        rules = await db(store.approved_rules(rs))
        return {"warnings": validate.aircraft_checks(rules, a["profile"]), "n_rules": len(rules)}

    @r.post("/rulesets/{rs}/bind")
    async def bind(rs: UUID, body: BindBody):
        by = who()
        _profile(body.aircraft_profile)
        ok, msg = await db(store.bind(rs, body.aircraft_profile, by))
        if not ok:
            raise HTTPException(409, msg)
        return {"ok": True}

    @r.get("/bindings")
    async def bindings():
        return await db(store.bindings())

    @r.delete("/bindings/{profile}")
    async def unbind(profile: str):
        who()
        if not await db(store.unbind(profile)):
            raise HTTPException(404, "Nothing bound to that aircraft")
        return {"ok": True}

    @r.get("/active/{profile}")
    async def active(profile: str):
        out = await db(store.active_rules(profile))
        if out is None:
            raise HTTPException(404, "No verified SOP ruleset is bound to this aircraft")
        return out

    return r, jobs, store
