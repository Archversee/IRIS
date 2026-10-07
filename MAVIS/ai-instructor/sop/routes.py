"""FastAPI routes for the SOP pages (mounted into MAVIS's app at /sop; the
IRIS React app reaches them through the /mavis proxy).

  Drafts (local JSON, nothing in Postgres yet)       -> /sop/new page
    POST   /sop/drafts                 upload PDF (+ doc_type poh|handbook, handbook afh|ifh|phak|school|other)
    POST   /sop/drafts/import          upload a rules file made with Export > JSON: becomes a draft to review
                                       (no PDF, no LLM run; see importer.py)
    GET    /sop/drafts                 unsaved drafts (resume)
    GET    /sop/drafts/{id}            draft + rules (poll for progress)
    PUT    /sop/drafts/{id}            autosave edits; returns re-checked rules
    POST   /sop/drafts/{id}/reparse    run the LLM again over the same pages
    POST   /sop/drafts/{id}/save       write to Postgres + make active for the aircraft
                                       (set-aside rules are saved too, marked set_aside, never used in lessons)
    DELETE /sop/drafts/{id}            discard
    GET    /sop/drafts/{id}/pages/{n}[/image]
  Saved rules (Postgres, edited in place + history)  -> /sop/rules page
    GET    /sop/rulesets[?aircraft=]   GET/PATCH/DELETE /sop/rulesets/{id}   (aircraft=_general: handbook sets)
    POST   /sop/rulesets/{id}/activate | /deactivate
    POST   /sop/rulesets/{id}/rules    PATCH/DELETE /sop/rules/{id}
                                       PATCH {set_aside: true|false} sets a saved rule aside / adds it back
                                       (adding back runs the full check; a set-aside rule may be saved incomplete)
    GET    /sop/rulesets/{id}/history[?rule_id=]
    GET    /sop/documents/{id}/pages/{n}[/image]
  Runtime
    GET    /sop/active/{aircraft}[?lesson_type=normal|emergency|instrument]
                                       enabled rules of the aircraft's active set + active handbook sets

Who made a change comes from the X-SOP-Editor header (the name typed into
the page); there is no PIN on these pages.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from uuid import UUID

import asyncpg  # noqa: F401  -- hard deps: fail at import so web/server.py can
import pymupdf  # noqa: F401  -- skip mounting /sop instead of failing per request
from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from . import extract, importer, suggest, validate
from .drafts import DraftStore
from .llm_parse import OllamaParser
from .pipeline import (HANDBOOK_CHUNK_CHARS, HANDBOOK_NUM_CTX, HANDBOOK_OVERLAP_CHARS, MAX_PDF_MB, POH_NUM_CTX,
                       JobManager)
from .schema import (AOI_ROLES, BOOKS, CAPTURE_AT, DOC_TYPES, GAZE_MEASURES, KINDS, LESSON_TYPES, METRICS, OPS,
                     REF_SPEEDS, SEVERITIES, TAGS, aircraft_profiles, known_phases, missing_ref_speeds, ref_base)
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
    tags: list[str] | None = None
    aoi: str | None = None                        # gaze rules: AOI role (schema.AOI_ROLES)
    gaze_measure: str | None = None               # gaze rules: schema.GAZE_MEASURES
    set_aside: bool | None = None                 # saved rules: kept with the set but not used in lessons
    lesson_types: list[str] | None = None         # normal | emergency | instrument: the lessons that use the rule
    ref_speed: str | None = None                  # airspeed rules: an aircraft speed (schema.REF_SPEEDS) the numbers
    ref_factor: float | None = None               # are measured from, times this factor ("1.3 VSO")


class DraftIn(BaseModel):
    name: str | None = None
    aircraft_profile: str | None = None
    handbook: str | None = None
    rules: list[RuleIn] | None = None


class RunIn(BaseModel):
    pages: str | list[int] | None = None          # '13-20, 34-63' or [13, 14, ...]; None = keep
    aircraft_profile: str | None = None
    doc_type: str | None = None                   # poh | handbook; None = keep
    handbook: str | None = None                   # afh | ifh | phak | school | other; None = keep
    model: str | None = None                      # an installed Ollama model for this run; "" = the configured one


class SaveIn(BaseModel):
    aircraft_profile: str | None = None           # required for an aircraft POH
    handbook: str | None = None                   # training handbook: which book (default: the draft's)
    name: str


class RenameIn(BaseModel):
    name: str


class ModelIn(BaseModel):
    model: str                                    # an installed Ollama model; "" = back to config.yaml's


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
    # Which model drafts rules can be switched on the Upload page without touching config.yaml.
    # The choice is kept in data/sop/settings.json (local to this machine, git-ignored).
    configured_model = parser.model
    coach_model = coach_cfg.get("model", "qwen2.5:14b-instruct")
    settings_file = data_dir / "settings.json"

    def read_settings() -> dict:
        try:
            v = json.loads(settings_file.read_text(encoding="utf-8"))
            return v if isinstance(v, dict) else {}
        except Exception:
            return {}

    def write_settings(v: dict) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        settings_file.write_text(json.dumps(v, indent=1), encoding="utf-8")

    if isinstance(read_settings().get("model"), str) and read_settings()["model"].strip():
        parser.model = read_settings()["model"].strip()
    drafts = DraftStore(data_dir / "drafts")
    drafts.fail_interrupted()

    def is_flying() -> bool:
        if "flying" in state:
            return bool(state["flying"])
        return str(state.get("phase") or "BRIEFING") not in NOT_FLYING_PHASES

    jobs = JobManager(drafts, parser, data_dir, is_flying, tesseract_cmd=tess,
                      ocr_lang=sop_cfg.get("ocr_lang", "eng"),
                      chunk_chars=int(sop_cfg.get("chunk_chars", 6000)),
                      lesson_path=cfg.get("lesson"),
                      max_pdf_mb=float(sop_cfg.get("max_pdf_mb", MAX_PDF_MB)),
                      handbook_num_ctx=int(sop_cfg.get("handbook_num_ctx", HANDBOOK_NUM_CTX)),
                      handbook_chunk_chars=int(sop_cfg.get("handbook_chunk_chars", HANDBOOK_CHUNK_CHARS)),
                      handbook_overlap_chars=int(sop_cfg.get("handbook_overlap_chars", HANDBOOK_OVERLAP_CHARS)),
                      poh_num_ctx=int(sop_cfg.get("poh_num_ctx", POH_NUM_CTX)), coach_model=coach_model)
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
        (db_ok, db_detail), names = await asyncio.gather(store.health(), parser.installed())
        llm_ok = bool(names) and (parser.model in names or f"{parser.model}:latest" in names)
        ocr_ok, ocr_detail = await asyncio.get_running_loop().run_in_executor(
            None, extract.ocr_check, tess, sop_cfg.get("ocr_lang", "eng"))
        return {"db": db_ok, "db_detail": db_detail, "llm": llm_ok, "model": parser.model, "models": names or [],
                "configured_model": configured_model, "coach_model": coach_model,
                "ocr": ocr_ok, "ocr_detail": ocr_detail,
                "busy": jobs.busy, "flying": is_flying(), "max_pdf_mb": jobs.max_pdf_mb}

    @r.put("/model")
    async def set_model(body: ModelIn):
        """Switch the model that drafts rules (kept across restarts). "" = the one in config.yaml."""
        name = body.model.strip()
        if jobs.busy:
            raise HTTPException(409, "A document is being processed - switch the model when it has finished")
        if name:
            names = await parser.installed()
            if names is None:
                raise HTTPException(503, "Ollama isn't reachable - start it, then choose the model")
            if name not in names and f"{name}:latest" not in names:
                raise HTTPException(400, f"'{name}' isn't installed in Ollama. Install it first:  ollama pull {name}")
        parser.model = name or configured_model
        write_settings({**read_settings(), "model": name or None})
        return {"model": parser.model, "configured_model": configured_model, "coach_model": coach_model}

    @r.get("/meta")
    async def meta():
        return {
            "phases": known_phases(), "kinds": list(KINDS), "ops": list(OPS),
            "severities": list(SEVERITIES), "capture_at": list(CAPTURE_AT),
            "metrics": [{"id": k, "label": v[3], "unit": v[1], "range": v[2]} for k, v in METRICS.items()],
            "tags": [{"id": k, "label": v[0], "help": v[1]} for k, v in TAGS.items()],
            "aoi_roles": [{"id": k, "label": v[0], "help": v[1]} for k, v in AOI_ROLES.items()],
            "gaze_measures": [{"id": k, "label": v[0], "unit": "s" if v[1] == "time" else "share", "help": v[2]}
                              for k, v in GAZE_MEASURES.items()],
            "doc_types": [{"id": k, "label": v} for k, v in DOC_TYPES.items()],
            "lesson_types": [{"id": k, "label": v[0], "help": v[1]} for k, v in LESSON_TYPES.items()],
            "ref_speeds": [{"id": k, "label": v[0], "short": v[1], "keys": list(v[2])} for k, v in REF_SPEEDS.items()],
            "books": [{"id": k, "label": v[0], "short": v[1]} for k, v in BOOKS.items()],
            "aircraft": [{"id": a["id"], "name": a["name"],
                          "v_speeds": (a["profile"].get("v_speeds") or {}),
                          "flaps": (a["profile"].get("flaps") or {}).get("detents_deg"),
                          "sim_match": a["profile"].get("sim_match") or [],
                          # speeds a rule may be measured from that this profile doesn't give
                          "missing_speeds": [REF_SPEEDS[k][1] for k in missing_ref_speeds(a["profile"].get("v_speeds"))]}
                         for a in aircraft_profiles()],
        }

    # ------------------------------------------------------------ drafts
    def get_draft(did: str) -> dict:
        d = drafts.get(did)
        if not d:
            raise HTTPException(404, "No such draft (it may have been saved or discarded)")
        return d

    def page_ctx(d: dict) -> tuple[dict[int, str], set[int]]:
        """The draft's page texts (read once per request, not once per rule)."""
        page_rows = drafts.pages(d["id"])
        return ({p["page_no"]: p["text"] for p in page_rows},
                {p["page_no"] for p in page_rows if p["source"] == "ocr"})

    def recheck(d: dict, rule: dict, nominal=None, ctx=None) -> dict:
        pages, ocr = ctx if ctx is not None else page_ctx(d)
        issues, fatal = validate.assess(rule, known_phases(), pages, ocr, nominal)
        rule["tags"] = validate.normalise_tags(rule.get("tags"), rule.get("kind"), rule.get("metric")) \
            if not rule.get("tags") else [t for t in rule["tags"] if t in TAGS]
        if rule.get("include"):
            rule.pop("auto_aside", None)          # the reviewer put it back in use
        elif rule.get("auto_aside") and not (fatal and str(fatal) in rule["auto_aside"]):
            # why the automatic checks set it aside (said once: not again as a plain warning,
            # and not when it only repeats what already blocks the rule)
            issues = ["Set aside: " + rule["auto_aside"]] + [i for i in issues if i != rule["auto_aside"]]
        rule["issues"], rule["blocked"] = issues, fatal
        rule["suggestion"] = offer(rule)
        return rule

    def offer(rule: dict) -> dict | None:
        """The FAA's number for a rule that lacks its tolerance / threshold, for the 'Fill in' button."""
        s = suggest.suggest(rule)
        return {**s, "text": suggest.describe(s), "note": suggest.note_with(rule.get("note"), s)} if s else None

    @r.post("/drafts")
    async def upload(file: UploadFile = File(...), doc_type: str = Form("poh"), handbook: str | None = Form(None),
                     x_sop_editor: str | None = Header(None)):
        if is_flying():
            raise HTTPException(409, "Finish or quit the current lesson before processing an SOP")
        try:
            jobs.check_upload(doc_type, handbook or None)
            if file.size is not None and file.size > jobs.max_pdf_bytes:
                raise jobs.too_big()
            sha, path = await jobs.receive_upload(file.read)      # streamed to disk in 1 MB blocks
            return jobs.start_stored(Path(file.filename or "sop.pdf").name, sha, path, x_sop_editor,
                                     doc_type=doc_type, handbook=handbook or None)
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))

    async def link_source(d: dict, rules: list[dict], f: dict) -> str | None:
        """Find the PDF an imported file's rules came from on this machine, so
        quotes can be checked and pages shown: (a) another draft that read the
        same PDF, (b) a document saved before. Matched by the PDF's SHA-256
        when the file carries it, else by file name - and then only if most of
        the quotes really are in that PDF."""
        sha, name = f.get("source_sha256"), f.get("source_pdf")
        if not sha and not name:
            return None

        def right_pdf(rows) -> bool:
            return bool(sha) or importer.quotes_found(rules, {p["page_no"]: p["text"] for p in rows}) >= 0.5

        for o in drafts.list():
            od = drafts.get(o["id"])
            if not od or od["id"] == d["id"] or not od.get("sha256"):
                continue
            if (od["sha256"] != sha) if sha else (od.get("filename") != name):
                continue
            rows = drafts.pages(od["id"])
            if not rows or not right_pdf(rows):
                continue
            drafts.set_pages(d["id"], rows)
            d.update({k: od.get(k) for k in ("sha256", "pdf_path", "outline", "chapters", "suggested_pages",
                                              "scanned_pages", "handbook_detected")},
                     page_count=od.get("page_count") or len(rows))
            return "another draft of the same PDF on this machine"
        try:
            doc = await store.find_document(sha, name)
            if doc:
                rows = await store.document_pages(doc["id"])
                if rows and right_pdf(rows):
                    drafts.set_pages(d["id"], rows)
                    d.update(sha256=doc["sha256"], pdf_path=doc["stored_path"], page_count=doc["page_count"])
                    return "the PDF saved with an earlier rule set"
        except DBUnavailable:
            pass
        return None

    @r.post("/drafts/import")
    async def import_rules(file: UploadFile = File(...), x_sop_editor: str | None = Header(None)):
        """A rules file made with Export > JSON -> a draft to review and save."""
        raw = await file.read(importer.MAX_IMPORT_MB * 1024 * 1024 + 1)
        fname = Path(file.filename or "rules.json").name
        try:
            if len(raw) > importer.MAX_IMPORT_MB * 1024 * 1024:
                raise ValueError(f"That rules file is larger than {importer.MAX_IMPORT_MB} MB")
            if raw.startswith(b"%PDF"):
                raise ValueError("That is a PDF, not a rules file - drop it on the upload box instead")
            data = importer.read_export(raw)
            f, warns = importer.draft_fields(data, fname)
        except ValueError as e:
            raise HTTPException(400, str(e))
        rules, skipped, relabelled = [], 0, 0
        for x in data["rules"]:
            rule, _notes = importer.rule_from_export(x)
            if rule is None:
                skipped += 1
                continue
            # Files drafted with prompt H4 marked some Instrument Flying Handbook rules "normal + instrument".
            # Instrument-handbook rules are for instrument lessons only now.
            if f["handbook"] == "ifh" and f["prompt_version"] == "H4" and rule["origin"] == "llm" \
                    and rule["lesson_types"] == ["normal", "instrument"]:
                rule["lesson_types"] = ["instrument"]
                relabelled += 1
            rules.append(rule)
        if not rules:
            raise HTTPException(400, "That rules file contains no rules")
        d = drafts.create(f["filename"], None, None, x_sop_editor, f["doc_type"], f["handbook"])
        d.update(name=f["name"], aircraft_profile=f["aircraft_profile"], llm_model=f["llm_model"],
                 prompt_version=f["prompt_version"], selected_pages=f["selected_pages"] or [],
                 imported_from=fname, imported_exported_at=f["exported_at"])
        linked = await link_source(d, rules, f)
        ctx, nominal = page_ctx(d), jobs.nominal_for_draft(d)
        d["rules"] = [recheck(d, x, nominal, ctx) for x in rules]
        # set aside as "quote not found" when the file was made, and the quote IS in the document
        # (a newer MAVIS matches quotes that were shortened with "..." piece by piece)
        refound = 0
        if linked:
            for x in rules:
                if not x["include"] and str(x.get("auto_aside") or "").startswith("quote not found") \
                        and (x.get("quote_score") or 0) >= validate.QUOTE_MIN:
                    x["auto_aside"] = ("the quote was not found when this file was exported; it is found in the document "
                                       "now. Check the rule, then add it back")
                    recheck(d, x, nominal, ctx)
                    refound += 1
        n_use = sum(1 for x in rules if x["include"])
        n_fix = sum(1 for x in rules if x["include"] and x["blocked"])
        msg = (f"Imported from {fname}: {len(rules)} rules ({n_use} in use, {len(rules) - n_use} set aside). "
               "Nothing is saved until you press Save.")
        if linked:
            msg += f" Quotes were checked against {linked}."
        else:
            msg += (" The PDF these rules came from isn't on this machine, so the quotes can't be checked "
                    "against its pages and no page image is shown.")
        warns.insert(0, msg)
        if n_fix:
            warns.append(f"{n_fix} rule(s) marked 'in use' in the file don't pass the checks - fix them or set them aside.")
        if refound:
            warns.append(f"{refound} rule(s) were set aside as 'quote not found' when this file was exported, and their "
                         "quotes are found in the document now. They are still set aside: check them under 'Set aside' "
                         "and add back the ones you want.")
        if relabelled:
            warns.append(f"{relabelled} rule(s) from the Instrument Flying Handbook were marked 'normal + instrument' by the "
                         "earlier version; they are now instrument-flying lessons only.")
        if skipped:
            warns.append(f"{skipped} entr{'y' if skipped == 1 else 'ies'} in the file had no rule kind and were skipped.")
        d.update(status="ready", stage="done", warnings=warns,
                 detail=f"{len(rules)} rules imported" + (f", {len(rules) - n_use} set aside" if len(rules) > n_use else ""))
        return with_checks(drafts.put(d))

    @r.get("/drafts")
    async def list_drafts():
        return drafts.list()

    def with_checks(d: dict) -> dict:
        """Cross-check the included rules against the chosen aircraft's profile
        (speeds above Vne / below Vs0, flaps beyond the last detent...)."""
        warns = []
        used = [x for x in d.get("rules", []) if x.get("include")]
        for a in aircraft_profiles():
            if d.get("doc_type") != "handbook" and a["id"] == d.get("aircraft_profile"):
                warns = validate.aircraft_checks(used, a["profile"])
            # a rule that names an aircraft speed is skipped in an aircraft whose profile doesn't give it
            if d.get("doc_type") == "handbook" or a["id"] == d.get("aircraft_profile"):
                missing = sorted({REF_SPEEDS[x["ref_speed"]][1] for x in used if x.get("ref_speed") in REF_SPEEDS
                                  and ref_base(x, a["profile"].get("v_speeds")) is None})
                if missing:
                    warns.append(f"The {a['name']} profile has no {', '.join(missing)}: rules that use "
                                 f"{'it' if len(missing) == 1 else 'them'} are skipped in that aircraft until the speed is "
                                 f"added under v_speeds in profiles/aircraft/{a['id']}.yaml")
        has_pdf = bool(d.get("pdf_path")) and Path(d["pdf_path"]).is_file()
        # the LLM can only (re-)run where the PDF is on this machine: not for an imported rules file
        return {**d, "aircraft_warnings": warns, "has_pdf": has_pdf,
                "can_rerun": has_pdf or not d.get("imported_from")}

    @r.get("/drafts/{did}")
    async def draft(did: str):
        return with_checks(get_draft(did))

    @r.put("/drafts/{did}")
    async def update_draft(did: str, body: DraftIn):
        d = get_draft(did)
        if d["status"] == "processing":
            raise HTTPException(409, "Still processing - wait until the rules are drafted")
        if d["status"] != "ready":
            body.rules = None                     # choose-pages / failed: only name + aircraft can change
        if body.name is not None:
            d["name"] = body.name.strip()[:120] or d["name"]
        if body.aircraft_profile is not None:
            d["aircraft_profile"] = body.aircraft_profile or None
        if body.handbook is not None and d.get("doc_type") == "handbook":
            if body.handbook not in BOOKS:
                raise HTTPException(400, f"Unknown handbook '{body.handbook}'")
            d["handbook"] = body.handbook
        if body.rules is not None:
            old = {x["cid"]: x for x in d["rules"]}
            nominal, ctx = jobs.nominal_for_draft(d), page_ctx(d)
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
                rule.setdefault("lesson_types", ["normal"])
                rule.setdefault("severity", "caution")
                rule.setdefault("title", "New rule")
                rule.setdefault("kind", "limit")
                # once a number was changed by hand, the LLM's as-written values no
                # longer describe the rule (no "number not in quote" check on it)
                if prev.get("as_written") and any(prev.get(k) != rule.get(k) for k in _NUMS):
                    rule.pop("as_written", None)
                # a pre-filled FAA suggestion the reviewer changed is the reviewer's number now
                sg = prev.get("suggested")
                if sg and any(rule.get(k) != v for k, v in (sg.get("fields") or {}).items()):
                    rule.pop("suggested", None)
                    if str(rule.get("auto_aside") or "").startswith(str(sg.get("what"))):
                        rule.pop("auto_aside", None)
                new_rules.append(recheck(d, rule, nominal, ctx))
            d["rules"] = new_rules
        return with_checks(drafts.put(d))

    @r.post("/drafts/{did}/run")
    @r.post("/drafts/{did}/reparse")
    async def run(did: str, body: RunIn | None = None):
        """Draft rules from the chosen pages (first run after choosing pages, or
        'Run again'). Rules added by hand are kept; LLM rules are re-drafted."""
        if is_flying():
            raise HTTPException(409, "Finish or quit the current lesson first")
        body = body or RunIn()
        if body.aircraft_profile:
            profile(body.aircraft_profile)
        try:
            return jobs.start_run(did, body.pages, body.aircraft_profile, body.doc_type, body.handbook, body.model)
        except KeyError:
            raise HTTPException(404, "No such draft")
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))

    saving: set[str] = set()      # drafts being written to the database right now

    @r.post("/drafts/{did}/save")
    async def save(did: str, body: SaveIn, x_sop_editor: str | None = Header(None)):
        d = get_draft(did)
        if d["status"] != "ready":
            raise HTTPException(409, "This draft isn't ready to save yet")
        if did in saving:
            # Save pressed again (or retried after a time-out) while the first one is still
            # running: a second copy of the rule set must not be written
            raise HTTPException(409, "This draft is already being saved - look under Saved rules in a moment")
        saving.add(did)
        try:
            return await save_now(d, did, body, x_sop_editor)
        finally:
            saving.discard(did)

    async def save_now(d: dict, did: str, body: SaveIn, x_sop_editor: str | None) -> dict:
        handbook = None
        if d.get("doc_type") == "handbook":
            handbook = body.handbook or d.get("handbook") or "other"
            if handbook not in BOOKS:
                raise HTTPException(400, f"Unknown handbook '{handbook}'")
        else:
            profile(body.aircraft_profile)
        nominal, ctx = jobs.nominal_for_draft(d), page_ctx(d)   # a rule set aside by the circuit check keeps that reason
        for rule in d["rules"]:
            recheck(d, rule, nominal, ctx)
        chosen = [x for x in d["rules"] if x.get("include")]
        blocked = [{"cid": x["cid"], "title": x.get("title"), "reason": x["blocked"]} for x in chosen if x.get("blocked")]
        if blocked:
            drafts.put(d)
            raise HTTPException(422, {"message": f"{len(blocked)} rule(s) still need fixing", "blocked": blocked})
        if not d["rules"]:
            raise HTTPException(422, {"message": "There are no rules to save", "blocked": []})
        # A set may be saved with every rule set aside (e.g. scan-path rules that all
        # still need a threshold): nothing is used in lessons until rules are added back.
        # Rules in use and set-aside rules are both saved, in draft order. A
        # set-aside rule keeps its reason and may be incomplete; it is never
        # used in a lesson until it is added back on the SOP Rules page.
        rows = []
        for x in d["rules"]:
            aside = not x.get("include")
            src = validate.storable(x) if aside else x
            rows.append({k: src.get(k) for k in RULE_FIELDS}
                        | {"set_aside": aside, "set_aside_reason": validate.aside_reason(x) if aside else None,
                           "origin": x.get("origin", "manual"), "llm_draft": x.get("llm_draft")})
        rs = await db(store.save_draft(d, drafts.pages(did), rows, None if handbook else body.aircraft_profile,
                                       body.name.strip()[:120] or d["name"], x_sop_editor, handbook=handbook))
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
            for part in validate.quote_parts(q):       # a quote shortened with "...": each piece
                words = part.split()
                for n in (len(words), 12, 8, 5, 3):   # longest findable prefix of the piece
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
        return await png(get_draft(did).get("pdf_path") or "", page_no, q)

    # ------------------------------------------------------------ saved rule sets
    async def check_saved(rule: dict, head: dict, verb: str = "save") -> dict:
        """Full check of a saved rule as edited. A rule IN USE must pass (422
        otherwise). A SET-ASIDE rule may be saved with problems: they come back
        as `blocked` (what stops it being added back) instead of an error."""
        pages, ocr = await db(store.page_texts(head["document_id"]))
        if rule.get("tags") is not None:
            rule["tags"] = [t for t in rule["tags"] if t in TAGS] or \
                validate.normalise_tags(None, rule.get("kind"), rule.get("metric"))
        nominal = jobs.nominal_for_draft({"doc_type": "handbook"} if head.get("scope") == "general"
                                         else {"aircraft_profile": head.get("aircraft_profile")})
        issues, fatal = validate.assess(rule, known_phases(), pages, ocr, nominal)
        if fatal and not rule.get("set_aside"):
            raise HTTPException(422, {"message": f"Can't {verb}: {fatal}", "issues": issues})
        return {"issues": issues, "blocked": fatal}

    @r.get("/rulesets")
    async def rulesets(aircraft: str | None = None):
        return await db(store.list_sets(aircraft))

    @r.get("/rulesets/{rs}")
    async def ruleset(rs: UUID):
        out = await db(store.get_set(rs))
        if not out:
            raise HTTPException(404, "No such rule set")
        for x in out["rules"]:              # set-aside rules that only wait for a number: the FAA's, to fill in
            x["suggestion"] = offer(x) if x.get("set_aside") else None
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
                "lesson_types": ["normal"], **rule, "origin": "manual"}
        if rule["kind"] == "gaze":
            rule.pop("metric", None)
        rule.setdefault("tags", validate.normalise_tags(None, rule["kind"], rule.get("metric")))
        chk = await check_saved(rule, head)
        rule.pop("origin")
        return {**await db(store.add_rule(rs, rule, x_sop_editor)), "issues": chk["issues"]}

    @r.patch("/rules/{rule_id}")
    async def patch_rule(rule_id: int, body: RuleIn, x_sop_editor: str | None = Header(None)):
        cur = await db(store.get_rule(rule_id))
        if not cur:
            raise HTTPException(404, "No such rule")
        head = await db(store.get_set(UUID(cur["rule_set_id"])))
        changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if k in RULE_FIELDS}
        changes.pop("set_aside_reason", None)             # computed here, never taken from the client
        if changes.get("set_aside") is None:
            changes.pop("set_aside", None)
        merged = {**cur, **changes}
        was_aside, now_aside = bool(cur.get("set_aside")), bool(merged.get("set_aside"))
        chk = await check_saved(merged, head, "add this rule back" if was_aside and not now_aside else "save")
        if now_aside:
            # why it is (still) set aside: what blocks it now; else the circuit
            # check; else it was the reviewer's choice
            if not was_aside:
                reason = validate.BY_REVIEWER + (f" ({x_sop_editor})" if x_sop_editor else "")
            elif chk["blocked"] or not str(cur.get("set_aside_reason") or "").startswith(validate.BY_REVIEWER):
                reason = validate.aside_reason(merged, chk["blocked"], chk["issues"])
                if reason == validate.BY_REVIEWER:
                    reason = "Nothing is blocking this rule now - it can be added back"
            else:
                reason = cur.get("set_aside_reason")
            merged["set_aside_reason"] = reason
            merged = {**merged, **{k: v for k, v in validate.storable(merged).items() if k in RULE_FIELDS}}
        else:
            merged["set_aside_reason"] = None
        for k in ("set_aside_reason", "severity", "title", "kind"):
            if merged.get(k) != cur.get(k):
                changes[k] = merged[k]
        for k in ("phases", "source_page", "quote_score", "tags",       # normalised by the check
                  "metric", "target", "tol_hi", "tol_lo", "min_value", "max_value", "op", "value",
                  "gate_phase", "capture_at", "conditions", "aoi", "gaze_measure",    # cleared on a kind change
                  "lesson_types", "ref_speed", "ref_factor"):
            if merged.get(k) != cur.get(k):
                changes[k] = merged[k]
        out = {**await db(store.update_rule(rule_id, changes, x_sop_editor)), "issues": chk["issues"],
               "blocked": chk["blocked"]}
        out["suggestion"] = offer(out) if out.get("set_aside") else None
        return out

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
    async def active(aircraft: str, lesson_type: str | None = None):
        """lesson_type=normal|emergency|instrument: only the rules used in that kind of lesson."""
        if lesson_type is not None and lesson_type not in LESSON_TYPES:
            raise HTTPException(400, f"Unknown lesson type '{lesson_type}'")
        out = await db(store.active_rules(aircraft, lesson_type))
        if out is None:
            raise HTTPException(404, "No active rules for this aircraft (and no active training handbook)")
        return out

    return r, jobs, store
