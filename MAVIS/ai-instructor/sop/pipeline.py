"""Orchestrates one upload: extract -> chunk -> LLM parse -> validate -> store.

Runs as a background asyncio task so the upload request returns at once; the
wizard polls the in-memory job for progress. One job at a time (it shares
the GPU with live coaching), and parsing pauses between chunks whenever a
flight is in progress.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import extract, validate
from .llm_parse import OllamaParser, system_prompt
from .schema import aircraft_profiles, known_phases, llm_json_schema
from .store import RuleStore

log = logging.getLogger(__name__)

MAX_PDF_BYTES = 50 * 1024 * 1024


@dataclass
class Job:
    id: str
    filename: str
    by: str | None
    stage: str = "queued"          # queued|extracting|parsing|paused|validating|done|failed
    detail: str = ""
    done: int = 0
    total: int = 0
    document_id: str | None = None
    ruleset_id: str | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    counts: dict = field(default_factory=dict)
    started: float = field(default_factory=time.time)
    finished: float | None = None

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["elapsed_s"] = round((self.finished or time.time()) - self.started, 1)
        return d


class JobManager:
    def __init__(self, store: RuleStore, parser: OllamaParser, pdf_dir: Path, is_flying,
                 tesseract_cmd: str | None = None, ocr_lang: str = "eng", chunk_chars: int = 6000):
        self.store = store
        self.parser = parser
        self.pdf_dir = Path(pdf_dir)
        self.is_flying = is_flying
        self.tesseract_cmd = tesseract_cmd or None
        self.ocr_lang = ocr_lang
        self.chunk_chars = chunk_chars
        self.jobs: dict[str, Job] = {}
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    # ------------------------------------------------------------ entry points
    def start_upload(self, filename: str, data: bytes, by: str | None) -> Job:
        if self.busy:
            raise RuntimeError("Another SOP is still being processed - wait for it to finish")
        if len(data) > MAX_PDF_BYTES:
            raise ValueError("PDF is larger than 50 MB")
        if not data.startswith(b"%PDF"):
            raise ValueError("That file is not a PDF")
        job = Job(uuid.uuid4().hex, filename, by)
        self.jobs[job.id] = job
        self._task = asyncio.create_task(self._run(job, data=data))
        return job

    def start_reparse(self, document_id: str, filename: str, by: str | None) -> Job:
        if self.busy:
            raise RuntimeError("Another SOP is still being processed - wait for it to finish")
        job = Job(uuid.uuid4().hex, filename, by, document_id=document_id)
        self.jobs[job.id] = job
        self._task = asyncio.create_task(self._run(job, data=None))
        return job

    # ------------------------------------------------------------ the pipeline
    async def _run(self, job: Job, data: bytes | None):
        loop = asyncio.get_running_loop()
        rs_id = None
        try:
            if data is not None:
                sha = extract.sha256_bytes(data)
                existing = await self.store.find_document(sha)
                if existing:
                    job.document_id = str(existing["id"])
                    job.warnings.append("This exact PDF was uploaded before - re-using its extracted text")
                else:
                    job.stage, job.detail = "extracting", "Reading pages"

                    def prog(i, n):
                        job.done, job.total = i, n
                        job.detail = f"Reading page {i} of {n}"
                    pages, warns = await loop.run_in_executor(
                        None, lambda: extract.extract_pages(data, self.tesseract_cmd, self.ocr_lang, prog))
                    job.warnings += warns
                    if not any(p.text.strip() for p in pages):
                        raise ValueError("No text could be read from this PDF"
                                         + ("" if extract.ocr_available(self.tesseract_cmd)
                                            else " (it looks scanned and Tesseract OCR is not installed)"))
                    self.pdf_dir.mkdir(parents=True, exist_ok=True)
                    path = self.pdf_dir / f"{sha}.pdf"
                    path.write_bytes(data)
                    doc_id = await self.store.create_document(
                        job.filename, sha, len(pages), [p.page_no for p in pages if p.source == "ocr"],
                        str(path), job.by, pages)
                    job.document_id = str(doc_id)

            doc_uuid = uuid.UUID(job.document_id)
            page_map, ocr_pages = await self.store.page_texts(doc_uuid)
            pages = [extract.PageText(n, t, "ocr" if n in ocr_pages else "text")
                     for n, t in sorted(page_map.items())]
            chunks = extract.make_chunks(pages, self.chunk_chars)

            rs_id = await self.store.create_ruleset(doc_uuid, job.by, self.parser.model)
            job.ruleset_id = str(rs_id)

            if not await self.parser.available():
                msg = (f"Local LLM '{self.parser.model}' is not available in Ollama - "
                       "no rules were drafted. You can add rules by hand, or start Ollama and re-parse.")
                job.warnings.append(msg)
                await self.store.set_status(rs_id, "draft", msg)
                job.stage, job.detail = "done", "LLM unavailable - manual entry"
                return

            phases = known_phases()
            schema = llm_json_schema(phases)
            system = system_prompt(phases, [a["name"] for a in aircraft_profiles()])
            job.stage, job.done, job.total = "parsing", 0, len(chunks)
            await self.store.set_progress(rs_id, 0, len(chunks))
            candidates: list[dict] = []
            for ch in chunks:
                while self.is_flying():
                    job.stage, job.detail = "paused", "Paused while a flight is in progress"
                    await asyncio.sleep(2.0)
                job.stage = "parsing"
                job.detail = f"Section {ch.index + 1} of {len(chunks)} (page {ch.pages[0]}" + \
                             (f"-{ch.pages[-1]})" if len(ch.pages) > 1 else ")")
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
                            job.warnings.append(f"Pages {ch.pages[0]}-{ch.pages[-1]}: LLM failed ({e}); "
                                                "check this section by hand")
                job.done = ch.index + 1
                await self.store.set_progress(rs_id, job.done, len(chunks))

            job.stage, job.detail = "validating", f"Checking {len(candidates)} candidate rules"
            checked = [validate.validate_candidate(c, phases, page_map, ocr_pages) for c in candidates]
            checked = validate.dedupe(checked)
            await self.store.insert_rules(rs_id, checked)
            await self.store.set_status(rs_id, "draft")
            job.counts = {"draft": sum(1 for c in checked if c.status == "draft"),
                          "auto_rejected": sum(1 for c in checked if c.status == "auto_rejected")}
            job.stage, job.detail = "done", f"{job.counts['draft']} rules drafted for review"
        except Exception as e:
            log.exception("SOP job %s failed", job.id)
            job.stage, job.error = "failed", str(e)
            if rs_id:
                try:
                    await self.store.set_status(rs_id, "failed", str(e))
                except Exception:
                    pass
        finally:
            job.finished = time.time()
