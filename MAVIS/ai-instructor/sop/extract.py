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


def make_chunks(pages: list[PageText], max_chars: int = 6000) -> list[Chunk]:
    """Pack whole pages into chunks up to max_chars; a page larger than that is
    split on paragraph boundaries. Every piece carries its page marker."""
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
        for piece in _split_long(p.text, max_chars):
            block = f"[[PAGE {p.page_no}]]\n{piece}\n"
            if len(cur_txt) + len(block) > max_chars:
                flush()
            cur_txt += block
            if p.page_no not in cur_pages:
                cur_pages.append(p.page_no)
    flush()
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


def read_pages(pdf_bytes: bytes, progress=None) -> list[dict]:
    """Step 1: text layer only (fast), plus whether each page will need OCR and
    which POH section it is in (carried forward across pages with no header)."""
    import pymupdf
    out: list[dict] = []
    current = None
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        if doc.needs_pass:
            raise ValueError("PDF is password-protected")
        n = doc.page_count
        for i, page in enumerate(doc):
            text = _clean(page.get_text("text", sort=True))
            needs_ocr = len(text) < MIN_TEXT_CHARS or (
                len(text) < THIN_TEXT_CHARS and _image_cover(page) >= IMAGE_COVER_FRAC)
            sec = detect_section(text) if text else None
            if sec is not None:
                current = sec
            out.append({"page_no": i + 1, "text": text, "source": "text" if text else "empty",
                        "ocr_confidence": None, "needs_ocr": bool(needs_ocr), "section": current})
            if progress:
                progress(i + 1, n)
    return out


def ocr_selected(pdf_bytes: bytes, pages: list[dict], selected: set[int], tesseract_cmd: str | None = None,
                 ocr_lang: str = "eng", progress=None) -> list[str]:
    """Step 2: OCR the chosen pages that need it (in place). Returns warnings."""
    import pymupdf
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
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
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
