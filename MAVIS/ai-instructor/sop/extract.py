"""Stage 1 of the backend pipeline: PDF -> per-page text (with OCR fallback)
-> LLM-sized chunks.

Scanned and mixed PDFs are handled page by page: a page whose text layer is
empty, or thin while mostly covered by images (a scanned checklist pasted
into a Word export), is rendered and run through local Tesseract. Nothing
here touches the network.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

MIN_TEXT_CHARS = 60          # below this a page counts as having no text layer
THIN_TEXT_CHARS = 400        # below this, OCR if images cover most of the page
IMAGE_COVER_FRAC = 0.4
OCR_DPI = 300


@dataclass
class PageText:
    page_no: int             # 1-based
    text: str
    source: str              # text | ocr | empty
    ocr_confidence: float | None = None


@dataclass
class Chunk:
    index: int
    pages: list[int]
    text: str                # with "[[PAGE n]]" markers so the LLM can cite pages


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def open_pdf(pdf):
    """pdf: the file's bytes, or its path (preferred for big files: nothing is
    copied into memory)."""
    import pymupdf
    if isinstance(pdf, (bytes, bytearray, memoryview)):
        return pymupdf.open(stream=bytes(pdf), filetype="pdf")
    return pymupdf.open(str(pdf))


def _clean(text: str) -> str:
    text = text.replace("­", "")                        # soft hyphen
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)              # de-hyphenate line breaks
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _image_cover(page) -> float:
    area = page.rect.width * page.rect.height or 1.0
    covered = 0.0
    try:
        for info in page.get_image_info():
            x0, y0, x1, y1 = info["bbox"]
            covered += max(0.0, x1 - x0) * max(0.0, y1 - y0)
    except Exception:
        return 0.0
    return min(1.0, covered / area)


def _ocr_page(page, tesseract_cmd: str | None, lang: str) -> tuple[str, float | None]:
    import pytesseract
    from PIL import Image

    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    pix = page.get_pixmap(dpi=OCR_DPI)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    data = pytesseract.image_to_data(img, lang=lang, output_type=pytesseract.Output.DICT)
    confs = [float(c) for c, w in zip(data["conf"], data["text"]) if w.strip() and float(c) >= 0]
    text = pytesseract.image_to_string(img, lang=lang)
    return text, (sum(confs) / len(confs) if confs else None)


def ocr_check(tesseract_cmd: str | None = None, lang: str = "eng") -> tuple[bool, str]:
    """(usable, human-readable reason). Shown on /sop/status so a red OCR chip
    says exactly what's wrong instead of just 'false'."""
    import os
    import shutil
    try:
        import pytesseract
    except ImportError:
        return False, ("Python package 'pytesseract' is not installed in MAVIS's venv - run "
                       ".venv\\Scripts\\pip install pytesseract pillow")
    cmd = (tesseract_cmd or "").strip().strip('"').strip("'")
    if cmd:
        if not os.path.isfile(cmd):
            hint = ""
            if "\t" in cmd or "\n" in cmd:
                hint = (" (the path contains a TAB/newline character: in config.yaml use forward slashes, "
                        "e.g. \"C:/Program Files/Tesseract-OCR/tesseract.exe\")")
            return False, f"sop.tesseract_cmd points to a file that doesn't exist: {cmd!r}{hint}"
        pytesseract.pytesseract.tesseract_cmd = cmd
    else:
        found = shutil.which("tesseract")
        default = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        if not found and os.path.isfile(default):
            pytesseract.pytesseract.tesseract_cmd = default      # installed, just not on PATH
            cmd = default
        elif not found:
            return False, ("tesseract.exe not found: it isn't on PATH for the process running MAVIS "
                           "(restart MAVIS from a NEW terminal after installing) and sop.tesseract_cmd "
                           "in config.yaml is empty - set it to the full path of tesseract.exe")
    try:
        ver = pytesseract.get_tesseract_version()
    except Exception as e:
        return False, f"tesseract could not be run ({type(e).__name__}: {e})"
    try:
        langs = pytesseract.get_languages(config="")
    except Exception:
        langs = []
    missing = [x for x in lang.split("+") if langs and x not in langs]
    if missing:
        return False, (f"Tesseract {ver} works, but language data for {missing} is missing "
                       f"(installed: {', '.join(langs) or 'none'}) - check the tessdata folder / TESSDATA_PREFIX")
    return True, f"Tesseract {ver} ({cmd or 'on PATH'})"


def ocr_available(tesseract_cmd: str | None = None, lang: str = "eng") -> bool:
    return ocr_check(tesseract_cmd, lang)[0]


def extract_pages(pdf_bytes: bytes, tesseract_cmd: str | None = None, ocr_lang: str = "eng",
                  progress=None) -> tuple[list[PageText], list[str]]:
    """Returns (pages, warnings). CPU-bound: call from an executor."""
    import pymupdf

    warnings: list[str] = []
    can_ocr = ocr_available(tesseract_cmd)
    out: list[PageText] = []
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        if doc.needs_pass:
            raise ValueError("PDF is password-protected")
        n = doc.page_count
        for i, page in enumerate(doc):
            text = _clean(page.get_text("text", sort=True))
            needs_ocr = len(text) < MIN_TEXT_CHARS or (
                len(text) < THIN_TEXT_CHARS and _image_cover(page) >= IMAGE_COVER_FRAC)
            if needs_ocr and can_ocr:
                try:
                    ocr_text, conf = _ocr_page(page, tesseract_cmd, ocr_lang)
                    ocr_text = _clean(ocr_text)
                    if len(ocr_text) > len(text):
                        out.append(PageText(i + 1, ocr_text, "ocr", conf))
                        if conf is not None and conf < 60:
                            warnings.append(f"Page {i + 1}: low OCR confidence ({conf:.0f}%) - check its rules carefully")
                    else:
                        out.append(PageText(i + 1, text, "text" if text else "empty"))
                except Exception as e:
                    log.warning("OCR failed on page %d: %s", i + 1, e)
                    warnings.append(f"Page {i + 1}: OCR failed ({e})")
                    out.append(PageText(i + 1, text, "text" if text else "empty"))
            else:
                if needs_ocr and not can_ocr and len(text) < MIN_TEXT_CHARS:
                    warnings.append(f"Page {i + 1} looks scanned but Tesseract is not installed - page skipped")
                out.append(PageText(i + 1, text, "text" if text else "empty"))
            if progress:
                progress(i + 1, n)
    return out, warnings


def _split_long(text: str, max_chars: int) -> list[str]:
    parts, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        if len(cur) + len(para) + 2 > max_chars and cur:
            parts.append(cur)
            cur = ""
        while len(para) > max_chars:                 # pathological: one giant paragraph
            parts.append(para[:max_chars])
            para = para[max_chars:]
        cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        parts.append(cur)
    return parts


CONTEXT_NOTE = "(end of the previous section, repeated for context only)"


def _tail(text: str, max_chars: int) -> str:
    """The last whole paragraphs (or, failing that, sentences) of text, up to max_chars."""
    body = re.sub(r"\[\[PAGE \d+\]\]\n?", "", text).strip()
    if len(body) <= max_chars:
        return body
    cut = body[-max_chars:]
    for rx in (r"\n\s*\n", r"(?<=[.!?])\s+"):
        m = re.search(rx, cut)
        if m and len(cut) - m.end() >= max_chars * 0.4:
            return cut[m.end():].strip()
    return cut.strip()


def make_chunks(pages: list[PageText], max_chars: int = 6000, overlap: int = 0) -> list[Chunk]:
    """Pack whole pages into chunks up to max_chars; a page larger than that is
    split on paragraph boundaries. Every piece carries its page marker.

    overlap > 0 (training handbooks): each chunk after the first starts with the
    last ~overlap characters of the one before, under its page marker and
    CONTEXT_NOTE, so a paragraph that runs across a chunk boundary is seen whole
    and a requirement keeps the sentences that explain it. The overlap comes
    out of max_chars."""
    body_max = max(1000, max_chars - overlap) if overlap else max_chars
    chunks: list[Chunk] = []
    cur_txt, cur_pages = "", []

    def flush():
        nonlocal cur_txt, cur_pages
        if cur_txt.strip():
            chunks.append(Chunk(len(chunks), cur_pages, cur_txt.strip()))
        cur_txt, cur_pages = "", []

    for p in pages:
        if not p.text.strip():
            continue
        for piece in _split_long(p.text, body_max):
            block = f"[[PAGE {p.page_no}]]\n{piece}\n"
            if len(cur_txt) + len(block) > body_max:
                flush()
            cur_txt += block
            if p.page_no not in cur_pages:
                cur_pages.append(p.page_no)
    flush()
    if overlap:
        tails = [(c.pages[-1], _tail(c.text, overlap)) for c in chunks]
        for i in range(1, len(chunks)):
            page, tail = tails[i - 1]
            if tail:
                chunks[i].text = f"[[PAGE {page}]]\n{CONTEXT_NOTE}\n{tail}\n\n{chunks[i].text}"
    return chunks


# ---------------------------------------------------------------- two-step flow
# Step 1 (upload): text layer of every page + section detection, no OCR.
# Step 2 (after the reviewer picks pages): OCR only the chosen scanned pages.

# Standard GAMA POH layout -- used to name a section when only its number is found.
GAMA_SECTIONS = {1: "General", 2: "Limitations", 3: "Emergency Procedures", 4: "Normal Procedures",
                 5: "Performance", 6: "Weight & Balance / Equipment List", 7: "Airplane & Systems Description",
                 8: "Handling, Service & Maintenance", 9: "Supplements", 10: "Safety Information"}
_SECTION_TITLES = [(re.compile(rx, re.I), n) for rx, n in (
    (r"\bLIMITATIONS\b", 2), (r"\bEMERGENCY\s+PROCEDURES\b", 3), (r"\bNORMAL\s+PROCEDURES\b", 4),
    (r"\bPERFORMANCE\b", 5), (r"\bWEIGHT\s*(?:&|AND)\s*BALANCE\b", 6), (r"\bSYSTEMS?\s+DESCRIPTIONS?\b", 7),
    (r"\bHANDLING,?\s+SERVICE\b", 8), (r"\bSUPPLEMENTS?\b", 9), (r"\bSAFETY\s+INFORMATION\b", 10))]
_SECTION_RX = re.compile(r"\bSECTION\s+(\d{1,2})\b", re.I)
_PAGE_LABEL_RX = re.compile(r"(?<![\w-])(\d{1,2})-(\d{1,3})(?![\w-])")
SUGGEST_SECTIONS = {2, 4}          # Limitations + Normal Procedures


def detect_section(text: str) -> int | None:
    """POH section number of a page, from its running header/footer:
    'SECTION 2 / LIMITATIONS', or a page label like '2-4' in the footer."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    edge = lines[:8] + lines[-4:]
    joined = "\n".join(edge)
    m = _SECTION_RX.search(joined)
    if m and 1 <= int(m.group(1)) <= 10:
        return int(m.group(1))
    for ln in lines[-4:]:
        m = _PAGE_LABEL_RX.search(ln)
        if m and 1 <= int(m.group(1)) <= 10:
            return int(m.group(1))
    for rx, n in _SECTION_TITLES:
        if any(rx.search(ln) and len(ln) < 60 for ln in lines[:6]):
            return n
    return None


def read_pages(pdf, progress=None, columns: bool = False) -> list[dict]:
    """Step 1: text layer only (fast), plus whether each page will need OCR,
    which POH section and which handbook chapter it is in (both carried forward
    across pages with no header). columns=True (training handbooks) reads
    two-column pages column by column instead of line by line across the page."""
    out: list[dict] = []
    current = None
    chapter, chapter_title = None, None
    with open_pdf(pdf) as doc:
        if doc.needs_pass:
            raise ValueError("PDF is password-protected")
        n = doc.page_count
        doc_title = " ".join(str((doc.metadata or {}).get(k) or "") for k in ("title", "subject")).strip()
        for i, page in enumerate(doc):
            sorted_text = _clean(page.get_text("text", sort=True))
            text = _clean(column_text(page)) if columns else sorted_text
            needs_ocr = len(text) < MIN_TEXT_CHARS or (
                len(text) < THIN_TEXT_CHARS and _image_cover(page) >= IMAGE_COVER_FRAC)
            sec = detect_section(sorted_text) if sorted_text else None
            if sec is not None:
                current = sec
            ch, title = detect_chapter(sorted_text) if sorted_text else (None, None)
            if ch is not None:
                if ch != chapter:
                    chapter_title = None
                chapter = ch
                chapter_title = title or chapter_title
            out.append({"page_no": i + 1, "text": text, "source": "text" if text else "empty",
                        "ocr_confidence": None, "needs_ocr": bool(needs_ocr), "section": current,
                        "chapter": chapter, "chapter_title": chapter_title if ch is not None else None})
            if progress:
                progress(i + 1, n)
    if out and doc_title:
        out[0]["doc_title"] = doc_title[:300]       # PDF metadata: the surest sign of which book this is
    return out


# ---------------------------------------------------------------- training handbooks
# FAA handbooks: two text columns, page labels "8-3" (chapter 8, page 3), a
# chapter's first page headed "Chapter 8 Airport Traffic Patterns".
_CHAPTER_LABEL_RX = re.compile(r"(?<![\w-])(\d{1,2}|[A-Z])-(\d{1,3})(?![\w-])")
_CHAPTER_HEAD_RX = re.compile(r"^chapter\s+(\d{1,2})\b\s*[:.\-–—]?\s*(.*)$", re.I)
_LETTER_PARTS = {"G": "Glossary", "I": "Index", "A": "Appendix A", "B": "Appendix B", "C": "Appendix C"}
# chapters worth reading for circuit + scan rules (matched against the chapter title)
SUGGEST_CHAPTERS = (r"basic flight maneuvers", r"takeoffs? and departure", r"traffic patterns?",
                    r"approaches and landings", r"attitude instrument flying", r"airport operations",
                    r"aeromedical")


def column_text(page) -> str:
    """Reading order for one- or two-column pages: between full-width blocks
    (headings, wide figures, footers) read the left column top to bottom, then
    the right column. Single-column pages come out as with sort=True."""
    try:
        blocks = [b for b in page.get_text("blocks", sort=True) if b[6] == 0 and str(b[4]).strip()]
    except Exception:
        return page.get_text("text", sort=True)
    if not blocks:
        return ""
    mid = page.rect.x0 + page.rect.width / 2
    margin = page.rect.width * 0.03
    left = [b for b in blocks if b[2] <= mid + margin]
    right = [b for b in blocks if b[0] >= mid - margin]
    if len(left) < 2 or len(right) < 2:
        return "\n".join(str(b[4]).strip() for b in blocks)
    out: list[str] = []
    band_l: list = []
    band_r: list = []

    def flush():
        out.extend(str(b[4]).strip() for b in sorted(band_l, key=lambda b: b[1]))
        out.extend(str(b[4]).strip() for b in sorted(band_r, key=lambda b: b[1]))
        band_l.clear()
        band_r.clear()

    for b in sorted(blocks, key=lambda b: (b[1], b[0])):
        if b[2] <= mid + margin:
            band_l.append(b)
        elif b[0] >= mid - margin:
            band_r.append(b)
        else:                                      # spans both columns
            flush()
            out.append(str(b[4]).strip())
    flush()
    return "\n".join(out)


def detect_chapter(text: str) -> tuple[str | None, str | None]:
    """(chapter key, title) of a handbook page: a 'Chapter 8 ...' heading near the
    top (title from the same or the next line), else the page label '8-3' /
    'G-2' in the footer or header. Table-of-contents pages (several chapter
    headings) give neither."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    heads = [i for i, ln in enumerate(lines) if _CHAPTER_HEAD_RX.match(ln)]
    if len(heads) == 1 and heads[0] < 8:
        i = heads[0]
        m = _CHAPTER_HEAD_RX.match(lines[i])
        title = re.sub(r"[.\s]*\d+-\d+\s*$", "", m.group(2)).strip(" .:-–—")
        if len(title) < 4 and i + 1 < len(lines):
            title = lines[i + 1].strip(" .:-–—")
        return m.group(1), (title[:80] or None)
    if len(heads) > 1:
        return None, None
    for ln in lines[-4:][::-1] + lines[:3]:
        m = _CHAPTER_LABEL_RX.search(ln)
        if m and (m.group(1).isdigit() or m.group(1) in _LETTER_PARTS):
            return m.group(1), None
    return None, None


def chapter_outline(pages: list[dict]) -> list[dict]:
    """Consecutive pages of the same chapter -> [{chapter, title, first, last,
    count, scanned, suggested}] for the handbook page picker."""
    out: list[dict] = []
    for p in pages:
        ch = p.get("chapter")
        if out and out[-1]["chapter"] == ch and out[-1]["last"] == p["page_no"] - 1:
            o = out[-1]
            o["last"] = p["page_no"]
            o["count"] += 1
            o["scanned"] += int(bool(p.get("needs_ocr")))
            o["title"] = o["title"] or p.get("chapter_title")
            continue
        out.append({"chapter": ch, "title": p.get("chapter_title"), "first": p["page_no"], "last": p["page_no"],
                    "count": 1, "scanned": int(bool(p.get("needs_ocr")))})
    for o in out:
        ch = o["chapter"]
        if not o["title"]:
            o["title"] = (_LETTER_PARTS.get(ch) if ch and not ch.isdigit() else
                          f"Chapter {ch}" if ch else "Front matter / unlabelled")
        o["suggested"] = bool(ch and any(re.search(rx, o["title"], re.I) for rx in SUGGEST_CHAPTERS))
    return out


def emergency_pages(pages: list[dict], handbook: bool) -> set[int]:
    """Pages of the emergency / abnormal procedures part of a document: POH
    Section 3, or a handbook chapter whose title says so. Rules from there get
    the lesson type "emergency": they are used in emergency lessons only."""
    if not handbook:
        return {p["page_no"] for p in pages if p.get("section") == 3}
    out: set[int] = set()
    for o in chapter_outline(pages):
        if re.search(r"emergenc|abnormal", o["title"] or "", re.I):
            out.update(range(o["first"], o["last"] + 1))
    return out


def instrument_pages(pages: list[dict], handbook: bool) -> set[int]:
    """Pages of a handbook chapter about flying by reference to the instruments
    ("Basic Instrument Maneuvers", "Attitude Instrument Flying"). Rules from
    there are for instrument-flying lessons, not for a visual circuit."""
    out: set[int] = set()
    if handbook:
        for o in chapter_outline(pages):
            if re.search(r"\binstrument\b", o["title"] or "", re.I):
                out.update(range(o["first"], o["last"] + 1))
    return out


def suggest_chapter_pages(pages: list[dict]) -> list[int]:
    picks: list[int] = []
    for o in chapter_outline(pages):
        if o["suggested"]:
            picks += list(range(o["first"], o["last"] + 1))
    return picks or [p["page_no"] for p in pages]


COVER_MAX_WORDS = 150       # a cover / title page has few words; a page of body text does not
HEADER_MAX_CHARS = 70       # a running header / footer line is short
FOOTER_MIN_SHARE = 0.4      # ...and is on at least this share of the pages


def detect_book(pages: list[dict]) -> tuple[str | None, str | None]:
    """Which FAA handbook this PDF is -> (book id from schema.BOOKS or None,
    id from schema.NOT_RULE_BOOKS if it's a book with no circuit rules, or None).

    Only the book's OWN name counts, never a mention in the running text:
    handbooks cite each other constantly (the Instrument Flying Handbook refers
    to the Pilot's Handbook of Aeronautical Knowledge on many pages). So, in
    order:
      1. the PDF's metadata title;
      2. a cover / title page: one of the first three pages, if it has few words;
      3. a running header or footer: the SAME short line, mostly made up of the
         book's name, on at least 40% of the pages.
    Unsure -> (None, None): the reviewer's own choice stands, or they are asked."""
    from .schema import BOOKS, NOT_RULE_BOOKS
    cands = [(bid, rx) for bid, (_, _, rx) in BOOKS.items() if rx] + \
            [(bid, rx) for bid, (rx, _) in NOT_RULE_BOOKS.items()]

    def answer(bid):
        return (bid, None) if bid in BOOKS else (None, bid)

    def first_named(text: str) -> str | None:
        """The book named EARLIEST in text (document number or title)."""
        text = text.lower()
        best = None
        for bid, rx in cands:
            for r in rx:
                m = re.search(r, text)
                if m and (best is None or m.start() < best[0]):
                    best = (m.start(), bid)
        return best[1] if best else None

    def name_share(line: str, bid: str) -> float:
        """How much of a line the book's number + title take up."""
        rx = dict(cands)[bid]
        hit = sum(m.end() - m.start() for r in rx for m in [re.search(r, line.lower())] if m)
        return hit / max(1, len(line))

    if not pages:
        return None, None
    hit = first_named(pages[0].get("doc_title") or "")
    if hit:
        return answer(hit)
    for p in pages[:3]:
        if len(p["text"].split()) <= COVER_MAX_WORDS:
            hit = first_named(p["text"])
            if hit:
                return answer(hit)
    # running header / footer: the same short line, page after page (digits
    # ignored, so "6-12" page labels don't matter). A citation at the bottom of
    # a page is a different sentence every time, so it never adds up.
    seen: dict[tuple[str, str], int] = {}
    for p in pages[:600]:
        lines = [ln.strip() for ln in p["text"].splitlines() if ln.strip()]
        on_page = set()
        for ln in lines[:2] + lines[-3:]:
            if len(ln) > HEADER_MAX_CHARS:
                continue
            bid = first_named(ln)
            if bid and name_share(ln, bid) >= 0.4:      # the line IS the name, not a sentence that mentions it
                on_page.add((bid, re.sub(r"[\d\W_]+", " ", ln.lower()).strip()))
        for key in on_page:
            seen[key] = seen.get(key, 0) + 1
    if seen:
        per_book: dict[str, int] = {}
        for (bid, _), n in seen.items():
            per_book[bid] = max(per_book.get(bid, 0), n)
        ranked = sorted(per_book.items(), key=lambda kv: -kv[1])
        top, n = ranked[0]
        runner = ranked[1][1] if len(ranked) > 1 else 0
        # a real running header/footer is on (nearly) every page, or every other
        # page in a book with different left and right pages
        if n >= max(3, FOOTER_MIN_SHARE * min(len(pages), 600)) and n >= 2 * runner:
            return answer(top)
    return None, None


def ocr_selected(pdf, pages: list[dict], selected: set[int], tesseract_cmd: str | None = None,
                 ocr_lang: str = "eng", progress=None) -> list[str]:
    """Step 2: OCR the chosen pages that need it (in place). Returns warnings.
    pdf: bytes or a path."""
    todo = [p for p in pages if p["page_no"] in selected and p.get("needs_ocr") and p["source"] != "ocr"]
    if not todo:
        return []
    warnings: list[str] = []
    if not ocr_available(tesseract_cmd, ocr_lang):
        skipped = [p["page_no"] for p in todo if len(p["text"]) < MIN_TEXT_CHARS]
        if skipped:
            warnings.append(f"{len(skipped)} chosen page(s) look scanned but OCR (Tesseract) isn't available - "
                            f"they were skipped: {compress_pages(skipped)}")
        return warnings
    with open_pdf(pdf) as doc:
        for k, p in enumerate(todo):
            try:
                txt, conf = _ocr_page(doc[p["page_no"] - 1], tesseract_cmd, ocr_lang)
                txt = _clean(txt)
                if len(txt) > len(p["text"]):
                    p.update(text=txt, source="ocr", ocr_confidence=conf)
                    if conf is not None and conf < 60:
                        warnings.append(f"Page {p['page_no']}: low OCR confidence ({conf:.0f}%) - check its rules carefully")
            except Exception as e:
                log.warning("OCR failed on page %d: %s", p["page_no"], e)
                warnings.append(f"Page {p['page_no']}: OCR failed ({e})")
            if progress:
                progress(k + 1, len(todo))
    return warnings


def outline(pages: list[dict]) -> list[dict]:
    """Consecutive pages of the same section -> [{section, title, first, last, count, suggested}]."""
    out: list[dict] = []
    for p in pages:
        sec = p.get("section")
        if out and out[-1]["section"] == sec and out[-1]["last"] == p["page_no"] - 1:
            out[-1]["last"] = p["page_no"]
            out[-1]["count"] += 1
            out[-1]["scanned"] += int(bool(p.get("needs_ocr")))
            continue
        out.append({"section": sec, "title": GAMA_SECTIONS.get(sec, "Front matter / unlabelled") if sec
                    else "Front matter / unlabelled", "first": p["page_no"], "last": p["page_no"],
                    "count": 1, "scanned": int(bool(p.get("needs_ocr")))})
    for o in out:
        o["suggested"] = o["section"] in SUGGEST_SECTIONS
    return out


def suggest_pages(pages: list[dict]) -> list[int]:
    picks = [p["page_no"] for p in pages if p.get("section") in SUGGEST_SECTIONS]
    return picks or [p["page_no"] for p in pages]


def parse_pages(spec, n: int) -> list[int]:
    """'1-5, 9, 12-14' or [1,2,3] -> sorted unique page numbers within 1..n."""
    if isinstance(spec, (list, tuple, set)):
        nums = {int(x) for x in spec}
    else:
        nums = set()
        for part in str(spec or "").replace(";", ",").split(","):
            part = part.strip()
            if not part:
                continue
            m = re.fullmatch(r"(\d+)\s*[-–]\s*(\d+)", part)
            if m:
                a, b = sorted((int(m.group(1)), int(m.group(2))))
                nums.update(range(a, b + 1))
            elif part.isdigit():
                nums.add(int(part))
            else:
                raise ValueError(f"Can't read page range '{part}' - use e.g. 1-5, 9, 12-14")
    return sorted(x for x in nums if 1 <= x <= n)


def compress_pages(nums) -> str:
    nums = sorted(set(nums))
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return ", ".join(out)
