"""Import a rule file that the SOP pages exported ("Export > JSON", format
`mavis-sop-rules/1`) as a DRAFT -- no PDF and no LLM run needed.

Used to move rules between machines (dev PC -> simulator laptop), to restore a
backup, or to load a file that was corrected by hand. The file is the same one
the Export button writes, from a saved rule set or from a draft.

The import never writes to Postgres. It creates an ordinary draft on the
/sop/new page: every rule is checked again (same checks as an LLM draft),
rules that were set aside stay set aside with their reason, and nothing is
used in a lesson until the reviewer presses Save.

When the PDF the rules came from is known on this machine (same file in
another draft, or saved before) the draft is linked to it, so quotes are
verified and the page viewer works. Otherwise the rules keep their page number
and quote as plain text.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from . import extract, validate
from .schema import BOOKS, GAZE_MEASURES, KINDS, REF_SPEEDS, aircraft_profiles, clean_lesson_types

FORMAT = "mavis-sop-rules/1"
MAX_IMPORT_MB = 20
MAX_RULES = 2000

_STR = ("metric", "op", "gate_phase", "capture_at", "aoi", "gaze_measure", "note", "source_quote")
_NUM = ("target", "tol_hi", "tol_lo", "min_value", "max_value", "value")


def looks_like_json(first_bytes: bytes) -> bool:
    return first_bytes.lstrip(b"\xef\xbb\xbf \t\r\n")[:1] in (b"{", b"[")


def read_export(raw: bytes) -> dict:
    """Bytes of an exported file -> {"rule_set": {...}, "rules": [...]}.
    Raises ValueError with a message the page can show."""
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"That file is not valid JSON ({e})")
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise ValueError("That JSON file is not a rules export (it has no \"rules\" list). "
                         "Use a file made with Export > JSON on the SOP pages.")
    fmt = data.get("format")
    if fmt and fmt != FORMAT:
        raise ValueError(f"This rules file has format '{fmt}'; this version of MAVIS reads '{FORMAT}'")
    if len(data["rules"]) > MAX_RULES:
        raise ValueError(f"This file has {len(data['rules'])} rules; the limit is {MAX_RULES}")
    if not isinstance(data.get("rule_set"), dict):
        data["rule_set"] = {}
    return data


def rule_from_export(x: dict) -> tuple[dict | None, list[str]]:
    """One exported rule -> a draft rule (stored units, same field names).
    -> (rule, notes). rule None = not a rule at all (skipped)."""
    notes: list[str] = []
    if not isinstance(x, dict) or not str(x.get("kind") or "").strip():
        return None, notes
    kind = str(x["kind"]).strip()
    r: dict = {"cid": uuid.uuid4().hex[:12], "kind": kind,
               "title": (str(x.get("title") or "").strip() or "Untitled rule")[:120],
               "origin": "llm" if x.get("origin") == "llm" else "manual",
               "llm_draft": None, "imported": True}
    for k in _STR:
        v = x.get(k)
        r[k] = str(v).strip() or None if isinstance(v, str) else None
    for k in _NUM:
        r[k] = validate._f(x.get(k))
    r["phases"] = [str(p) for p in x.get("phases") or [] if isinstance(p, str)] if isinstance(x.get("phases"), list) else []
    r["tags"] = [str(t) for t in x.get("tags") or [] if isinstance(t, str)] if isinstance(x.get("tags"), list) else []
    conds = x.get("conditions") if isinstance(x.get("conditions"), list) else []
    r["conditions"] = [{"metric": c.get("metric"), "op": c.get("op"), "value": validate._f(c.get("value"))}
                       for c in conds if isinstance(c, dict)]
    r["severity"] = x.get("severity") if isinstance(x.get("severity"), str) else "caution"
    # files exported before lesson types existed: an "emergency" tag meant an emergency procedure
    r["lesson_types"] = clean_lesson_types(x.get("lesson_types")) if isinstance(x.get("lesson_types"), list) \
        else (["emergency"] if "emergency" in r["tags"] else ["normal"])
    r["ref_speed"] = x.get("ref_speed") if x.get("ref_speed") in REF_SPEEDS else None
    r["ref_factor"] = (validate._f(x.get("ref_factor")) or 1.0) if r["ref_speed"] else None
    sp = x.get("source_page")
    r["source_page"] = int(sp) if isinstance(sp, (int, float)) and not isinstance(sp, bool) and sp >= 1 else None
    if r["source_quote"]:
        r["source_quote"] = r["source_quote"][:validate.QUOTE_MAX]
    status = str(x.get("status") or "").strip().lower()
    aside = bool(x.get("set_aside")) or status == "set aside"
    # a share written as a percentage by hand ("90" for 90 %): stored shares are 0..1. Not for a
    # set-aside rule: there the odd value may be exactly why it was set aside, so it stays as it is.
    if kind == "gaze" and not aside and r["value"] is not None and r["gaze_measure"] in GAZE_MEASURES \
            and GAZE_MEASURES[r["gaze_measure"]][1] == "fraction" and 1 < r["value"] <= 100:
        notes.append(f"Read {r['value']:g} as {r['value']:g}% of the time")
        r["value"] = r["value"] / 100.0
    r["include"] = not aside
    r["enabled"] = not (x.get("enabled") is False or status == "switched off")
    reason = str(x.get("set_aside_reason") or "").strip()
    if aside and reason and not reason.startswith(validate.BY_REVIEWER):
        r["auto_aside"] = reason[:500]          # kept as the reason unless something blocks the rule now
    if kind not in KINDS:
        notes.append(f"Unknown rule kind '{kind}'")
    return r, notes


def draft_fields(data: dict, json_name: str) -> tuple[dict, list[str]]:
    """Header of the export -> fields of the new draft, plus warnings."""
    h = data["rule_set"]
    warns: list[str] = []
    general = h.get("scope") == "general" or bool(h.get("handbook"))
    out: dict = {"doc_type": "handbook" if general else "poh", "handbook": None, "aircraft_profile": None}
    if general:
        out["handbook"] = h.get("handbook") if h.get("handbook") in BOOKS else "other"
    else:
        known = {a["id"] for a in aircraft_profiles()}
        ac = h.get("aircraft_profile")
        if ac in known:
            out["aircraft_profile"] = ac
        elif ac:
            warns.append(f"The file is for aircraft profile '{ac}', which this MAVIS doesn't have - choose the aircraft.")
    stem = Path(json_name).stem
    out["name"] = (str(h.get("name") or "").strip() or stem)[:120]
    out["filename"] = str(h.get("source_pdf") or "").strip() or json_name
    out["llm_model"] = h.get("llm_model") if isinstance(h.get("llm_model"), str) else None
    out["prompt_version"] = h.get("prompt_version") if isinstance(h.get("prompt_version"), str) else None
    sel = None
    if isinstance(h.get("pages_read"), str) and h["pages_read"].strip():
        import re
        if all(int(n) <= 5000 for n in re.findall(r"\d+", h["pages_read"])):   # no absurd ranges from a hand-edited file
            try:
                sel = extract.parse_pages(h["pages_read"], 5000) or None
            except ValueError:
                sel = None
    out["selected_pages"] = sel
    sha = h.get("source_sha256")
    out["source_sha256"] = sha if isinstance(sha, str) and len(sha) == 64 else None
    out["source_pdf"] = str(h.get("source_pdf") or "").strip() or None
    out["exported_at"] = data.get("exported_at") if isinstance(data.get("exported_at"), str) else None
    return out, warns


def quotes_found(rules: list[dict], pages: dict[int, str]) -> float:
    """Share of the quoted rules whose quote is in these pages (is this the right PDF?)."""
    quoted = [r for r in rules if r.get("source_quote")]
    if not quoted:
        return 1.0
    hit = sum(1 for r in quoted
              if validate.locate_quote(r["source_quote"], pages, r.get("source_page"))[1] >= validate.QUOTE_MIN)
    return hit / len(quoted)
