"""Stage 3 of the backend pipeline: deterministic checks on everything the LLM
drafted. This is where hallucinations are caught -- the LLM's output is a
*proposal*, never trusted as-is.

A candidate is AUTO-REJECTED (kept, shown collapsed in the wizard, can be
restored by the instructor) when:
  * its source_quote can't be found in the extracted SOP text (score < QUOTE_MIN)
  * its kind is missing the fields it needs, or its unit doesn't fit its metric
  * a number is physically implausible for its metric
Otherwise it becomes a DRAFT, possibly with non-fatal `issues` the reviewer sees
(numbers not present in the quote, rule from an OCR page, unknown phases dropped...).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .schema import (CAPTURE_AT, KINDS, METRICS, OPS, SEVERITIES, to_stored, unit_ok)

QUOTE_MIN = 0.85          # below this the quote isn't in the SOP -> auto-reject
QUOTE_EXACT = 0.999

_NUM_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


# ---------------------------------------------------------------- text matching
def norm_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = s.replace("±", " +/- ").replace("−", "-")
    s = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", s)
    s = re.sub(r"[^a-z0-9.+/%-]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _tokens(s: str) -> list[str]:
    return norm_text(s).split()


def quote_score(quote: str, page_text: str) -> float:
    """1.0 = verbatim (after whitespace/case/punctuation normalisation).
    Otherwise the best token-level similarity of any same-length window of the
    page -- tolerant of OCR noise and the LLM dropping a comma, not of an
    invented sentence."""
    q, p = norm_text(quote), norm_text(page_text)
    if not q or not p:
        return 0.0
    if q in p:
        return 1.0
    qt, pt = q.split(), p.split()
    n = len(qt)
    if n == 0 or len(pt) == 0:
        return 0.0
    heads = set(qt[:3]) | set(qt[-3:])
    best = 0.0
    for i, tok in enumerate(pt):
        if tok not in heads:
            continue
        lo = max(0, i - 3)
        window = pt[lo:lo + n + 6]
        sm = SequenceMatcher(None, qt, window, autojunk=False)
        matched = sum(b.size for b in sm.get_matching_blocks())
        best = max(best, matched / n)
        if best >= QUOTE_EXACT:
            break
    return best


def locate_quote(quote: str, pages: dict[int, str], hint_page: int | None) -> tuple[int | None, float]:
    """Search the hinted page (and neighbours) first, then the whole document."""
    order: list[int] = []
    if hint_page in pages:
        order += [hint_page, hint_page - 1, hint_page + 1]
    order += [p for p in pages if p not in order]
    best_page, best = None, 0.0
    for p in order:
        if p not in pages:
            continue
        s = quote_score(quote, pages[p])
        if s > best:
            best_page, best = p, s
            if s >= QUOTE_EXACT:
                break
    return best_page, best


def numbers_in(text: str) -> set[float]:
    return {float(m.replace(",", "")) for m in _NUM_RE.findall(text or "")}


# ---------------------------------------------------------------- rule checks
@dataclass
class Checked:
    rule: dict
    issues: list[str] = field(default_factory=list)
    reject_reason: str | None = None

    @property
    def status(self) -> str:
        return "auto_rejected" if self.reject_reason else "draft"


_NUM_FIELDS = ("target", "tol_hi", "tol_lo", "min_value", "max_value", "value")


def _f(x):
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def normalise_candidate(raw: dict) -> tuple[dict, list[str], str | None]:
    """LLM candidate (numbers as written + unit) -> stored-unit rule dict."""
    issues: list[str] = []
    kind = str(raw.get("kind") or "").strip()
    metric = raw.get("metric") or None
    unit = str(raw.get("unit") or "none")
    if kind not in KINDS:
        return {}, issues, f"unknown rule kind '{kind}'"
    if kind != "unobservable" and metric not in METRICS:
        issues.append("No telemetry metric could be identified - converted to 'not observable'")
        kind, metric = "unobservable", None
    reject = None
    if kind != "unobservable" and not unit_ok(unit, metric):
        reject = f"unit '{unit}' does not fit {METRICS[metric][3].lower()}"

    as_written = {k: raw.get(k) for k in ("target", "tol_plus", "tol_minus", "min", "max", "value")
                  if raw.get(k) is not None}
    as_written["unit"] = unit
    conv = (lambda v: to_stored(_f(v), unit)) if kind != "unobservable" else (lambda v: None)
    conds = []
    for c in raw.get("when") or []:
        cm, cu = c.get("metric"), str(c.get("unit") or "none")
        if cm not in METRICS or c.get("op") not in OPS or _f(c.get("value")) is None:
            issues.append("Dropped an unreadable condition")
            continue
        if not unit_ok(cu, cm):
            issues.append(f"Dropped condition on {cm}: unit '{cu}' does not fit")
            continue
        conds.append({"metric": cm, "op": c["op"], "value": to_stored(_f(c["value"]), cu)})
        as_written.setdefault("when", []).append(c)

    rule = {
        "kind": kind,
        "title": (str(raw.get("title") or "").strip() or "Untitled rule")[:120],
        "phases": [str(p) for p in (raw.get("phases") or [])],
        "metric": metric,
        "target": conv(raw.get("target")),
        "tol_hi": conv(raw.get("tol_plus")),
        "tol_lo": conv(raw.get("tol_minus")),
        "min_value": conv(raw.get("min")),
        "max_value": conv(raw.get("max")),
        "op": raw.get("op") if kind == "event" else None,
        "value": conv(raw.get("value")),
        "gate_phase": raw.get("gate_phase") if kind == "event" else None,
        "capture_at": raw.get("capture_at") if kind == "capture" else None,
        "conditions": conds if kind != "unobservable" else [],
        "severity": raw.get("severity") if raw.get("severity") in SEVERITIES else "caution",
        "source_quote": (str(raw.get("source_quote") or "").strip())[:600] or None,
        "source_page": int(raw["page"]) if str(raw.get("page") or "").isdigit() else None,
        "as_written": as_written,
    }
    # tolerances are magnitudes
    for k in ("tol_hi", "tol_lo"):
        if rule[k] is not None:
            rule[k] = abs(rule[k])
    return rule, issues, reject


def check_rule(rule: dict, phases: list[str]) -> tuple[list[str], str | None]:
    """Structural + plausibility checks on a stored-unit rule (LLM or manual).
    Returns (issues, fatal_reason). Mutates `rule` to drop unknown phases."""
    issues: list[str] = []
    kind, metric = rule.get("kind"), rule.get("metric")
    if kind not in KINDS:
        return issues, f"unknown rule kind '{kind}'"
    bad = [p for p in rule.get("phases") or [] if p not in phases]
    if bad:
        rule["phases"] = [p for p in rule["phases"] if p in phases]
        issues.append(f"Dropped unknown phase(s): {', '.join(bad)}")
    if kind == "unobservable":
        for k in _NUM_FIELDS:
            rule[k] = None
        rule["metric"] = None
        return issues, None
    if metric not in METRICS:
        return issues, f"unknown metric '{metric}'"

    g = {k: _f(rule.get(k)) for k in _NUM_FIELDS}
    if kind == "band":
        if g["target"] is None:
            return issues, "band rule needs a target"
        if g["tol_hi"] is None and g["tol_lo"] is None:
            issues.append("No tolerance in the SOP - set one before approving, or change to a limit")
    elif kind == "limit":
        if g["min_value"] is None and g["max_value"] is None:
            return issues, "limit rule needs a min or a max"
        if g["min_value"] is not None and g["max_value"] is not None and g["min_value"] > g["max_value"]:
            return issues, "min is greater than max"
    elif kind == "event":
        if rule.get("op") not in OPS or g["value"] is None:
            return issues, "event rule needs a comparison and a value"
        if rule.get("gate_phase") and rule["gate_phase"] not in phases:
            return issues, f"unknown gate phase '{rule['gate_phase']}'"
        if not rule.get("gate_phase"):
            issues.append("No deadline phase - rule will be checked continuously")
    elif kind == "capture":
        if rule.get("capture_at") not in CAPTURE_AT:
            return issues, "capture rule needs a moment (liftoff, touchdown, ...)"
        if g["min_value"] is None and g["max_value"] is None:
            return issues, "capture rule needs a min or a max"

    lo, hi = METRICS[metric][2]
    span = hi - lo
    for k in ("target", "min_value", "max_value", "value"):
        v = g[k]
        if v is not None and not (lo - 0.05 * span <= v <= hi + 0.05 * span):
            return issues, f"{k.replace('_value', '')} {v:g} is implausible for {METRICS[metric][3].lower()}"
    for k in ("tol_hi", "tol_lo"):
        if g[k] is not None and g[k] > span / 2:
            return issues, f"tolerance {g[k]:g} is implausibly large"
    for c in rule.get("conditions") or []:
        if c.get("metric") not in METRICS or c.get("op") not in OPS or _f(c.get("value")) is None:
            return issues, "a condition is incomplete"
    return issues, None


def check_provenance(rule: dict, pages: dict[int, str], ocr_pages: set[int]) -> tuple[list[str], str | None]:
    """Quote must exist in the SOP; numbers should appear in the quote."""
    issues: list[str] = []
    quote = rule.get("source_quote") or ""
    if not quote:
        return issues, "no source quote - can't be traced to the SOP"
    page, score = locate_quote(quote, pages, rule.get("source_page"))
    rule["quote_score"] = round(score, 3)
    if score < QUOTE_MIN:
        return issues, f"quote not found in the SOP (best match {score:.0%})"
    if page != rule.get("source_page"):
        issues.append(f"Page corrected from {rule.get('source_page')} to {page}")
        rule["source_page"] = page
    if score < QUOTE_EXACT:
        issues.append(f"Quote matches the SOP text {score:.0%}, not word for word")
    if page in ocr_pages:
        issues.append("From a scanned page (OCR) - compare the numbers with the page image")
    written = rule.get("as_written") or {}
    nums = [_f(v) for k, v in written.items() if k not in ("unit", "when")]
    nums += [_f(c.get("value")) for c in written.get("when", [])]
    in_quote = numbers_in(quote)
    # signs are our convention (descent = negative vs, left bank = negative), not the SOP's
    missing = sorted({abs(n) for n in nums if n is not None and abs(n) not in in_quote})
    if missing:
        issues.append("Number(s) not in the quoted text: " + ", ".join(f"{n:g}" for n in missing))
    return issues, None


def validate_candidate(raw: dict, phases: list[str], pages: dict[int, str],
                       ocr_pages: set[int]) -> Checked:
    rule, issues, reject = normalise_candidate(raw)
    if not rule:
        return Checked({"kind": "unobservable", "title": str(raw.get("title") or "Unreadable"),
                        "source_quote": raw.get("source_quote"), "as_written": raw},
                       issues, reject or "unreadable")
    i2, r2 = check_rule(rule, phases)
    i3, r3 = check_provenance(rule, pages, ocr_pages)
    return Checked(rule, issues + i2 + i3, reject or r3 or r2)


def dedupe(checked: list[Checked]) -> list[Checked]:
    """Chunks can overlap in content (repeated limits tables, summary pages):
    keep the first accepted copy of identical rules; merge phase lists."""
    seen: dict[tuple, Checked] = {}
    out: list[Checked] = []
    for c in checked:
        r = c.rule
        key = (r.get("kind"), r.get("metric"), r.get("op"), r.get("gate_phase"), r.get("capture_at"),
               *(round(r[k], 2) if r.get(k) is not None else None for k in _NUM_FIELDS),
               tuple(sorted((x["metric"], x["op"], round(x["value"], 2)) for x in r.get("conditions") or [])),
               norm_text(r.get("title", "")) if r.get("kind") == "unobservable" else "")
        prev = seen.get(key)
        if prev and not c.reject_reason and not prev.reject_reason:
            merged = sorted(set(prev.rule.get("phases") or []) | set(r.get("phases") or []))
            if prev.rule.get("phases") and r.get("phases"):
                prev.rule["phases"] = merged
            elif not r.get("phases"):
                prev.rule["phases"] = []
            continue
        if not c.reject_reason:
            seen[key] = c
        out.append(c)
    return out


def aircraft_checks(rules: list[dict], profile: dict) -> list[str]:
    """Cross-check approved rules against the aircraft profile being bound.
    Warnings only -- the instructor decides."""
    warns: list[str] = []
    vs = profile.get("v_speeds") or {}
    vne = _f(vs.get("vne"))
    vs0 = _f(vs.get("vs0"))
    detents = (profile.get("flaps") or {}).get("detents_deg") or []
    max_flap = max(detents) if detents else None
    max_alt = _f((profile.get("limits") or {}).get("max_alt_ft"))
    for r in rules:
        nums = [_f(r.get(k)) for k in ("target", "min_value", "max_value", "value")]
        nums = [n for n in nums if n is not None]
        t = r.get("title", "rule")
        if r.get("metric") == "ias" and vne and any(n > vne for n in nums):
            warns.append(f"'{t}': airspeed above this aircraft's Vne ({vne:g} kt)")
        if r.get("metric") == "ias" and vs0 and r.get("kind") in ("band", "limit") and \
                any(0 < n < vs0 for n in nums) and "TAKEOFF_ROLL" not in (r.get("phases") or []):
            warns.append(f"'{t}': airspeed below this aircraft's stall speed Vs0 ({vs0:g} kt)")
        if r.get("metric") == "flaps_deg" and max_flap is not None and any(n > max_flap for n in nums):
            warns.append(f"'{t}': flap setting beyond this aircraft's max detent ({max_flap:g}°)")
        if r.get("metric") == "flaps_deg" and detents and r.get("kind") == "event" and \
                r.get("value") is not None and _f(r["value"]) not in [float(d) for d in detents]:
            warns.append(f"'{t}': flap value {r['value']:g}° is not a detent on this aircraft ({detents})")
        if r.get("metric") == "alt_msl" and max_alt and any(n > max_alt for n in nums):
            warns.append(f"'{t}': altitude above this aircraft's ceiling ({max_alt:g} ft)")
    return warns
