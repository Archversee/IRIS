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
