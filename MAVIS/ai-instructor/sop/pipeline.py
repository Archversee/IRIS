"""Upload -> read pages -> (choose pages) -> OCR + local LLM -> validated DRAFT.

  1. upload       text layer of every page + POH section / handbook chapter
                  detection (fast, no OCR). The reviewer says up front whether
                  it's an aircraft POH or a training handbook (doc_type).
  2. choose pages short documents skip this; longer ones stop at status
                  "choose_pages" with Limitations + Normal Procedures (POH) or
                  the circuit / scan chapters (handbook) suggested
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
import re
import time
import uuid
from pathlib import Path

from . import extract, suggest, validate
from .drafts import DraftStore
from .llm_parse import (HANDBOOK_NUM_PREDICT, HANDBOOK_PROMPT_VERSION, NUM_PREDICT, PROMPT_VERSION, OllamaParser,
                        handbook_prompt, is_example_quote, system_prompt)
from .profile import (FLIGHT_PHASES, circuit_conflict, load_nominal, nominals_all, phase_conflicts,
                      with_provisional_tolerance)
from .runtime import match_profile
from .schema import (BOOKS, DOC_TYPES, LESSON_TYPES, NOT_RULE_BOOKS, aircraft_profiles, known_phases,
                     llm_json_schema)

log = logging.getLogger(__name__)

MAX_PDF_MB = 500           # default upload limit (sop.max_pdf_mb); the full FAA handbooks are 100-300 MB
AUTO_ALL_PAGES = 40        # documents up to this many pages skip the choose-pages step
# Training handbooks are prose: a requirement is explained over paragraphs, so
# each LLM call gets ~2 pages (plus the end of the previous section) instead of
# one, in a larger context window. Overridable under `sop:` in config.yaml.
HANDBOOK_NUM_CTX = 16384
# Prompt v3 is longer and every rule now writes all its fields, so a POH run
# also asks for a bigger window than the coach's default (sop.poh_num_ctx).
POH_NUM_CTX = 12288
HANDBOOK_CHUNK_CHARS = 9000
HANDBOOK_OVERLAP_CHARS = 1200


# Wording that marks an emergency procedure even when the LLM forgot the tag and the
# document's sections couldn't be detected (a scanned POH without running headers).
_EMERGENCY_RX = re.compile(
    r"engine\s+fail|forced\s+landing|precautionary\s+landing|emergency\s+(?:landing|descent|procedure|operation)|"
    r"\bditching\b|\b(?:engine|electrical|cabin|wing)\s+fire\b|no\s+power\s+is\s+available|without\s+engine\s+power|"
    r"\bicing\s+encounter|flat\s+(?:main|nose)\s+tire|vacuum\s+(?:system\s+)?failure|static\s+source\s+blockage|"
    r"best\s+glide", re.I)


_GO_AROUND_RX = re.compile(r"go[\s-]?around|balked\s+landing|rejected\s+landing", re.I)


def lesson_types_for(rule: dict, handbook: str | None, in_emergency: bool, in_instrument: bool) -> list[str]:
    """Which kind of lesson a drafted rule belongs to (the reviewer can change it).
    Exactly one, so that nothing meant for one kind of lesson is ever checked in
    another:

      emergency   from the emergency part of the document (POH Section 3 / a
                  chapter titled Emergency, Abnormal), or its title / quote says so
                  ("engine failure", "forced landing"...), or - in a POH - the LLM
                  tagged it "emergency". A go-around is a NORMAL procedure, whatever
                  the LLM tagged it, and in a handbook the tag alone is not enough.
      instrument  from the Instrument Flying Handbook, or from a chapter about
                  flying by instruments: INSTRUMENT LESSONS ONLY. In a visual
                  circuit the pilot looks outside ~90 % of the time, so none of the
                  instrument-scan rules are checked there.
      normal      everything else
    """
    title = rule.get("title") or ""
    said = f"{title} {(rule.get('source_quote') or '')[:160]}"
    tagged = "emergency" in (rule.get("tags") or []) and handbook is None and not _GO_AROUND_RX.search(title)
    if in_emergency or tagged or (_EMERGENCY_RX.search(said) and not _GO_AROUND_RX.search(title)):
        return ["emergency"]
    if handbook == "ifh" or in_instrument:
        return ["instrument"]
    return ["normal"]


def coverage_gaps(rules: list[dict], is_handbook: bool, handbook: str | None) -> list[str]:
    """What a document of this kind normally gives that NO drafted rule covers
    (the local model sometimes simply misses a passage). Names for a warning."""
    def any_rule(pred) -> bool:
        return any(pred(r) for r in rules)
    ias = lambda r: r.get("metric") == "ias" and r.get("kind") in ("band", "limit", "capture", "event")  # noqa: E731
    normal = lambda r: "normal" in (r.get("lesson_types") or ["normal"])                                   # noqa: E731
    in_phase = lambda r, p: p in (r.get("phases") or [])                                                   # noqa: E731
    title = lambda r, rx: bool(re.search(rx, r.get("title") or "", re.I))                                  # noqa: E731
    if not is_handbook:
        want = [
            ("the never-exceed speed", lambda r: ias(r) and r.get("max_value") is not None and not r.get("conditions")
                and not r.get("phases") and normal(r)),
            ("the maximum speed with flaps extended", lambda r: ias(r) and r.get("max_value") is not None
                and any(c.get("metric") == "flaps_deg" for c in r.get("conditions") or [])),
            ("the maximum engine RPM", lambda r: r.get("metric") == "rpm" and r.get("max_value") is not None and normal(r)),
            ("the rotation (lift-off) speed", lambda r: ias(r) and normal(r) and (in_phase(r, "TAKEOFF_ROLL")
                or r.get("capture_at") == "liftoff")),
            ("the climb speed after takeoff", lambda r: ias(r) and normal(r) and in_phase(r, "CLIMB_OUT")),
            ("the approach speed", lambda r: ias(r) and normal(r) and in_phase(r, "FINAL")),
            ("the before-takeoff checklist", lambda r: r.get("kind") == "unobservable" and title(r, r"before\s+take")),
            ("the before-landing checklist", lambda r: r.get("kind") == "unobservable" and title(r, r"before\s+landing")),
        ]
    elif handbook == "afh":
        want = [
            ("the traffic pattern altitude", lambda r: r.get("metric") in ("alt_agl", "alt_msl")),
            ("how much of the time to look outside", lambda r: r.get("kind") == "gaze" and r.get("aoi") == "outside"),
            ("the airspeed to hold on final approach", lambda r: ias(r) and in_phase(r, "FINAL")),
        ]
    elif handbook == "ifh":
        want = [
            ("the scan error 'fixation' (staring at one instrument)", lambda r: r.get("kind") == "gaze" and r.get("gaze_measure") == "max_dwell"),
            ("the scan error 'omission' (leaving an instrument out)", lambda r: r.get("kind") == "gaze" and r.get("gaze_measure") == "max_gap"),
        ]
    else:
        want = []
    return [name for name, pred in want if not any_rule(pred)]


def auto_review(rules: list[dict], *, phases: list[str], page_map: dict[int, str], ocr_pages: set[int], nominal,
                handbook: str | None, emergency: set[int] = frozenset(), instrument: set[int] = frozenset()) -> dict[str, int]:
    """Everything that is decided about freshly drafted rules without a human:
    which lesson type each belongs to, which phases a correct circuit rules out,
    and which rules are set aside (with the reason). handbook: the book id for a
    training handbook, None for an aircraft POH. emergency / instrument: page
    numbers of those parts of the document. Mutates the rules; returns how many
    were set aside, by reason."""
    # ---- lesson type: emergency rules are for emergency lessons, instrument-flying
    # rules for instrument lessons; neither is checked in a normal circuit
    for r in rules:
        r["lesson_types"] = lesson_types_for(r, handbook,
                                             r.get("source_page") in emergency, r.get("source_page") in instrument)
        if r["lesson_types"] == ["emergency"] and "emergency" not in r["tags"]:
            r["tags"] = (r["tags"] + ["emergency"])[-4:] if len(r["tags"]) >= 4 else r["tags"] + ["emergency"]
        elif r["lesson_types"] != ["emergency"] and "emergency" in r["tags"]:
            # tagged emergency by the LLM but it isn't one (a go-around): the tag would only mislead
            r["tags"] = [t for t in r["tags"] if t != "emergency"] or validate.normalise_tags(None, r.get("kind"), r.get("metric"))
    # ---- automatic set-asides. A set-aside rule is kept (and saved) with
    # its reason; the reviewer can add it back. Nothing is deleted.
    n_aside: dict[str, int] = {}
    fastest = max((float(a["profile"].get("v_speeds", {}).get("vne") or 0) for a in aircraft_profiles()), default=0)

    def set_aside(r: dict, why: str, key: str, keep: bool = True):
        r["include"] = False
        if keep:
            r["auto_aside"] = why             # survives re-checks; becomes the saved reason
        r["issues"].insert(0, "Set aside: " + why)
        n_aside[key] = n_aside.get(key, 0) + 1

    for r in rules:        # same full check the page and Save use (e.g. band needs a tolerance)
        if not r["include"]:
            continue
        issues, fatal = validate.assess(r, phases, page_map, ocr_pages)
        r["issues"] = list(dict.fromkeys(r["issues"] + issues))
        r["blocked"] = fatal
        # complete except for a number the document doesn't give: waits for the instructor
        no_threshold = bool(fatal) and r.get("kind") == "gaze" and r.get("value") is None and "threshold" in fatal
        no_tolerance = bool(fatal) and r.get("kind") == "band" and "tolerance" in fatal
        if fatal and not (no_threshold or no_tolerance):
            continue                      # something else is wrong with it: "needs fixing"
        normal = "normal" in r["lesson_types"]
        msg = None
        if normal:          # (a rule for emergency or instrument lessons isn't meant to fit a normal circuit)
            # phases of the rule that a correct circuit breaks: drop those and keep the ones that fit
            # ("pattern altitude 1,000 ft" during Downwind, Base AND Final -> Downwind)
            bad = phase_conflicts(r, nominal)
            own = [p for p in r.get("phases") or [] if p in FLIGHT_PHASES]
            if bad and len(bad) < len(own):
                r["phases"] = [p for p in r["phases"] if p not in bad]
                gone = ", ".join(p.replace("_", " ").capitalize() for p in bad)
                r["issues"].append(f"Removed {gone} from this rule: {next(iter(bad.values()))}. It now applies during "
                                   + ", ".join(p.replace("_", " ").capitalize() for p in r["phases"]) + " only")
            # a target without a tolerance is judged with a generous one
            msg = circuit_conflict(with_provisional_tolerance(r), nominal)
            if msg and no_tolerance:
                msg += " (Checked with a generous tolerance, because the document gives none.)"
        not_in_quote = next((i[len(validate.NOT_IN_QUOTE):] for i in r["issues"]
                             if i.startswith(validate.NOT_IN_QUOTE)), None)
        wrong_way = next((i[len(validate.WRONG_WAY):] for i in r["issues"]
                          if i.startswith(validate.WRONG_WAY)), None)
        misread = next((i[len(validate.NOT_A_LIMIT):] for i in r["issues"]
                        if i.startswith(validate.NOT_A_LIMIT)), None)
        listed = next((i[len(validate.ERROR_LIST):] for i in r["issues"]
                       if i.startswith(validate.ERROR_LIST)), None)
        if msg:            # a correct circuit breaks it
            set_aside(r, msg, "conflict")
        elif wrong_way or misread:
            set_aside(r, wrong_way or misread, "numbers")
        elif listed:
            set_aside(r, listed, "errors")
        elif not_in_quote:
            set_aside(r, f"the rule uses {not_in_quote}, which is not in the quoted text - the LLM may have "
                         "invented or miscopied it. Check it against the page", "numbers")
        elif handbook is not None and fastest and r.get("kind") == "limit" and r.get("metric") == "ias" and not r.get("ref_speed") \
                and r.get("min_value") is None and (r.get("max_value") or 0) > fastest:
            set_aside(r, f"no aircraft here can fly that fast (the highest never-exceed speed in the aircraft profiles is "
                         f"{fastest:g} kt), so this rule could never be broken", "numbers")
        elif (no_tolerance or no_threshold) and suggest.apply(r):
            # The document gives no number, the FAA does: pre-fill it, say where it is from, and
            # leave the rule set aside until the reviewer accepts it. Then check the completed rule.
            sg = r["suggested"]
            issues, fatal = validate.assess(r, phases, page_map, ocr_pages)
            r["issues"] = [i for i in dict.fromkeys(r["issues"] + issues) if not i.startswith("No tolerance in the SOP")]
            r["blocked"] = fatal
            broken = circuit_conflict(r, nominal) if normal and not fatal else None
            if broken:
                sg["ready"] = False             # not one for "Accept all": a correct circuit breaks it
                set_aside(r, broken, "conflict")
            else:
                set_aside(r, suggest.describe(sg) + ". Check it, then add the rule back", "suggested")
        elif no_tolerance:
            # (not kept as auto_aside: the blocking reason already says exactly this)
            set_aside(r, "the document gives no tolerance for this - set one, then add it back", "tolerance", keep=False)
        elif no_threshold:
            set_aside(r, "the book gives no number for this - set the threshold, then add it back", "open", keep=False)
    # with the FAA's number filled in, a rule can say exactly what a rule from the document
    # already says ("within +10/-5 kt of the approach speed", twice): not one to accept
    in_use = {}
    for o in rules:
        if o["include"] and not o.get("blocked"):
            in_use.setdefault(same_check(o), o)
    for r in rules:
        sg = r.get("suggested")
        twin = in_use.get(same_check(r)) if sg and not r["include"] and sg.get("ready") is not False else None
        if twin:
            sg["ready"] = False
            was = "Set aside: " + (r.get("auto_aside") or "")
            r["auto_aside"] = (f"with the FAA value filled in, this is the same check as '{twin['title']}' "
                               f"(page {twin.get('source_page')}), which is already in use")
            r["issues"] = ["Set aside: " + r["auto_aside"]] + [i for i in r["issues"] if i != was]
            n_aside["suggested"] = n_aside.get("suggested", 1) - 1
            n_aside["duplicate"] = n_aside.get("duplicate", 0) + 1
    return n_aside


def same_check(r: dict) -> tuple:
    """What a rule checks, without its wording: two rules with the same key do the same thing."""
    num = lambda k: None if r.get(k) is None else round(float(r[k]), 3)
    return (r.get("kind"), r.get("metric"), r.get("aoi"), r.get("gaze_measure"), r.get("ref_speed"), num("ref_factor"),
            num("target"), num("tol_hi"), num("tol_lo"), num("min_value"), num("max_value"), num("value"),
            tuple(sorted(r.get("phases") or [])), tuple(sorted(r.get("lesson_types") or [])),
            tuple(sorted((c["metric"], c["op"], c["value"]) for c in r.get("conditions") or [])))


def draft_rule(ch: validate.Checked, raw: dict) -> dict:
    """A validated candidate as it sits in a draft (stored units)."""
    r = dict(ch.rule)
    r.update({"cid": uuid.uuid4().hex[:12], "origin": "llm", "llm_draft": raw,
              "include": ch.reject_reason is None, "blocked": ch.reject_reason,
              "issues": ch.issues, "enabled": True, "note": r.get("note")})
    r.setdefault("conditions", [])
    r.setdefault("phases", [])
    r.setdefault("tags", [])
    return r


class JobManager:
    def __init__(self, drafts: DraftStore, parser: OllamaParser, pdf_dir: Path, is_flying,
                 tesseract_cmd: str | None = None, ocr_lang: str = "eng", chunk_chars: int = 6000,
                 lesson_path: str | None = None, max_pdf_mb: float = MAX_PDF_MB,
                 handbook_num_ctx: int = HANDBOOK_NUM_CTX, handbook_chunk_chars: int = HANDBOOK_CHUNK_CHARS,
                 handbook_overlap_chars: int = HANDBOOK_OVERLAP_CHARS, poh_num_ctx: int = POH_NUM_CTX,
                 coach_model: str | None = None, shares_gpu: bool = True):
        self.drafts = drafts
        self.parser = parser
        self.pdf_dir = Path(pdf_dir)
        self.is_flying = is_flying
        self.tesseract_cmd = tesseract_cmd or None
        self.ocr_lang = ocr_lang
        self.chunk_chars = chunk_chars
        self.lesson_path = lesson_path
        self.max_pdf_mb = float(max_pdf_mb)
        self.handbook_num_ctx = int(handbook_num_ctx)
        self.handbook_chunk_chars = int(handbook_chunk_chars)
        self.handbook_overlap_chars = max(0, int(handbook_overlap_chars))
        self.poh_num_ctx = int(poh_num_ctx)
        self.coach_model = coach_model         # the model MAVIS keeps loaded for coaching
        # True when rules are drafted on the same Ollama (= the same GPU) the coach
        # uses: drafting then pauses during flights and swaps models in and out.
        # False when the SOP LLM runs on a separate device (sop.ollama_url): that
        # GPU is its own, so drafting runs straight through and nothing is unloaded.
        self.shares_gpu = bool(shares_gpu)
        self._task: asyncio.Task | None = None

    def is_coach_model(self, name: str) -> bool:
        return self.coach_model is not None and name in (self.coach_model, f"{self.coach_model}:latest")

    def _chunk_budget(self, system: str, num_ctx: int | None = None, num_predict: int = NUM_PREDICT,
                      cap: int | None = None) -> int:
        """Largest section that still fits the model's context window next to the
        prompt and the answer (~3.5 characters per token, 10% margin)."""
        num_ctx = int(num_ctx or getattr(self.parser, "num_ctx", 8192) or 8192)
        room_tokens = num_ctx - len(system) / 3.5 - num_predict - 200
        return max(1500, min(cap or self.chunk_chars, int(room_tokens * 3.5 * 0.9)))

    def llm_plan(self, d: dict, system: str) -> dict:
        """How a draft's pages are fed to the LLM: context window, answer limit,
        text per call and the overlap between calls."""
        if d.get("doc_type") == "handbook":
            num_ctx = max(self.handbook_num_ctx, int(getattr(self.parser, "num_ctx", 8192) or 8192))
            budget = self._chunk_budget(system, num_ctx, HANDBOOK_NUM_PREDICT, self.handbook_chunk_chars)
            return {"num_ctx": num_ctx, "num_predict": HANDBOOK_NUM_PREDICT, "chunk_chars": budget,
                    "overlap": min(self.handbook_overlap_chars, budget // 4)}
        num_ctx = max(self.poh_num_ctx, int(getattr(self.parser, "num_ctx", 8192) or 8192))
        return {"num_ctx": num_ctx, "num_predict": NUM_PREDICT,
                "chunk_chars": self._chunk_budget(system, num_ctx), "overlap": 0}

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    def nominal_for(self, aircraft_id: str | None) -> dict | None:
        return load_nominal(aircraft_id, self.lesson_path)

    def nominal_for_draft(self, d: dict):
        """What a draft's rules are checked against: the chosen aircraft's
        correct circuit (POH), or every aircraft's (handbook rules apply to all)."""
        if d.get("doc_type") == "handbook":
            return nominals_all() or None
        return self.nominal_for(d.get("aircraft_profile"))

    # ------------------------------------------------------------ entry points
    def check_upload(self, doc_type: str = "poh", handbook: str | None = None) -> None:
        """Refuse an upload before its bytes are read."""
        if doc_type not in DOC_TYPES:
            raise ValueError(f"Unknown document type '{doc_type}'")
        if handbook is not None and handbook not in BOOKS:
            raise ValueError(f"Unknown handbook '{handbook}'")
        if self.busy:
            raise RuntimeError("Another SOP is still being processed - wait for it to finish")

    @property
    def max_pdf_bytes(self) -> int:
        return int(self.max_pdf_mb * 1024 * 1024)

    def too_big(self) -> ValueError:
        return ValueError(f"PDF is larger than {self.max_pdf_mb:g} MB (the limit is sop.max_pdf_mb in config.yaml)")

    async def receive_upload(self, read) -> tuple[str, Path]:
        """Stream an upload to disk: `read(n)` is an async reader (FastAPI's
        UploadFile.read). Nothing but one 1 MB block is ever in memory, so a
        300 MB handbook is fine. -> (sha256, path of the stored PDF)."""
        import hashlib
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.pdf_dir / f"upload-{uuid.uuid4().hex}.part"
        h, size = hashlib.sha256(), 0
        try:
            with tmp.open("wb") as f:
                while True:
                    block = await read(1024 * 1024)
                    if not block:
                        break
                    if size == 0 and not block.startswith(b"%PDF"):
                        raise ValueError("That file is not a PDF")
                    size += len(block)
                    if size > self.max_pdf_bytes:
                        raise self.too_big()
                    h.update(block)
                    f.write(block)
            if size == 0:
                raise ValueError("That file is empty")
            path = self.pdf_dir / f"{h.hexdigest()}.pdf"
            if path.exists():
                tmp.unlink()
            else:
                tmp.replace(path)
            return h.hexdigest(), path
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def start_stored(self, filename: str, sha: str, path: Path, by: str | None,
                     doc_type: str = "poh", handbook: str | None = None) -> dict:
        """Start reading a PDF that receive_upload stored."""
        self.check_upload(doc_type, handbook)
        d = self.drafts.create(filename, sha, str(path), by, doc_type,
                               handbook if doc_type == "handbook" else None)
        self._task = asyncio.create_task(self._read(d["id"], path))
        return d

    def start_upload(self, filename: str, data: bytes, by: str | None,
                     doc_type: str = "poh", handbook: str | None = None) -> dict:
        """Same, from bytes already in memory (small files, tests)."""
        self.check_upload(doc_type, handbook)
        if len(data) > self.max_pdf_bytes:
            raise self.too_big()
        if not data.startswith(b"%PDF"):
            raise ValueError("That file is not a PDF")
        sha = extract.sha256_bytes(data)
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        path = self.pdf_dir / f"{sha}.pdf"
        if not path.exists():
            path.write_bytes(data)
        return self.start_stored(filename, sha, path, by, doc_type, handbook)

    def start_run(self, did: str, pages=None, aircraft: str | None = None,
                  doc_type: str | None = None, handbook: str | None = None, model: str | None = None) -> dict:
        """Run (or re-run) OCR + LLM over the chosen pages. pages: '1-5, 9' or
        a list; None = keep the previous choice (or the suggestion). model: an
        installed Ollama model to draft with instead of the configured one
        ('' = back to the configured one; None = keep)."""
        if self.busy:
            raise RuntimeError("Another SOP is still being processed - wait for it to finish")
        d = self.drafts.get(did)
        if not d:
            raise KeyError(did)
        rows = self.drafts.pages(did)
        if model is not None:
            d["llm_choice"] = model.strip() or None
        if doc_type is not None:
            if doc_type not in DOC_TYPES:
                raise ValueError(f"Unknown document type '{doc_type}'")
            if doc_type != d.get("doc_type", "poh") and rows and doc_type == "handbook":
                rows = []                       # handbook pages are read column by column: read again
            d["doc_type"] = doc_type
        if handbook is not None:
            if handbook not in BOOKS:
                raise ValueError(f"Unknown handbook '{handbook}'")
            d["handbook"] = handbook
        if d.get("doc_type") == "handbook":
            d["handbook"] = d.get("handbook") or d.get("handbook_detected") or "other"
        else:
            d["handbook"] = None
        if aircraft is not None:
            d["aircraft_profile"] = aircraft or None
        if not rows or "needs_ocr" not in rows[0]:
            # reading never finished (or a draft from before page selection): start over from the PDF
            pdf = Path(d.get("pdf_path") or "")
            if not d.get("pdf_path") or not pdf.is_file():
                raise ValueError("These rules were imported from a rules file and there is no PDF to read - upload "
                                 "the PDF to draft rules with the LLM" if d.get("imported_from") else
                                 "The PDF for this draft is no longer on this machine - upload it again")
            d.update(status="processing", stage="queued", detail="", error=None, done=0, total=0,
                     selected_pages=extract.parse_pages(pages, 10 ** 6) if pages else d.get("selected_pages"),
                     run_after_read=True)
            self.drafts.put(d)
            self._task = asyncio.create_task(self._read(did, pdf))
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
    async def _read(self, did: str, pdf):
        """pdf: path of the stored PDF (or its bytes)."""
        loop = asyncio.get_running_loop()
        d = self.drafts.get(did)
        save, progress_only = self._saver(d)
        try:
            progress_only(stage="extracting", detail="Reading pages")

            def prog(i, n):
                progress_only(done=i, total=n, detail=f"Reading page {i} of {n}")
            handbook = d.get("doc_type") == "handbook"
            rows = await loop.run_in_executor(None, lambda: extract.read_pages(pdf, prog, columns=handbook))
            if not rows:
                raise ValueError("The PDF has no pages")
            self.drafts.set_pages(did, rows)
            n_scanned = sum(1 for r in rows if r["needs_ocr"])
            # detect_book only answers when the PDF names itself (metadata, cover,
            # running header) -- never from books it merely cites in its text
            book, not_rules = extract.detect_book(rows)
            d["handbook_detected"] = book
            warns = [w for w in (d.get("warnings") or [])
                     if "look scanned" not in w and "looks like" not in w and "Couldn't tell" not in w]
            ask_book = False
            if handbook:
                suggested = extract.suggest_chapter_pages(rows)
                chosen_book = d.get("handbook")
                if chosen_book:                        # the reviewer's choice stands
                    if book and book != chosen_book:
                        warns.append(f"This PDF's own title says it is the {BOOKS[book][0]}, not the "
                                     f"{BOOKS[chosen_book][0]} - check the handbook chosen below.")
                elif book:
                    d["handbook"] = book
                else:
                    ask_book = True
                    warns.append("Couldn't tell which handbook this is from the PDF (it has no cover page or title) - "
                                 "choose it below.")
                if d.get("handbook") in ("afh", "ifh", "phak") and d.get("name") == Path(d["filename"]).stem:
                    d["name"] = BOOKS[d["handbook"]][1]       # a clearer default rule-set name than the file name
            else:
                suggested = extract.suggest_pages(rows)
                if not d.get("aircraft_profile"):     # best guess from the SOP's own wording (reviewer confirms)
                    head = " ".join(r["text"] for r in rows[:5])[:20000]
                    guess, _ = match_profile({"text": head}, aircraft_profiles())
                    d["aircraft_profile"] = guess
                if book:
                    warns.append(f"This looks like the {BOOKS[book][0]}, a training handbook rather than an aircraft "
                                 "POH - choose 'Training handbook' below so the right prompt is used.")
            if not_rules:
                warns.append("This looks like " + NOT_RULE_BOOKS[not_rules][1])
            if n_scanned and not extract.ocr_available(self.tesseract_cmd, self.ocr_lang):
                warns.append(f"{n_scanned} page(s) look scanned but OCR (Tesseract) isn't available - "
                             "those pages can't be read until it is installed.")
            fields = dict(page_count=len(rows), outline=extract.outline(rows), chapters=extract.chapter_outline(rows),
                          suggested_pages=suggested,
                          scanned_pages=[r["page_no"] for r in rows if r["needs_ocr"]], warnings=warns)
            # a POH upload that is really a handbook (or vice versa) stops at the
            # page picker, where the type can be changed before any LLM time is spent
            mismatch = bool(not_rules) or (not handbook and book is not None) or ask_book
            run_now = d.pop("run_after_read", False) or (len(rows) <= AUTO_ALL_PAGES and not mismatch)
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
            # drop the previous run's LLM warnings; keep what reading the PDF found
            warnings = [w for w in (d.get("warnings") or [])
                        if not w.startswith(("Local LLM", "No aircraft chosen", "Pages ", "Imported from", "No rule was drafted"))
                        and "scan-path rule" not in w and "no tolerance" not in w and "FAA value" not in w]

            need = [r for r in rows if r["page_no"] in chosen and r.get("needs_ocr") and r["source"] != "ocr"]
            if need:
                pdf = Path(d.get("pdf_path") or "")
                if not pdf.is_file():
                    warnings.append("The original PDF is missing, so scanned pages couldn't be OCR'd")
                else:
                    progress_only(stage="ocr", done=0, total=len(need), detail=f"Reading {len(need)} scanned page(s)")

                    def prog(i, n):
                        progress_only(done=i, total=n, detail=f"OCR page {i} of {n}")
                    warnings += await loop.run_in_executor(None, lambda: extract.ocr_selected(
                        pdf, rows, chosen, self.tesseract_cmd, self.ocr_lang, prog))
                    self.drafts.set_pages(did, rows)

            page_map = {r["page_no"]: r["text"] for r in rows}
            ocr_pages = {r["page_no"] for r in rows if r["source"] == "ocr"}
            manual = [r for r in d.get("rules", []) if r.get("origin") == "manual"]   # kept on re-run

            parser = self.parser.for_model(d.get("llm_choice"))
            # drafting with another model than the coach's, on the coach's GPU
            swapped = self.shares_gpu and not self.is_coach_model(parser.model)
            if not await parser.available():
                save(status="ready", stage="done", detail="LLM unavailable - add rules by hand", warnings=warnings + [
                     f"Local LLM '{parser.model}' is not available in Ollama - no rules were drafted. "
                     "Add rules by hand, or start Ollama and press 'Run again'."], rules=manual)
                return

            ac_names = {a["id"]: a["name"] for a in aircraft_profiles()}
            phases = known_phases()
            is_handbook = d.get("doc_type") == "handbook"
            if is_handbook:
                schema = llm_json_schema(phases, gaze=True)
                system = handbook_prompt(phases, d.get("handbook"))
                prompt_version = HANDBOOK_PROMPT_VERSION
            else:
                schema = llm_json_schema(phases)
                system = system_prompt(phases, ac_names.get(d.get("aircraft_profile") or ""))
                prompt_version = PROMPT_VERSION
            pages = [extract.PageText(r["page_no"], r["text"], r["source"]) for r in rows if r["page_no"] in chosen]
            plan = self.llm_plan(d, system)
            chunks = extract.make_chunks(pages, plan["chunk_chars"], overlap=plan["overlap"])
            progress_only(stage="parsing", done=0, total=len(chunks), llm_model=parser.model,
                          prompt_version=prompt_version, llm_seconds=None)
            if swapped:
                # one large model fits on the GPU at a time: the others leave it for this run
                gone = await parser.make_room()
                if gone:
                    log.info("unloaded %s to make room for %s", ", ".join(gone), parser.model)
            if swapped or not self.shares_gpu:
                # the first call may have to load the model (and a separate device's model
                # may be cold): a longer timeout, for this run only
                parser = OllamaParser(parser.url, parser.model, max(parser.timeout, 600.0), parser.num_ctx, parser.think)
            started, gpu_checked = time.monotonic(), False
            candidates: list[dict] = []
            for ch in chunks:
                # pause during a flight only when sharing the coach's GPU
                while self.shares_gpu and self.is_flying():
                    if d["stage"] != "paused":
                        progress_only(stage="paused", detail="Paused while a lesson is being flown")
                        if swapped:                 # give the GPU back to the coach for the lesson
                            await parser.unload(parser.model)
                    await asyncio.sleep(2.0)
                progress_only(stage="parsing", detail=f"Section {ch.index + 1} of {len(chunks)} (page {ch.pages[0]}"
                              + (f"-{ch.pages[-1]})" if len(ch.pages) > 1 else ")"))
                for attempt in (1, 2):
                    try:
                        got = await parser.parse_chunk(ch.text, system, schema, plan["num_ctx"],
                                                            plan["num_predict"])
                        for g in got:
                            if not g.get("page") and ch.pages:
                                g["page"] = ch.pages[0]
                        candidates += got
                        break
                    except Exception as e:
                        log.warning("chunk %d attempt %d failed: %s", ch.index, attempt, e)
                        if attempt == 2:
                            slow = "ReadTimeout" in type(e).__name__ or "timed out" in str(e).lower()
                            warnings.append(f"Pages {ch.pages[0]}-{ch.pages[-1]}: the LLM failed ("
                                            + (f"no answer within {parser.timeout:g} s - the model is too slow on this "
                                               "machine, probably because it doesn't fit on the GPU" if slow else str(e))
                                            + "); check this section by hand")
                progress_only(done=ch.index + 1)
                if not gpu_checked:
                    gpu_checked = True
                    share = await parser.gpu_share()
                    if share is not None and share < 0.98:
                        warnings.append(f"Local LLM '{parser.model}' does not fit on the GPU ({round(share * 100)}% of it is on "
                                        "the GPU, the rest runs on the CPU), so drafting is slow. Close other programs that use "
                                        "the GPU, lower sop.handbook_num_ctx / sop.poh_num_ctx in config.yaml, or use a smaller model.")
            d["llm_seconds"] = round(time.monotonic() - started)
            if swapped:
                await parser.unload(parser.model)       # the coach's model can load again

            progress_only(stage="validating", detail=f"Checking {len(candidates)} candidate rules")
            nominal = self.nominal_for_draft(d)
            checked = [(validate.validate_candidate(c, phases, page_map, ocr_pages), c) for c in candidates]
            # the model handed back one of the prompt's example sentences as its "quote" and the
            # document doesn't contain it: not a rule from this document at all
            n_all = len(checked)
            checked = [(c, raw) for c, raw in checked
                       if not (is_example_quote(raw.get("source_quote")) and (c.rule.get("quote_score") or 0) < validate.QUOTE_MIN)]
            if n_all - len(checked):
                log.info("dropped %d candidate(s) quoting the prompt's examples", n_all - len(checked))
            book = (d.get("handbook") or "other") if is_handbook else None
            em_pages, in_pages = extract.emergency_pages(rows, is_handbook), extract.instrument_pages(rows, is_handbook)
            kept = validate.dedupe([c for c, _ in checked], context=lambda r: tuple(lesson_types_for(
                r, book, r.get("source_page") in em_pages, r.get("source_page") in in_pages)))
            kept_ids = {id(c) for c in kept}
            rules = [draft_rule(c, raw) for c, raw in checked if id(c) in kept_ids]
            n_aside = auto_review(rules, phases=phases, page_map=page_map, ocr_pages=ocr_pages, nominal=nominal,
                                  handbook=book, emergency=em_pages, instrument=in_pages)
            rules.sort(key=lambda r: (r.get("source_page") or 10 ** 6))
            n_ok = sum(1 for r in rules if r["include"])
            if nominal is None and not is_handbook:
                warnings.append("No aircraft chosen, so rules weren't checked against a correctly flown circuit - "
                                "choose the aircraft and press 'Run again' to check them.")
            aside = len(rules) - n_ok
            if n_aside.get("suggested"):
                warnings.append(f"{n_aside['suggested']} rule(s) lacked a tolerance or scan threshold in the document and were given "
                                "the standard FAA value as a suggestion (marked on each rule, with its source). They stay set aside "
                                "until you accept them: check each one and add it back, or use 'Accept all FAA suggestions'.")
            if n_aside.get("duplicate"):
                warnings.append(f"{n_aside['duplicate']} rule(s) without a tolerance turned out to be the same check as a rule "
                                "already in use once the FAA value was filled in, and were set aside as duplicates.")
            if n_aside.get("tolerance"):
                warnings.append(f"{n_aside['tolerance']} rule(s) give a target but the document gives no tolerance, so they were "
                                "set aside - set a tolerance (e.g. '74 kt, +10 / -5') on each one you want to use, then add it back.")
            gaps = coverage_gaps(rules, is_handbook, d.get("handbook")) if rules else []
            if gaps:
                warnings.append("No rule was drafted for: " + "; ".join(gaps) + ". A document of this kind normally gives "
                                + ("these" if len(gaps) > 1 else "this") + " - check that the right pages were read, then add "
                                "the rule by hand or run the LLM again (the local model sometimes misses a passage).")
            if n_aside.get("open"):
                warnings.append(f"{n_aside['open']} scan-path rule(s) have no number in the book and were set aside - "
                                "set a threshold (e.g. 'look at the airspeed at least every 5 s') on each one you want "
                                "to use, then add it back.")
            why = [f"{n} {t}" for n, t in ((n_aside.get("conflict"), "would be broken by a correct circuit"),
                                           (n_aside.get("numbers"), "with numbers that don't match the text"),
                                           (n_aside.get("errors"), "from lists of common errors"),
                                           (n_aside.get("suggested"), "with an FAA suggestion to accept"),
                                           (n_aside.get("duplicate"), "the same as a rule already in use"),
                                           (n_aside.get("tolerance"), "waiting for a tolerance"),
                                           (n_aside.get("open"), "waiting for a threshold")) if n]
            used = [r for r in rules if r["include"]]
            by_type = [f"{sum(1 for r in used if t in r['lesson_types'])} {LESSON_TYPES[t][0].lower()}"
                       for t in LESSON_TYPES if any(t in r["lesson_types"] for r in used)]
            split = f" ({', '.join(by_type)})" if any(r["lesson_types"] != ["normal"] for r in used) else ""
            save(rules=rules + manual, status="ready", stage="done", warnings=warnings,
                 detail=f"{n_ok} rules drafted{split}" + (f", {aside} set aside" if aside else "")
                 + (f" ({'; '.join(why)})" if why else ""))
        except Exception as e:
            self._fail(d, e)
