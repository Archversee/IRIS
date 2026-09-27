"""Upload -> extract (OCR where needed) -> local LLM -> validated DRAFT.

Runs as a background asyncio task per upload; progress is written into the
draft file, which the /sop/new page polls. One job at a time (it shares the
GPU with live coaching), and LLM work pauses between sections while a lesson
is being flown. The result is only a draft -- Postgres is untouched until
the instructor saves it.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from . import extract, validate
from .drafts import DraftStore
from .llm_parse import OllamaParser, system_prompt
from .runtime import match_profile
from .schema import aircraft_profiles, known_phases, llm_json_schema

log = logging.getLogger(__name__)

MAX_PDF_BYTES = 50 * 1024 * 1024


def draft_rule(ch: validate.Checked, raw: dict) -> dict:
    """A validated candidate as it sits in a draft (stored units)."""
    r = dict(ch.rule)
    r.update({"cid": uuid.uuid4().hex[:12], "origin": "llm", "llm_draft": raw,
              "include": ch.reject_reason is None, "blocked": ch.reject_reason,
              "issues": ch.issues, "enabled": True, "note": None})
    r.setdefault("conditions", [])
    r.setdefault("phases", [])
    return r


class JobManager:
    def __init__(self, drafts: DraftStore, parser: OllamaParser, pdf_dir: Path, is_flying,
                 tesseract_cmd: str | None = None, ocr_lang: str = "eng", chunk_chars: int = 6000):
        self.drafts = drafts
        self.parser = parser
        self.pdf_dir = Path(pdf_dir)
        self.is_flying = is_flying
        self.tesseract_cmd = tesseract_cmd or None
        self.ocr_lang = ocr_lang
        self.chunk_chars = chunk_chars
        self._task: asyncio.Task | None = None

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

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
        self._task = asyncio.create_task(self._run(d["id"], data))
        return d

    def start_reparse(self, did: str) -> dict:
        """Run the LLM again over the draft's already-extracted pages."""
        if self.busy:
            raise RuntimeError("Another SOP is still being processed - wait for it to finish")
        d = self.drafts.get(did)
        if not d:
            raise KeyError(did)
        if not self.drafts.pages(did):
            raise ValueError("This draft has no extracted pages - upload the PDF again")
        d.update(status="processing", stage="queued", detail="", error=None, done=0, total=0)
        self.drafts.put(d)
        self._task = asyncio.create_task(self._run(did, None))
        return d

    async def _run(self, did: str, data: bytes | None):
        loop = asyncio.get_running_loop()
        d = self.drafts.get(did)

        def save(**kw):
            d.update(kw)
            self.drafts.put(d)

        try:
            if data is not None:
                save(stage="extracting", detail="Reading pages")

                def prog(i, n):
                    d.update(done=i, total=n, detail=f"Reading page {i} of {n}")
                    self.drafts.put(d)
                pages, warns = await loop.run_in_executor(
                    None, lambda: extract.extract_pages(data, self.tesseract_cmd, self.ocr_lang, prog))
                if not any(p.text.strip() for p in pages):
                    raise ValueError("No text could be read from this PDF"
                                     + ("" if extract.ocr_available(self.tesseract_cmd)
                                        else " (it looks scanned and Tesseract OCR is not installed)"))
                self.drafts.set_pages(did, [p.__dict__ for p in pages])
                save(warnings=d["warnings"] + warns, page_count=len(pages),
                     ocr_pages=[p.page_no for p in pages if p.source == "ocr"])
            page_rows = self.drafts.pages(did)
            page_map = {p["page_no"]: p["text"] for p in page_rows}
            ocr_pages = {p["page_no"] for p in page_rows if p["source"] == "ocr"}

            # best guess of the aircraft from the SOP's own wording (reviewer confirms it)
            if not d.get("aircraft_profile"):
                head = " ".join(p["text"] for p in page_rows[:3])[:20000]
                guess, _ = match_profile({"text": head}, aircraft_profiles())
                if guess:
                    save(aircraft_profile=guess)

            if not await self.parser.available():
                save(status="ready", stage="done", detail="LLM unavailable - add rules by hand",
                     warnings=d["warnings"] + [
                         f"Local LLM '{self.parser.model}' is not available in Ollama - no rules were drafted. "
                         "Add rules by hand, or start Ollama and press 'Run LLM again'."])
                return

            pages = [extract.PageText(p["page_no"], p["text"], p["source"]) for p in page_rows]
            chunks = extract.make_chunks(pages, self.chunk_chars)
            phases = known_phases()
            schema = llm_json_schema(phases)
            system = system_prompt(phases, [a["name"] for a in aircraft_profiles()])
            save(stage="parsing", done=0, total=len(chunks), llm_model=self.parser.model)
            candidates: list[dict] = []
            for ch in chunks:
                while self.is_flying():
                    if d["stage"] != "paused":
                        save(stage="paused", detail="Paused while a lesson is being flown")
                    await asyncio.sleep(2.0)
                save(stage="parsing", detail=f"Section {ch.index + 1} of {len(chunks)} (page {ch.pages[0]}"
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
                            d["warnings"].append(f"Pages {ch.pages[0]}-{ch.pages[-1]}: the LLM failed ({e}); "
                                                 "check this section by hand")
                save(done=ch.index + 1)

            save(stage="validating", detail=f"Checking {len(candidates)} candidate rules")
            checked = [(validate.validate_candidate(c, phases, page_map, ocr_pages), c) for c in candidates]
            kept = validate.dedupe([c for c, _ in checked])
            kept_ids = {id(c) for c in kept}
            rules = [draft_rule(c, raw) for c, raw in checked if id(c) in kept_ids]
            for r in rules:        # same full check the page and Save use (e.g. band needs a tolerance)
                if r["include"]:
                    issues, fatal = validate.assess(r, phases, page_map, ocr_pages)
                    r["issues"] = list(dict.fromkeys(r["issues"] + issues))
                    r["blocked"] = fatal
            rules.sort(key=lambda r: (r.get("source_page") or 10 ** 6))
            manual = [r for r in d.get("rules", []) if r.get("origin") == "manual"]   # keep hand-added ones on re-run
            n_ok = sum(1 for r in rules if r["include"])
            save(rules=rules + manual, status="ready", stage="done",
                 detail=f"{n_ok} rules drafted" + (f", {len(rules) - n_ok} set aside" if len(rules) > n_ok else ""))
        except Exception as e:
            log.exception("SOP draft %s failed", did)
            save(status="failed", stage="failed", error=str(e))
