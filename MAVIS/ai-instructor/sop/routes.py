"""FastAPI routes for the SOP pages (mounted into MAVIS's app at /sop; the
IRIS React app reaches them through the /mavis proxy).

  Drafts (local JSON, nothing in Postgres yet)       -> /sop/new page
    POST   /sop/drafts                 upload PDF, start extract + LLM
    GET    /sop/drafts                 unsaved drafts (resume)
    GET    /sop/drafts/{id}            draft + rules (poll for progress)
    PUT    /sop/drafts/{id}            autosave edits; returns re-checked rules
    POST   /sop/drafts/{id}/reparse    run the LLM again over the same pages
    POST   /sop/drafts/{id}/save       write to Postgres + make active for the aircraft
    DELETE /sop/drafts/{id}            discard
    GET    /sop/drafts/{id}/pages/{n}[/image]
  Saved rules (Postgres, edited in place + history)  -> /sop/rules page
    GET    /sop/rulesets[?aircraft=]   GET/PATCH/DELETE /sop/rulesets/{id}
    POST   /sop/rulesets/{id}/activate | /deactivate
    POST   /sop/rulesets/{id}/rules    PATCH/DELETE /sop/rules/{id}
    GET    /sop/rulesets/{id}/history[?rule_id=]
    GET    /sop/documents/{id}/pages/{n}[/image]
  Runtime
    GET    /sop/active/{aircraft}      enabled rules of the aircraft's active set

Who made a change comes from the X-SOP-Editor header (the name typed into
the page); there is no PIN on these pages.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from uuid import UUID

import asyncpg  # noqa: F401  -- hard deps: fail at import so web/server.py can
import pymupdf  # noqa: F401  -- skip mounting /sop instead of failing per request
from fastapi import APIRouter, File, Header, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from . import extract, validate
from .drafts import DraftStore
from .llm_parse import OllamaParser
from .pipeline import JobManager
from .schema import (CAPTURE_AT, KINDS, METRICS, OPS, SEVERITIES, aircraft_profiles,
                     known_phases)
from .store import RULE_FIELDS, DBUnavailable, RuleStore

log = logging.getLogger(__name__)

NOT_FLYING_PHASES = {"BRIEFING", "DEBRIEF"}
_NUMS = ("target", "tol_hi", "tol_lo", "min_value", "max_value", "value")


class RuleIn(BaseModel):
    cid: str | None = None
    include: bool | None = None
    enabled: bool | None = None
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
    note: str | None = None


class DraftIn(BaseModel):
    name: str | None = None
    aircraft_profile: str | None = None
    rules: list[RuleIn] | None = None


class SaveIn(BaseModel):
    aircraft_profile: str
    name: str


class RenameIn(BaseModel):
    name: str


def build_sop_router(state: dict, cfg: dict | None, app_root: Path) -> tuple[APIRouter, JobManager, RuleStore]:
    cfg = cfg or {}
    sop_cfg = cfg.get("sop") or {}
    coach_cfg = cfg.get("coach") or {}
    store = RuleStore((cfg.get("database") or {}).get("url"))
    think = sop_cfg.get("think", False)
    parser = OllamaParser(sop_cfg.get("ollama_url") or coach_cfg.get("ollama_url", "http://localhost:11434"),
                          sop_cfg.get("model") or coach_cfg.get("model", "qwen2.5:14b-instruct"),
                          timeout_s=float(sop_cfg.get("llm_timeout_s", 300)),
                          num_ctx=int(sop_cfg.get("num_ctx", 8192)),
                          think=None if think == "omit" else bool(think))
    tess = sop_cfg.get("tesseract_cmd") or None
    data_dir = app_root / sop_cfg.get("pdf_dir", "data/sop")
    drafts = DraftStore(data_dir / "drafts")
    drafts.fail_interrupted()

    def is_flying() -> bool:
        if "flying" in state:
            return bool(state["flying"])
        return str(state.get("phase") or "BRIEFING") not in NOT_FLYING_PHASES

    jobs = JobManager(drafts, parser, data_dir, is_flying, tesseract_cmd=tess,
                      ocr_lang=sop_cfg.get("ocr_lang", "eng"),
                      chunk_chars=int(sop_cfg.get("chunk_chars", 6000)))
    r = APIRouter(prefix="/sop", tags=["sop"])

    async def db(coro):
        try:
            return await coro
        except DBUnavailable as e:
            raise HTTPException(503, f"Rules database unavailable: {e}")

    def profile(pid: str | None) -> dict:
        for a in aircraft_profiles():
            if a["id"] == pid:
                return a
        raise HTTPException(400, f"Unknown aircraft profile '{pid}'")

    # ------------------------------------------------------------ status / meta
    @r.get("/status")
    async def status():
        db_ok, llm_ok = await asyncio.gather(store.ping(), parser.available())
        ocr_ok, ocr_detail = await asyncio.get_running_loop().run_in_executor(
            None, extract.ocr_check, tess, sop_cfg.get("ocr_lang", "eng"))
        return {"db": db_ok, "llm": llm_ok, "model": parser.model,
                "ocr": ocr_ok, "ocr_detail": ocr_detail,
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

    # ------------------------------------------------------------ drafts
    def get_draft(did: str) -> dict:
        d = drafts.get(did)
        if not d:
            raise HTTPException(404, "No such draft (it may have been saved or discarded)")
        return d

    def recheck(d: dict, rule: dict) -> dict:
        page_rows = drafts.pages(d["id"])
        pages = {p["page_no"]: p["text"] for p in page_rows}
        ocr = {p["page_no"] for p in page_rows if p["source"] == "ocr"}
        issues, fatal = validate.assess(rule, known_phases(), pages, ocr)
        rule["issues"], rule["blocked"] = issues, fatal
        return rule

    @r.post("/drafts")
    async def upload(file: UploadFile = File(...), x_sop_editor: str | None = Header(None)):
        if is_flying():
            raise HTTPException(409, "Finish or quit the current lesson before processing an SOP")
        data = await file.read()
        try:
            return jobs.start_upload(Path(file.filename or "sop.pdf").name, data, x_sop_editor)
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))

    @r.get("/drafts")
    async def list_drafts():
        return drafts.list()

    def with_checks(d: dict) -> dict:
        """Cross-check the included rules against the chosen aircraft's profile
        (speeds above Vne / below Vs0, flaps beyond the last detent...)."""
        warns = []
        for a in aircraft_profiles():
            if a["id"] == d.get("aircraft_profile"):
                warns = validate.aircraft_checks([x for x in d.get("rules", []) if x.get("include")], a["profile"])
        return {**d, "aircraft_warnings": warns}

    @r.get("/drafts/{did}")
    async def draft(did: str):
        return with_checks(get_draft(did))

    @r.put("/drafts/{did}")
    async def update_draft(did: str, body: DraftIn):
        d = get_draft(did)
        if d["status"] == "processing":
            raise HTTPException(409, "Still processing - wait until the rules are drafted")
        if body.name is not None:
            d["name"] = body.name.strip()[:120] or d["name"]
        if body.aircraft_profile is not None:
            d["aircraft_profile"] = body.aircraft_profile or None
        if body.rules is not None:
            old = {x["cid"]: x for x in d["rules"]}
            new_rules = []
            for rin in body.rules:
                inc = rin.model_dump(exclude_unset=True)
                prev = old.get(inc.get("cid") or "", {})
                rule = {**prev, **inc}
                rule.setdefault("cid", uuid.uuid4().hex[:12])
                rule.setdefault("origin", "manual")
                rule.setdefault("include", True)
                rule.setdefault("enabled", True)
                rule.setdefault("phases", [])
                rule.setdefault("conditions", [])
                rule.setdefault("severity", "caution")
                rule.setdefault("title", "New rule")
                rule.setdefault("kind", "limit")
                # once a number was changed by hand, the LLM's as-written values no
                # longer describe the rule (no "number not in quote" check on it)
                if prev.get("as_written") and any(prev.get(k) != rule.get(k) for k in _NUMS):
                    rule.pop("as_written", None)
                new_rules.append(recheck(d, rule))
            d["rules"] = new_rules
        return with_checks(drafts.put(d))

    @r.post("/drafts/{did}/reparse")
    async def reparse(did: str):
        if is_flying():
            raise HTTPException(409, "Finish or quit the current lesson first")
        try:
            return jobs.start_reparse(did)
        except KeyError:
            raise HTTPException(404, "No such draft")
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))

    @r.post("/drafts/{did}/save")
    async def save(did: str, body: SaveIn, x_sop_editor: str | None = Header(None)):
        d = get_draft(did)
        if d["status"] != "ready":
            raise HTTPException(409, "This draft isn't ready to save yet")
        profile(body.aircraft_profile)
        for rule in d["rules"]:
            recheck(d, rule)
        chosen = [x for x in d["rules"] if x.get("include")]
        blocked = [{"cid": x["cid"], "title": x.get("title"), "reason": x["blocked"]} for x in chosen if x.get("blocked")]
        if blocked:
            drafts.put(d)
            raise HTTPException(422, {"message": f"{len(blocked)} rule(s) still need fixing", "blocked": blocked})
        if not chosen:
            raise HTTPException(422, {"message": "Include at least one rule", "blocked": []})
        rows = [{k: x.get(k) for k in RULE_FIELDS} | {"origin": x.get("origin", "manual"),
                                                       "llm_draft": x.get("llm_draft")} for x in chosen]
        rs = await db(store.save_draft(d, drafts.pages(did), rows, body.aircraft_profile,
                                       body.name.strip()[:120] or d["name"], x_sop_editor))
        drafts.delete(did)
        return {"rule_set_id": rs}

    @r.delete("/drafts/{did}")
    async def discard(did: str):
        d = get_draft(did)
        if d["status"] == "processing" and jobs.busy:
            raise HTTPException(409, "Wait for processing to finish, then discard")
        drafts.delete(did)
        return {"ok": True}

    def render_page(pdf_path: str, page_no: int, q: str) -> bytes:
        import pymupdf as fitz
        with fitz.open(pdf_path) as pdf:
            if not 1 <= page_no <= pdf.page_count:
                raise HTTPException(404, "No such page")
            page = pdf[page_no - 1]
            words = q.split()
            for n in (len(words), 12, 8, 5):          # longest findable prefix of the quote
                if n <= 0 or n > len(words):
                    continue
                hits = page.search_for(" ".join(words[:n]))
                if hits:
                    for h in hits[:3]:
                        page.add_highlight_annot(h)
                    break
            return page.get_pixmap(dpi=110).tobytes("png")

    async def png(pdf_path: str, page_no: int, q: str) -> Response:
        if not pdf_path or not Path(pdf_path).is_file():
            raise HTTPException(404, "Original PDF not found on this machine")
        data = await asyncio.get_running_loop().run_in_executor(None, render_page, pdf_path, page_no, q)
        return Response(data, media_type="image/png", headers={"Cache-Control": "no-store"})

    @r.get("/drafts/{did}/pages/{page_no}")
    async def draft_page(did: str, page_no: int):
        get_draft(did)
        for p in drafts.pages(did):
            if p["page_no"] == page_no:
                return p
        raise HTTPException(404, "No such page")

    @r.get("/drafts/{did}/pages/{page_no}/image")
    async def draft_page_image(did: str, page_no: int, q: str = ""):
        return await png(get_draft(did)["pdf_path"], page_no, q)

    # ------------------------------------------------------------ saved rule sets
    async def check_saved(rule: dict, doc_id) -> dict:
        pages, ocr = await db(store.page_texts(doc_id))
        issues, fatal = validate.assess(rule, known_phases(), pages, ocr)
        if fatal:
            raise HTTPException(422, {"message": f"Can't save: {fatal}", "issues": issues})
        return {"issues": issues}

    @r.get("/rulesets")
    async def rulesets(aircraft: str | None = None):
        return await db(store.list_sets(aircraft))

    @r.get("/rulesets/{rs}")
    async def ruleset(rs: UUID):
        out = await db(store.get_set(rs))
        if not out:
            raise HTTPException(404, "No such rule set")
        return out

    @r.patch("/rulesets/{rs}")
    async def rename(rs: UUID, body: RenameIn, x_sop_editor: str | None = Header(None)):
        if not body.name.strip():
            raise HTTPException(400, "Name can't be empty")
        if not await db(store.rename_set(rs, body.name.strip()[:120], x_sop_editor)):
            raise HTTPException(404, "No such rule set")
        return {"ok": True}

    @r.post("/rulesets/{rs}/activate")
    async def activate(rs: UUID, x_sop_editor: str | None = Header(None)):
        if not await db(store.set_active(rs, True, x_sop_editor)):
            raise HTTPException(404, "No such rule set")
        return {"ok": True}

    @r.post("/rulesets/{rs}/deactivate")
    async def deactivate(rs: UUID, x_sop_editor: str | None = Header(None)):
        if not await db(store.set_active(rs, False, x_sop_editor)):
            raise HTTPException(404, "No such rule set")
        return {"ok": True}

    @r.delete("/rulesets/{rs}")
    async def delete_set(rs: UUID):
        if not await db(store.delete_set(rs)):
            raise HTTPException(409, "Only an inactive rule set can be deleted")
        return {"ok": True}

    @r.post("/rulesets/{rs}/rules")
    async def add_rule(rs: UUID, body: RuleIn, x_sop_editor: str | None = Header(None)):
        head = await db(store.get_set(rs))
        if not head:
            raise HTTPException(404, "No such rule set")
        rule = {k: v for k, v in body.model_dump(exclude_unset=True).items() if k in RULE_FIELDS}
        rule = {"title": "New rule", "kind": "limit", "phases": [], "conditions": [], "severity": "caution",
                **rule, "origin": "manual"}
        await check_saved(rule, head["document_id"])
        rule.pop("origin")
        return await db(store.add_rule(rs, rule, x_sop_editor))

    @r.patch("/rules/{rule_id}")
    async def patch_rule(rule_id: int, body: RuleIn, x_sop_editor: str | None = Header(None)):
        cur = await db(store.get_rule(rule_id))
        if not cur:
            raise HTTPException(404, "No such rule")
        head = await db(store.get_set(UUID(cur["rule_set_id"])))
        changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if k in RULE_FIELDS}
        merged = {**cur, **changes}
        await check_saved(merged, head["document_id"])
        for k in ("phases", "source_page", "quote_score"):      # normalised by the check
            if merged.get(k) != cur.get(k):
                changes[k] = merged[k]
        return await db(store.update_rule(rule_id, changes, x_sop_editor))

    @r.delete("/rules/{rule_id}")
    async def delete_rule(rule_id: int, x_sop_editor: str | None = Header(None)):
        if not await db(store.delete_rule(rule_id, x_sop_editor)):
            raise HTTPException(404, "No such rule")
        return {"ok": True}

    @r.get("/rulesets/{rs}/history")
    async def history(rs: UUID, rule_id: int | None = None):
        return await db(store.history(rs, rule_id))

    @r.get("/documents/{doc_id}/pages/{page_no}")
    async def doc_page(doc_id: UUID, page_no: int):
        p = await db(store.get_page(doc_id, page_no))
        if not p:
            raise HTTPException(404, "No such page")
        return {"page_no": p["page_no"], "text": p["text"], "source": p["source"],
                "ocr_confidence": p["ocr_confidence"]}

    @r.get("/documents/{doc_id}/pages/{page_no}/image")
    async def doc_page_image(doc_id: UUID, page_no: int, q: str = ""):
        doc = await db(store.get_document(doc_id))
        if not doc:
            raise HTTPException(404, "No such document")
        return await png(doc["stored_path"], page_no, q)

    @r.get("/active/{aircraft}")
    async def active(aircraft: str):
        out = await db(store.active_rules(aircraft))
        if out is None:
            raise HTTPException(404, "No active rule set for this aircraft")
        return out

    return r, jobs, store
