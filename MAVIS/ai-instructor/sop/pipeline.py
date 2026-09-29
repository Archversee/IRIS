"""Upload -> read pages -> (choose pages) -> OCR + local LLM -> validated DRAFT.

  1. upload       text layer of every page + POH section detection (fast, no OCR)
  2. choose pages short documents skip this; longer ones stop at status
                  "choose_pages" with Limitations + Normal Procedures suggested
  3. run          OCR only the chosen scanned pages, LLM over the chosen pages,
                  deterministic checks, set aside rules a correct circuit breaks

Runs as a background asyncio task; progress is written into the draft file,
which the /sop/new page polls. One job at a time (it shares the GPU with live
coaching); LLM work pauses between sections while a lesson is being flown.
Nothing is written to Postgres until the instructor saves the draft.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from . import extract, validate
from .drafts import DraftStore
from .llm_parse import PROMPT_VERSION, OllamaParser, system_prompt
from .profile import conflict_message, conflicts, load_nominal
from .runtime import match_profile
from .schema import aircraft_profiles, known_phases, llm_json_schema

log = logging.getLogger(__name__)

MAX_PDF_BYTES = 50 * 1024 * 1024
AUTO_ALL_PAGES = 40        # documents up to this many pages skip the choose-pages step


def draft_rule(ch: validate.Checked, raw: dict) -> dict:
    """A validated candidate as it sits in a draft (stored units)."""
    r = dict(ch.rule)
    r.update({"cid": uuid.uuid4().hex[:12], "origin": "llm", "llm_draft": raw,
              "include": ch.reject_reason is None, "blocked": ch.reject_reason,
              "issues": ch.issues, "enabled": True, "note": None})
    r.setdefault("conditions", [])
    r.setdefault("phases", [])
    r.setdefault("tags", [])
    return r


class JobManager:
    def __init__(self, drafts: DraftStore, parser: OllamaParser, pdf_dir: Path, is_flying,
                 tesseract_cmd: str | None = None, ocr_lang: str = "eng", chunk_chars: int = 6000,
                 lesson_path: str | None = None):
        self.drafts = drafts
        self.parser = parser
        self.pdf_dir = Path(pdf_dir)
        self.is_flying = is_flying
        self.tesseract_cmd = tesseract_cmd or None
        self.ocr_lang = ocr_lang
        self.chunk_chars = chunk_chars
        self.lesson_path = lesson_path
        self._task: asyncio.Task | None = None

    def _chunk_budget(self, system: str) -> int:
        """Largest section that still fits the model's context window next to the
        prompt and the answer (~3.5 characters per token, 10% margin)."""
        num_ctx = int(getattr(self.parser, "num_ctx", 8192) or 8192)
        room_tokens = num_ctx - len(system) / 3.5 - 3072 - 200
        return max(1500, min(self.chunk_chars, int(room_tokens * 3.5 * 0.9)))

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    def nominal_for(self, aircraft_id: str | None) -> dict | None:
        return load_nominal(aircraft_id, self.lesson_path)

    # ------------------------------------------------------------ entry points
    def start_upload(self, filename: str, data: bytes, by: str | None) -> dict:
        if self.busy:
            raise RuntimeError("Another SOP is still being processed - wait for it to finish")
        if len(data) > MAX_PDF_BYTES:
            raise ValueError("PDF is larger than 50 MB")
        if not data.startswith(b"%PDF"):
            raise ValueError("That file is not a PDF")
        sha = extract.sha256_bytes(data)
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        path = self.pdf_dir / f"{sha}.pdf"
        if not path.exists():
            path.write_bytes(data)
        d = self.drafts.create(filename, sha, str(path), by)
        self._task = asyncio.create_task(self._read(d["id"], data))
        return d

    def start_run(self, did: str, pages=None, aircraft: str | None = None) -> dict:
        """Run (or re-run) OCR + LLM over the chosen pages. pages: '1-5, 9' or
        a list; None = keep the previous choice (or the suggestion)."""
        if self.busy:
            raise RuntimeError("Another SOP is still being processed - wait for it to finish")
        d = self.drafts.get(did)
        if not d:
            raise KeyError(did)
        rows = self.drafts.pages(did)
        if aircraft is not None:
            d["aircraft_profile"] = aircraft or None
        if not rows or "needs_ocr" not in rows[0]:
            # reading never finished (or a draft from before page selection): start over from the PDF
            pdf = Path(d.get("pdf_path") or "")
            if not pdf.is_file():
                raise ValueError("The PDF for this draft is no longer on this machine - upload it again")
            d.update(status="processing", stage="queued", detail="", error=None, done=0, total=0,
                     selected_pages=extract.parse_pages(pages, 10 ** 6) if pages else d.get("selected_pages"),
                     run_after_read=True)
            self.drafts.put(d)
            self._task = asyncio.create_task(self._read(did, pdf.read_bytes()))
            return d
        n = len(rows)
        chosen = extract.parse_pages(pages, n) if pages is not None else \
            (d.get("selected_pages") or d.get("suggested_pages") or [r["page_no"] for r in rows])
        if not chosen:
            raise ValueError("Choose at least one page")
        d.update(status="processing", stage="queued", detail="", error=None, done=0, total=0,
                 selected_pages=chosen)
        self.drafts.put(d)
        self._task = asyncio.create_task(self._run(did))
        return d

    # ------------------------------------------------------------ helpers
    def _saver(self, d: dict):
        def save(**kw):
            d.update(kw)
            self.drafts.put(d)

        def progress_only(**kw):
            # intermediate writes are cosmetic (the final save writes the whole
            # draft again): a busy file on Windows must never kill the job
            d.update(kw)
            try:
                self.drafts.put(d)
            except OSError as e:
                log.info("progress update skipped: %s", e)
        return save, progress_only

    def _fail(self, d: dict, e: Exception):
        log.exception("SOP draft %s failed", d.get("id"))
        msg = str(e)
        if isinstance(e, PermissionError):
            msg += (" - Windows blocked access to a MAVIS data file (often antivirus or OneDrive "
                    "scanning it). Press 'Run again'; if it keeps happening, move the IRIS folder "
                    "out of Downloads/OneDrive or exclude data/sop from scanning.")
        d.update(status="failed", stage="failed", error=msg)
        try:
            self.drafts.put(d)
        except OSError:
            log.error("could not record failure for draft %s", d.get("id"))

    # ------------------------------------------------------------ step 1: read
    async def _read(self, did: str, data: bytes):
        loop = asyncio.get_running_loop()
        d = self.drafts.get(did)
        save, progress_only = self._saver(d)
        try:
            progress_only(stage="extracting", detail="Reading pages")

            def prog(i, n):
                progress_only(done=i, total=n, detail=f"Reading page {i} of {n}")
            rows = await loop.run_in_executor(None, lambda: extract.read_pages(data, prog))
            if not rows:
                raise ValueError("The PDF has no pages")
            self.drafts.set_pages(did, rows)
            suggested = extract.suggest_pages(rows)
            n_scanned = sum(1 for r in rows if r["needs_ocr"])
            if not d.get("aircraft_profile"):     # best guess from the SOP's own wording (reviewer confirms)
                head = " ".join(r["text"] for r in rows[:5])[:20000]
                guess, _ = match_profile({"text": head}, aircraft_profiles())
                d["aircraft_profile"] = guess
            warns = [w for w in (d.get("warnings") or []) if "look scanned" not in w]
            if n_scanned and not extract.ocr_available(self.tesseract_cmd, self.ocr_lang):
                warns.append(f"{n_scanned} page(s) look scanned but OCR (Tesseract) isn't available - "
                             "those pages can't be read until it is installed.")
            fields = dict(page_count=len(rows), outline=extract.outline(rows), suggested_pages=suggested,
                          scanned_pages=[r["page_no"] for r in rows if r["needs_ocr"]], warnings=warns)
            run_now = d.pop("run_after_read", False) or len(rows) <= AUTO_ALL_PAGES
            if run_now:
                chosen = [p for p in (d.get("selected_pages") or []) if p <= len(rows)] or \
                    ([r["page_no"] for r in rows] if len(rows) <= AUTO_ALL_PAGES else suggested)
                save(**fields, selected_pages=chosen, stage="queued", detail="")
                await self._run(did)
            else:
                save(**fields, status="choose_pages", stage="choose_pages",
                     detail=f"{len(rows)} pages read - choose which to draft rules from")
        except Exception as e:
            self._fail(d, e)

    # ------------------------------------------------------------ step 2: OCR + LLM
    async def _run(self, did: str):
        loop = asyncio.get_running_loop()
        d = self.drafts.get(did)
        save, progress_only = self._saver(d)
        try:
            rows = self.drafts.pages(did)
            chosen = set(d.get("selected_pages") or [r["page_no"] for r in rows])
            warnings = [w for w in (d.get("warnings") or [])
                        if not w.startswith(("Local LLM", "No aircraft chosen", "Pages "))]

            need = [r for r in rows if r["page_no"] in chosen and r.get("needs_ocr") and r["source"] != "ocr"]
            if need:
                pdf = Path(d.get("pdf_path") or "")
                if not pdf.is_file():
                    warnings.append("The original PDF is missing, so scanned pages couldn't be OCR'd")
                else:
                    data = pdf.read_bytes()
                    progress_only(stage="ocr", done=0, total=len(need), detail=f"Reading {len(need)} scanned page(s)")

                    def prog(i, n):
                        progress_only(done=i, total=n, detail=f"OCR page {i} of {n}")
                    warnings += await loop.run_in_executor(None, lambda: extract.ocr_selected(
                        data, rows, chosen, self.tesseract_cmd, self.ocr_lang, prog))
                    self.drafts.set_pages(did, rows)

            page_map = {r["page_no"]: r["text"] for r in rows}
            ocr_pages = {r["page_no"] for r in rows if r["source"] == "ocr"}
            manual = [r for r in d.get("rules", []) if r.get("origin") == "manual"]   # kept on re-run

            if not await self.parser.available():
                save(status="ready", stage="done", detail="LLM unavailable - add rules by hand", warnings=warnings + [
                     f"Local LLM '{self.parser.model}' is not available in Ollama - no rules were drafted. "
                     "Add rules by hand, or start Ollama and press 'Run again'."], rules=manual)
                return

            ac_names = {a["id"]: a["name"] for a in aircraft_profiles()}
            phases = known_phases()
            schema = llm_json_schema(phases)
            system = system_prompt(phases, ac_names.get(d.get("aircraft_profile") or ""))
            pages = [extract.PageText(r["page_no"], r["text"], r["source"]) for r in rows if r["page_no"] in chosen]
            chunks = extract.make_chunks(pages, self._chunk_budget(system))
            progress_only(stage="parsing", done=0, total=len(chunks), llm_model=self.parser.model,
                          prompt_version=PROMPT_VERSION)
            candidates: list[dict] = []
            for ch in chunks:
                while self.is_flying():
                    if d["stage"] != "paused":
                        progress_only(stage="paused", detail="Paused while a lesson is being flown")
                    await asyncio.sleep(2.0)
                progress_only(stage="parsing", detail=f"Section {ch.index + 1} of {len(chunks)} (page {ch.pages[0]}"
                              + (f"-{ch.pages[-1]})" if len(ch.pages) > 1 else ")"))
                for attempt in (1, 2):
                    try:
                        got = await self.parser.parse_chunk(ch.text, system, schema)
                        for g in got:
                            if not g.get("page") and ch.pages:
                                g["page"] = ch.pages[0]
                        candidates += got
                        break
                    except Exception as e:
                        log.warning("chunk %d attempt %d failed: %s", ch.index, attempt, e)
                        if attempt == 2:
                            warnings.append(f"Pages {ch.pages[0]}-{ch.pages[-1]}: the LLM failed ({e}); "
                                            "check this section by hand")
                progress_only(done=ch.index + 1)

            progress_only(stage="validating", detail=f"Checking {len(candidates)} candidate rules")
            nominal = self.nominal_for(d.get("aircraft_profile"))
            checked = [(validate.validate_candidate(c, phases, page_map, ocr_pages), c) for c in candidates]
            kept = validate.dedupe([c for c, _ in checked])
            kept_ids = {id(c) for c in kept}
            rules = [draft_rule(c, raw) for c, raw in checked if id(c) in kept_ids]
            n_conflict = 0
            for r in rules:        # same full check the page and Save use (e.g. band needs a tolerance)
                if not r["include"]:
                    continue
                issues, fatal = validate.assess(r, phases, page_map, ocr_pages)
                r["issues"] = list(dict.fromkeys(r["issues"] + issues))
                r["blocked"] = fatal
                msg = None if fatal else conflict_message(conflicts(r, nominal))
                if msg:            # a correct circuit breaks it: set aside for the reviewer, not deleted
                    r["include"] = False
                    r["issues"].insert(0, "Set aside: " + msg)
                    n_conflict += 1
            rules.sort(key=lambda r: (r.get("source_page") or 10 ** 6))
            n_ok = sum(1 for r in rules if r["include"])
            if nominal is None:
                warnings.append("No aircraft chosen, so rules weren't checked against a correctly flown circuit - "
                                "choose the aircraft and press 'Run again' to check them.")
            aside = len(rules) - n_ok
            save(rules=rules + manual, status="ready", stage="done", warnings=warnings,
                 detail=f"{n_ok} rules drafted" + (f", {aside} set aside" if aside else "")
                 + (f" ({n_conflict} would be broken by a correct circuit)" if n_conflict else ""))
        except Exception as e:
            self._fail(d, e)
