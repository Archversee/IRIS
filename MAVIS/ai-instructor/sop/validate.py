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
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from functools import lru_cache

from .schema import (AOI_ROLES, CAPTURE_AT, FOREIGN_UNITS, GAZE_MEASURES, GAZE_RANGE, KINDS, METRIC_TAG,
                     METRICS, OPS, REF_FACTOR_RANGE, REF_OFFSET_MAX, REF_SPEEDS, SEVERITIES, TAGS,
                     clean_lesson_types, gaze_unit_ok, to_stored, unit_ok)

QUOTE_MIN = 0.85          # below this the quote isn't in the SOP -> auto-reject
QUOTE_MAX = 1000          # characters kept of a quote (handbook rules quote a whole passage)
LLM_NOTE = "LLM summary of the passage: "   # prefix of the note the handbook prompt writes
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


@lru_cache(maxsize=2048)
def _page(text: str) -> tuple[str, frozenset]:
    """A page ready for matching: its normalised text and the set of its words.
    Kept between calls -- one review re-checks every rule against the same pages,
    and normalising a 400-page handbook once per rule is what made that slow."""
    p = norm_text(text)
    return p, frozenset(sys.intern(t) for t in p.split())


def _pair(a: tuple[str, frozenset], b: tuple[str, frozenset]) -> tuple[str, frozenset]:
    """Two consecutive pages as one text (a passage can run over a page break)."""
    return a[0] + " " + b[0], a[1] | b[1]


def _could_reach(cq: Counter, n: int, words: frozenset) -> float:
    """The highest score this quote could get on a page, from the page's words alone:
    a quote word that isn't on the page can't be matched. Costs microseconds, so
    a page that can't hold the quote is skipped without comparing word by word."""
    return sum(c for t, c in cq.items() if t in words) / n if n else 0.0


def _score(q: str, qt: list[str], cq: Counter, page: tuple[str, frozenset], floor: float = 0.0) -> float:
    """quote_score on prepared texts. floor: a result at or below it is of no
    use to the caller, so a page that can't beat it is not examined (-> 0.0)."""
    p, words = page
    if not q or not p:
        return 0.0
    if q in p:
        return 1.0
    n = len(qt)
    if n == 0 or _could_reach(cq, n, words) <= floor:
        return 0.0
    pt = p.split()
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


def _prep(quote: str) -> tuple[str, list[str], Counter]:
    q = norm_text(quote)
    qt = q.split()
    return q, qt, Counter(qt)


def quote_score(quote: str, page_text: str) -> float:
    """1.0 = verbatim (after whitespace/case/punctuation normalisation).
    Otherwise the best token-level similarity of any same-length window of the
    page -- tolerant of OCR noise and the LLM dropping a comma, not of an
    invented sentence."""
    return _score(*_prep(quote), _page(page_text))


# "NORMAL TAKEOFF ... 4. Elevator Control -- LIFT NOSE WHEEL (at 55 KIAS)." -- a model that shortens
# its quote joins the pieces with an ellipsis. Not dot leaders: "Normal Climb Out ........ 75-85 KIAS".
_ELLIPSIS = re.compile(r"(?<![.\s])\s*(?:(?<!\.)\.\.\.(?!\.)|…)\s*|\s+(?:(?<!\.)\.\.\.(?!\.)|…)\s*")
_SHORT_PART = 6           # words; a shorter piece is usually a heading


def quote_parts(quote: str) -> list[str]:
    """The pieces of a quote that was shortened with '...' (one piece = not shortened)."""
    if "..." not in (quote or "") and "…" not in (quote or ""):
        return [quote or ""]
    parts = [p.strip() for p in _ELLIPSIS.split(quote) if norm_text(p)]
    return parts or [quote]


def parts_score(parts: list[str], text: str) -> float:
    """How well a shortened quote is in the text: every piece has to be there
    (at least QUOTE_MIN each). Only a short piece -- a heading: 'NORMAL TAKEOFF',
    OCR'd as 'NORMAL TAK EOFF' -- may match just half, and such pieces may make up
    no more than 30 % of the quote. The score is the word-weighted average, so an
    invented piece can't hide behind real ones."""
    return _parts_score([_prep(part) for part in parts], _page(text))


def _parts_score(preps: list[tuple[str, list[str], Counter]], page: tuple[str, frozenset]) -> float:
    total = got = soft = 0.0
    for q, qt, cq in preps:
        n = len(qt)
        sc = _score(q, qt, cq, page)
        if sc < QUOTE_MIN:
            if n >= _SHORT_PART or sc < 0.5:
                return min(sc, QUOTE_MIN - 0.01)
            soft += n
        total += n
        got += n * sc
    if not total or soft / total > 0.3:
        return min(got / total if total else 0.0, QUOTE_MIN - 0.01)
    return max(got / total, QUOTE_MIN)        # every piece is there


def _parts_possible(preps: list[tuple[str, list[str], Counter]], words: frozenset) -> bool:
    """Could every piece be on a page with these words? (cheap; see _could_reach)"""
    return all(_could_reach(cq, len(qt), words) >= (QUOTE_MIN if len(qt) >= _SHORT_PART else 0.5)
               for _q, qt, cq in preps)


def _near(pages: dict[int, str], hint_page: int | None) -> list[int]:
    """The page the rule names and its neighbours (the LLM is often one page off)."""
    return [p for p in (hint_page, (hint_page or 0) - 1, (hint_page or 0) + 1) if p in pages] if hint_page in pages else []


def locate_quote(quote: str, pages: dict[int, str], hint_page: int | None) -> tuple[int | None, float]:
    """Search the hinted page (and neighbours) first, then the whole document.
    A passage can run over a page break, so if no single page holds it, each
    pair of consecutive pages is tried too (the rule is filed under the first).
    A quote shortened with '...' is found when each of its pieces is.

    Pages near the hint are compared word by word, so a quote that isn't found
    still gets a meaningful "best match". The rest of the document is only
    searched for a page that could actually hold the quote (see _could_reach):
    comparing every rule with every page of a 400-page handbook took 40 s."""
    parts = quote_parts(quote)
    if len(parts) <= 1:
        return _locate_whole(quote, pages, hint_page)
    near = _near(pages, hint_page)
    # the "..." may be the book's own: the quote as it stands, around the hinted page
    whole = _locate_whole(quote, {p: pages[p] for p in near}, hint_page) if near else (None, 0.0)
    if whole[1] >= QUOTE_MIN:
        return whole
    preps = [_prep(part) for part in parts]
    order = near + [p for p in pages if p not in near]
    best_page, best = whole
    for pair in (False, True):                  # one page first; then a passage over a page break
        for p in order:
            if pair and p + 1 not in pages:
                continue
            page = _pair(_page(pages[p]), _page(pages[p + 1])) if pair else _page(pages[p])
            if p not in near and not _parts_possible(preps, page[1]):
                continue
            sc = _parts_score(preps, page)
            if sc > best:
                best_page, best = p, sc
            if best >= QUOTE_MIN:
                return best_page, best
    return best_page, best


def _locate_whole(quote: str, pages: dict[int, str], hint_page: int | None) -> tuple[int | None, float]:
    q, qt, cq = _prep(quote)
    near = _near(pages, hint_page)
    order = near + [p for p in pages if p not in near]
    best_page, best = None, 0.0
    likeliest, reach = None, 0.0            # the far page with most of the quote's words, if none can hold it
    for p in order:
        page = _page(pages[p])
        if p in near:
            s = _score(q, qt, cq, page)
        else:                               # far from the hint: only a page that could hold the quote
            r = _could_reach(cq, len(qt), page[1])
            if r > reach:
                likeliest, reach = p, r
            s = _score(q, qt, cq, page, max(best, QUOTE_MIN - 1e-9))
        if s > best:
            best_page, best = p, s
            if s >= QUOTE_EXACT:
                return best_page, best
    if best < QUOTE_EXACT and len(qt) >= 12:
        for p in order:
            if p + 1 in pages:
                page = _pair(_page(pages[p]), _page(pages[p + 1]))
                # clearly better than any single page; far from the hint, only if it could hold the quote
                s = _score(q, qt, cq, page, best + 0.02 if p in near else max(best + 0.02, QUOTE_MIN - 1e-9))
                if s > best + 0.02:
                    best_page, best = p, s
                    if s >= QUOTE_EXACT:
                        break
    if best_page is None and likeliest is not None:
        # nothing near the hint (or no hint) and no page can hold it: say how close the likeliest page is
        best_page, best = likeliest, _score(q, qt, cq, _page(pages[likeliest]))
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
    raw = {**raw, **{k: _unescape(raw.get(k)) for k in ("title", "source_quote", "note") if isinstance(raw.get(k), str)}}
    kind = str(raw.get("kind") or "").strip()
    metric = raw.get("metric") or None
    unit = str(raw.get("unit") or "none")
    if kind not in KINDS:
        return {}, issues, f"unknown rule kind '{kind}'"
    if kind == "gaze":
        return _normalise_gaze(raw, unit)
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
        cv, cop = to_stored(_f(c["value"]), cu), c["op"]
        if cm == "flaps_deg" and cop == ">=" and cv == 0:
            # "flaps 0 to 10 degrees: 110 KIAS" written as "while flaps >= 0": always true, so the flap
            # speed would apply with the flaps UP as well. A flap condition means flaps extended.
            cop = ">"
            issues.append("Changed the condition 'flaps at least 0°' (always true) to 'flaps above 0°': "
                          "a rule for a flap setting applies with the flaps extended")
        conds.append({"metric": cm, "op": cop, "value": cv})
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
        "source_quote": (str(raw.get("source_quote") or "").strip())[:QUOTE_MAX] or None,
        "source_page": int(raw["page"]) if str(raw.get("page") or "").isdigit() else None,
        "as_written": as_written,
        "tags": normalise_tags(raw.get("tags"), kind, metric),
        "note": llm_note(raw),
        "ref_speed": None, "ref_factor": None,
    }
    # a speed that depends on the aircraft ("1.3 VSO", "the recommended approach speed"):
    # the numbers are then differences from  factor x that speed
    if raw.get("ref_speed") in REF_SPEEDS and metric == "ias" and kind in ("band", "limit", "capture", "event"):
        rule["ref_speed"] = raw["ref_speed"]
        rule["ref_factor"] = _f(raw.get("ref_factor")) or 1.0
        if rule["ref_factor"] != 1.0:
            as_written["ref_factor"] = rule["ref_factor"]
        if kind == "band" and rule["target"] is None:
            rule["target"] = 0.0
    elif kind == "band" and metric == "ias" and rule["target"] is None and rule["min_value"] is None \
            and rule["max_value"] is None:
        # a speed the LLM couldn't put a number on, because the text names an aircraft speed instead
        named = infer_ref(rule["source_quote"] or "")
        if named:
            rule.update(ref_speed=named[0], ref_factor=named[1], target=0.0)
            issues.append(f"The LLM left the speed empty; the text names {REF_SPEEDS[named[0]][1]}"
                          f"{'' if named[1] == 1 else f' x {named[1]:g}'}, so the rule is measured from that speed of each aircraft")
            tol = _TOL_IN_TEXT.search(rule["source_quote"] or "")
            if tol and rule["tol_hi"] is None and rule["tol_lo"] is None:
                rule.update(tol_hi=float(tol.group(1)), tol_lo=float(tol.group(2)))
                issues.append(f"Tolerance +{tol.group(1)} / -{tol.group(2)} kt read from the quoted text - check it")
    # tolerances are magnitudes
    for k in ("tol_hi", "tol_lo"):
        if rule[k] is not None:
            rule[k] = abs(rule[k])
    if kind == "limit" and rule["min_value"] is None and rule["max_value"] is None:
        # "Wing Flaps -- 20°", "Climb Speed -- 60 KIAS": the model gave one number in
        # `value` (or `target`) and no range. Keep the number where the reviewer
        # can see it, as a target, and let them say what it is.
        one = rule["value"] if rule["value"] is not None else rule["target"]
        if one is not None:
            rule.update(kind="band", target=one, value=None)
            reject = reject or ("the LLM gave a single value without saying whether it is a target, a minimum or a "
                                "maximum - set a tolerance, or change it to a limit")
    elif kind == "limit" and rule["min_value"] is not None and rule["min_value"] == rule["max_value"]:
        # "approach at 70 KIAS" written as min 70 / max 70: nobody can hold a
        # speed exactly, so it is a target that still needs a tolerance
        rule.update(kind="band", target=rule["min_value"], min_value=None, max_value=None)
        issues.append("The LLM gave the same number as minimum and maximum - made it a target; set the tolerance")
    elif kind == "band" and rule["target"] is None and (rule["min_value"] is not None or rule["max_value"] is not None):
        # "generally 500-1000 fpm" called a band: a range is a limit
        rule.update(kind="limit")
        issues.append("The LLM called this a target but gave a range - made it a limit")
    if rule["kind"] == "band" and rule["tol_hi"] is None and rule["tol_lo"] is None and rule["target"] is not None \
            and not rule["ref_speed"]:
        quote, t = rule["source_quote"] or "", rule["target"]
        said, span = stated_direction(quote, t), range_in_quote(quote, t)
        if said:
            # "Throttle -- 1000 RPM or LESS" drafted as a target of 1000: it is a maximum
            rule.update(kind="limit", target=None, **{"max_value" if said[0] == "max" else "min_value": t})
            issues.append(f"The text says '{said[1]}': made it a {'maximum' if said[0] == 'max' else 'minimum'}, not a target")
        elif span:
            # "Approach at 65 to 75 KIAS" drafted as a target of 75: the text gives the range
            lo, hi = (-span[1], -span[0]) if t < 0 else span
            rule.update(kind="limit", target=None, min_value=lo, max_value=hi)
            issues.append(f"The text gives a range ({span[0]:g} to {span[1]:g}): made it a limit instead of a target of {t:g}")
            as_written.update(min=lo, max=hi)
    if rule["ref_speed"] == "vs0" and (infer_ref(rule["source_quote"] or "") or ("", 0))[0] == "approach":
        # "use the manufacturer's recommended airspeed, or 1.3 VSO if there is none": every aircraft
        # here has a recommended approach speed in its profile, so that is the one to hold
        issues.append(f"The text prefers the manufacturer's recommended speed and gives {rule['ref_factor']:g} x VSO only as a "
                      "fallback: the rule is measured from each aircraft's approach speed")
        rule.update(ref_speed="approach", ref_factor=1.0)
        as_written.pop("ref_factor", None)
    tidy_kind(rule)
    # numbers the LLM wrote into fields this kind of rule doesn't use (a stray "value": 10 on
    # a limit) are gone now; don't hold them against the quote either
    left = {abs(rule[k]) for k in _NUM_FIELDS if rule.get(k) is not None}
    for k in ("target", "tol_plus", "tol_minus", "min", "max", "value"):
        v = conv(raw.get(k)) if raw.get(k) is not None else None
        if k in as_written and (v is None or abs(v) not in left):
            as_written.pop(k)
    return rule, issues, reject


_KIND_USES = {"band": ("target", "tol_hi", "tol_lo"), "limit": ("min_value", "max_value"),
              "event": ("value",), "capture": ("min_value", "max_value"), "gaze": ("value",), "unobservable": ()}


def tidy_kind(rule: dict) -> dict:
    """Empty the fields a rule's kind doesn't use. The LLM must write every
    field, so it often fills one that means nothing for the kind."""
    kind = rule.get("kind")
    if kind not in _KIND_USES:
        return rule
    for k in _NUM_FIELDS:
        if k not in _KIND_USES[kind]:
            rule[k] = None
    if kind != "event":
        rule["op"] = rule["gate_phase"] = None
    if kind != "capture":
        rule["capture_at"] = None
    return rule


_UESC = re.compile(r"\\u([0-9a-fA-F]{4})")


def _unescape(s: str | None) -> str | None:
    """'\\u2022 Failure to ...' (a bullet the model wrote as an escape code) -> the character."""
    return _UESC.sub(lambda m: chr(int(m.group(1), 16)), s) if s else s


def llm_note(raw: dict) -> str | None:
    """The handbook prompt's "note": what the surrounding text says that helps
    the reviewer set or check the rule. Unverified LLM text, so it is labelled."""
    t = re.sub(r"\s+", " ", str(raw.get("note") or "")).strip()
    return (LLM_NOTE + t)[:500] if t else None


def _normalise_gaze(raw: dict, unit: str) -> tuple[dict, list[str], str | None]:
    """A gaze candidate: aoi + gaze_measure + value (seconds or a 0..1 share).
    value may be null -- the book described the behaviour without a number,
    and the instructor sets the threshold before saving."""
    issues: list[str] = []
    aoi, measure = raw.get("aoi") or None, raw.get("gaze_measure") or None
    reject = None
    if aoi not in AOI_ROLES:
        reject = f"unknown area of interest '{aoi}'"
    elif measure not in GAZE_MEASURES:
        reject = f"unknown gaze check '{measure}'"
    value = _f(raw.get("value"))
    as_written: dict = {"unit": unit}
    if value is not None:
        as_written["value"] = raw.get("value")
        dim = GAZE_MEASURES[measure][1] if measure in GAZE_MEASURES else None
        if dim == "fraction":
            # a share: models write "50" for 50 % whatever unit they name; only
            # 0..1 with unit "fraction" is taken as a fraction
            if unit == "s":
                reject = reject or f"unit 's' does not fit '{GAZE_MEASURES[measure][0].lower()}'"
            elif unit == "percent" or value > 1:
                value = value / 100.0
        elif dim == "time":
            if unit in ("percent", "fraction"):
                reject = reject or f"unit '{unit}' does not fit '{GAZE_MEASURES[measure][0].lower()}'"
        else:
            value = to_stored(value, unit)
    rule = {
        "kind": "gaze",
        "title": (str(raw.get("title") or "").strip() or "Untitled rule")[:120],
        "phases": [str(p) for p in (raw.get("phases") or [])],
        "metric": None, "target": None, "tol_hi": None, "tol_lo": None, "min_value": None, "max_value": None,
        "op": None, "value": value, "gate_phase": None, "capture_at": None, "conditions": [],
        "aoi": aoi, "gaze_measure": measure,
        "severity": raw.get("severity") if raw.get("severity") in SEVERITIES else "advisory",
        "source_quote": (str(raw.get("source_quote") or "").strip())[:QUOTE_MAX] or None,
        "source_page": int(raw["page"]) if str(raw.get("page") or "").isdigit() else None,
        "as_written": as_written,
        "tags": normalise_tags(raw.get("tags"), "gaze", None),
        "note": llm_note(raw),
        "ref_speed": None, "ref_factor": None,
    }
    return rule, issues, reject


_SHARE_TO_TIME = {"min_share": "max_gap", "max_share": "max_dwell"}
_PERCENT = re.compile(r"\d\s*(?:percent|per\s+cent|%)", re.I)


def share_without_number(rule: dict) -> list[str]:
    """A scan rule written as a share of time that has no percentage (the book
    gave none, or the LLM's number wasn't in the text and was removed).
    "Refer to the attitude indicator" / "only quick glances": a share nobody can
    state is the wrong check; the time-based one says the same thing in a way an
    instructor can put a number on. Returns the notes for the reviewer."""
    measure = rule.get("gaze_measure")
    if rule.get("kind") != "gaze" or measure not in _SHARE_TO_TIME or rule.get("value") is not None \
            or rule.get("aoi") not in AOI_ROLES:
        return []
    if _PERCENT.search(rule.get("source_quote") or ""):
        # the passage does give a percentage: leave the share for the reviewer to fill in
        return ["The quoted text gives a percentage but the rule has no threshold - check which area the "
                "percentage is about, then set it"]
    was = GAZE_MEASURES[measure][0].lower()
    rule["gaze_measure"] = _SHARE_TO_TIME[measure]
    return [f"The book gives no percentage, so '{was}' was changed to "
            f"'{GAZE_MEASURES[rule['gaze_measure']][0].lower()} N seconds' - change it back if a share fits better"]


_PCT_NUM = re.compile(r"(\d+(?:\.\d+)?)\s*(?:to|-|\u2013|\u2014)?\s*(\d+(?:\.\d+)?)?\s*(?:percent|per\s+cent|%)", re.I)
_SEC_NUM = re.compile(r"(\d+(?:\.\d+)?)\s*(?:to|-|\u2013|\u2014)?\s*(\d+(?:\.\d+)?)?\s*(?:seconds?|secs?\b)", re.I)


def gaze_number_stated(rule: dict) -> bool:
    """Is a scan rule's threshold in the quote AS a percentage / a number of
    seconds? "Height equal to 1/10 the span" does not make a 10 % rule."""
    v, measure = _f(rule.get("value")), rule.get("gaze_measure")
    if v is None or measure not in GAZE_MEASURES:
        return True
    share = GAZE_MEASURES[measure][1] == "fraction"
    want = round(v * 100, 4) if share else v
    for m in (_PCT_NUM if share else _SEC_NUM).finditer(rule.get("source_quote") or ""):
        if any(g is not None and abs(float(g) - want) < 1e-6 for g in m.groups()):
            return True
    return False

# What a handbook calls the aircraft's own speeds, for a rule the LLM left without
# its speed ("within +10/-5 knots of the recommended landing speed" -> target null).
_REF_WORDING = [
    (re.compile(r"recommended\s+(?:landing|approach)\s+speed|manufacturer.s\s+recommended\s+(?:approach\s+)?(?:air)?speed|\bV\s?REF\b", re.I), "approach", 1.0),
    (re.compile(r"\b1\.3\s*(?:x|×|times)?\s*V\s?S\s?[O0]\b", re.I), "vs0", 1.3),
    (re.compile(r"best\s+rate[\s-]+of[\s-]+climb\s+(?:air)?speed|\bV\s?Y\b", re.I), "vy", 1.0),
    (re.compile(r"best\s+angle[\s-]+of[\s-]+climb\s+(?:air)?speed|\bV\s?X\b", re.I), "vx", 1.0),
]


_TOL_IN_TEXT = re.compile(r"\+\s*(\d{1,2})\s*/\s*[-\u2212\u2013]\s*(\d{1,2})\s*(?:KIAS|knots?|kts?)\b", re.I)


def infer_ref(quote: str) -> tuple[str, float] | None:
    """The aircraft speed a quote names, when it names exactly one way to get it
    (the first wording that matches; 'recommended landing speed' wins over the
    '1.3 VSO' fallback the same sentence often adds)."""
    for rx, key, factor in _REF_WORDING:
        if rx.search(quote or ""):
            return key, factor
    return None


def normalise_tags(tags, kind: str | None, metric: str | None) -> list[str]:
    """Known tags only, no duplicates, and always the tag the metric implies
    (so 'Pilot controls' / 'Flight telemetry' / 'Scan path' filters are reliable)."""
    out = [t for t in dict.fromkeys(tags or []) if t in TAGS]
    implied = "scan" if kind == "gaze" else METRIC_TAG.get(metric or "")
    if implied and implied not in out:
        out.append(implied)
    if not out:
        out = ["procedure"] if kind == "unobservable" else ["flight_telemetry"]
    return out[:4]


_FOREIGN = [(re.compile(r"\s*(?:" + rx + r")", re.I), dim) for rx, dim in FOREIGN_UNITS.items()]
_NUM_WITH_POS = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)")


def foreign_quantity(rule: dict) -> str | None:
    """If the rule's numbers appear in its quote only as a quantity the sim can't
    measure ('20 PSI', '53 gallons', '2550 lbs'), return that quantity -- the LLM
    has put it on the wrong metric (e.g. oil pressure -> engine RPM)."""
    if rule.get("kind") in (None, "unobservable", "gaze") or not rule.get("source_quote"):
        return None
    written = rule.get("as_written") or {}
    nums = {_f(v) for k, v in written.items() if k not in ("unit", "when") and _f(v) is not None}
    if not nums:
        return None
    quote = rule["source_quote"]
    for n in nums:
        uses = [m for m in _NUM_WITH_POS.finditer(quote) if float(m.group(1).replace(",", "")) == abs(n)]
        if not uses:
            continue
        dims = []
        for m in uses:
            tail = quote[m.end():m.end() + 16]
            dims.append(next((d for rx, d in _FOREIGN if rx.match(tail)), None))
        if all(dims):
            unit = quote[uses[0].end():uses[0].end() + 16].strip().split()[0].rstrip(".,;:)")
            return f"{n:g} {unit} ({dims[0]})"
    return None


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
    if kind != "gaze":
        rule["aoi"] = rule["gaze_measure"] = None
    rule["lesson_types"] = clean_lesson_types(rule.get("lesson_types"))
    ref = rule.get("ref_speed") or None
    if ref and (ref not in REF_SPEEDS or metric != "ias" or kind in ("gaze", "unobservable")):
        issues.append("Removed the aircraft-speed reference: it only works for an airspeed rule")
        ref = None
    rule["ref_speed"] = ref
    rule["ref_factor"] = (_f(rule.get("ref_factor")) or 1.0) if ref else None
    if ref and kind == "band" and _f(rule.get("target")) is None:
        rule["target"] = 0.0           # "at the approach speed": no difference from it
    if kind == "unobservable":
        for k in _NUM_FIELDS:
            rule[k] = None
        rule["metric"] = None
        return issues, None
    if kind == "gaze":
        return _check_gaze(rule, issues)
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
    if ref:
        # the numbers are differences from the aircraft's speed, not speeds
        f_lo, f_hi = REF_FACTOR_RANGE
        if not f_lo <= rule["ref_factor"] <= f_hi:
            return issues, f"{rule['ref_factor']:g} x {REF_SPEEDS[ref][1]} is implausible"
        for k in ("target", "min_value", "max_value", "value"):
            if g[k] is not None and abs(g[k]) > REF_OFFSET_MAX:
                return issues, (f"{g[k]:g} kt is implausibly far from {REF_SPEEDS[ref][1]} - with an aircraft speed chosen, "
                                "the numbers are differences from it (0 = exactly that speed)")
    for k in ("target", "min_value", "max_value", "value"):
        v = g[k]
        if not ref and v is not None and not (lo - 0.05 * span <= v <= hi + 0.05 * span):
            return issues, f"{k.replace('_value', '')} {v:g} is implausible for {METRICS[metric][3].lower()}"
    for k in ("tol_hi", "tol_lo"):
        if g[k] is not None and g[k] > span / 2:
            return issues, f"tolerance {g[k]:g} is implausibly large"
    for c in rule.get("conditions") or []:
        if c.get("metric") not in METRICS or c.get("op") not in OPS or _f(c.get("value")) is None:
            return issues, "a condition is incomplete"
    return issues, None


def _check_gaze(rule: dict, issues: list[str]) -> tuple[list[str], str | None]:
    for k in ("target", "tol_hi", "tol_lo", "min_value", "max_value"):
        rule[k] = None
    rule.update(metric=None, op=None, gate_phase=None, capture_at=None, conditions=[])
    if rule.get("aoi") not in AOI_ROLES:
        return issues, "choose the area of the cockpit (look at…)"
    if rule.get("gaze_measure") not in GAZE_MEASURES:
        return issues, "choose what is checked (how often / how long / share of time)"
    v = _f(rule.get("value"))
    if v is None:          # allowed in a draft; assess() blocks saving until it's set
        return issues, None
    lo, hi = GAZE_RANGE[GAZE_MEASURES[rule["gaze_measure"]][1]]
    if not lo <= v <= hi:
        what = f"{v:g} s" if GAZE_MEASURES[rule["gaze_measure"]][1] == "time" else f"{v * 100:g}%"
        return issues, f"{what} is implausible for '{GAZE_MEASURES[rule['gaze_measure']][0].lower()}'"
    return issues, None


def scattered_match(quote: str, page_text: str) -> bool:
    """True if the quote's words AND all its numbers sit close together on the
    page, in any order. PDF tables come out of text extraction with their cells
    reordered, and a model quoting a table row tidies it up ("VFE ... 10° to 30°
    Flaps 85 KIAS"), so an in-order match fails although the row is there. Only
    for quotes that carry numbers: prose has to match in order."""
    qt, pt = norm_text(quote).split(), norm_text(page_text).split()
    nums = {t for t in qt if any(ch.isdigit() for ch in t)}
    if len(qt) < 4 or not nums or not pt:
        return False
    need = Counter(qt)
    width = min(len(pt), 2 * len(qt) + 8)
    for start in range(0, max(1, len(pt) - width + 1), max(1, len(qt) // 2)):
        win = Counter(pt[start:start + width])
        if all(win[n] for n in nums) and sum(min(c, win[t]) for t, c in need.items()) / len(qt) >= 0.8:
            return True
    return False


NOT_IN_QUOTE = "Number(s) not in the quoted text: "
WRONG_WAY = "Check the direction: "

_NUM_UNIT = r"\s*(?:°|deg(?:rees?)?|kias|kcas|knots?|kts?|rpm|feet|ft|fpm|ft/min|percent|%)?\s*"
_AFTER = [(re.compile(_NUM_UNIT + r"(?:or\s+(?:less|below|lower|slower)|max(?:imum)?\b)", re.I), "max"),
          (re.compile(_NUM_UNIT + r"(?:or\s+(?:more|greater|above|higher|faster)|minimum\b)", re.I), "min")]
_BEFORE = [(re.compile(r"(?:not\s+(?:to\s+)?exceed(?:ing)?|no(?:t)?\s+more\s+than|no(?:t)?\s+greater\s+than|"
                       r"maximum\s+of|prohibited\s+above|in\s+excess\s+of)\s*$", re.I), "max"),
           (re.compile(r"(?:at\s+least|no(?:t)?\s+less\s+than|no(?:t)?\s+lower\s+than|minimum\s+of|"
                       r"prohibited\s+below)\s*$", re.I), "min")]


def stated_direction(quote: str, number: float) -> tuple[str, str] | None:
    """What the quoted text itself says a number is: ("max", "1000 RPM or LESS")
    or ("min", "at least 500 feet"). None when the wording doesn't say."""
    for m in _NUM_WITH_POS.finditer(quote or ""):
        if float(m.group(1).replace(",", "")) != abs(number):
            continue
        after, before = quote[m.end():m.end() + 40], quote[max(0, m.start() - 40):m.start()]
        for rx, way in _AFTER:
            hit = rx.match(after)
            if hit:
                return way, (m.group(1) + hit.group(0)).strip()
        for rx, way in _BEFORE:
            hit = rx.search(before)
            if hit:
                return way, (hit.group(0) + m.group(1)).strip()
    return None


NOT_A_LIMIT = "Not a limit on normal flight: "
# Wording that every POH uses for a number that is NOT an operating limit of
# the measured value itself, and that models keep turning into one.
_NOT_A_LIMIT = [
    # (metric, only when the rule is a bare maximum?, wording, why)
    ("ias", False, re.compile(r"abrupt\s+(?:use\s+of\s+(?:the\s+)?)?control|full\s+or\s+abrupt|man(?:o)?euvering\s+speed|"
                              r"manoeuvring\s+speed", re.I),
     "this is the manoeuvring speed - it limits full or abrupt control movements, not how fast the aircraft may fly"),
    ("ias", False, re.compile(r"demonstrated\s+crosswind|crosswind\s+velocity", re.I),
     "this is a wind speed (demonstrated crosswind), not an airspeed"),
    ("ias", True, re.compile(r"flap\s+retraction\s+speed|retract\w*\b[^.]{0,80}\b(?:after|until|when|once)\b[^.]{0,40}\breach", re.I),
     "this is the speed to reach BEFORE retracting the flaps, not a maximum speed"),
    ("ias", True, re.compile(r"obstacle\s+clearance\s+speed|until\s+(?:all\s+)?obstacles\s+are\s+cleared", re.I),
     "this is a climb speed to hold until obstacles are cleared (short-field technique), not a maximum speed"),
]


def not_a_limit(rule: dict) -> str | None:
    if rule.get("kind") not in ("limit", "band", "capture", "event") or rule.get("ref_speed"):
        return None
    quote = rule.get("source_quote") or ""
    max_only = rule.get("kind") == "limit" and rule.get("max_value") is not None and rule.get("min_value") is None
    for metric, needs_max_only, rx, why in _NOT_A_LIMIT:
        if rule.get("metric") == metric and (max_only or not needs_max_only) and rx.search(quote):
            return why
    return None


ERROR_LIST = "From a list of mistakes: "
# (a LIST of errors -- not "fixation is a common error", which is how the scan errors themselves are introduced)
_ERROR_LIST = re.compile(r"\bmost\s+common\s+errors\b|\bcommon\s+errors\s+(?:made|in|include|are|associated|when|while)\b|"
                         r"\berrors?\s+(?:made|committed)\s+(?:while|when|in)\b", re.I)


def from_error_list(rule: dict) -> str | None:
    """'The most common errors made while ... include: failure to clear the area':
    a list of what goes wrong, not a statement of what the pilot must do."""
    if _ERROR_LIST.search(rule.get("source_quote") or ""):
        return "the quote is a list of common errors - it describes mistakes, not a requirement"
    return None


_RANGE = re.compile(r"(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?:to|-|\u2013|\u2014)\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)(?!\s*(?:percent|%))")


def range_in_quote(quote: str, target: float) -> tuple[float, float] | None:
    """(low, high) of a range "A to B" / "A-B" in the quote that contains the
    rule's single target (as a magnitude): the text asked for a range."""
    for m in _RANGE.finditer(quote or ""):
        a, b = (float(g.replace(",", "")) for g in m.groups())
        if a < b and a <= abs(target) <= b and b <= 4 * max(a, 1):       # "2-4" page labels, "10-200" lists: not a flying range
            return a, b
    return None


def direction_conflict(rule: dict) -> str | None:
    """'Throttle -- 1000 RPM or LESS' drafted as a MINIMUM of 1000 rpm: the
    rule points the opposite way from the text it quotes. Only for a limit or
    capture with one bound, on wording that leaves no doubt. Scan rules too:
    "each area should be observed for at least 1 second" is not "never look
    outside for longer than 1 s"."""
    if rule.get("kind") == "gaze":
        v, measure = _f(rule.get("value")), rule.get("gaze_measure")
        if v is None or measure not in GAZE_MEASURES:
            return None
        share = GAZE_MEASURES[measure][1] == "fraction"
        mine = "min" if measure.startswith("min_") else "max"
        said = stated_direction(rule.get("source_quote") or "", round(v * 100, 4) if share else v)
        if said and said[0] != mine:
            shown = f"{v * 100:g}%" if share else f"{v:g} s"
            return (f"the text says '{said[1]}' (a minimum) but the rule sets a maximum of {shown} - it looks reversed"
                    if mine == "max" else
                    f"the text says '{said[1]}' (a maximum) but the rule sets a minimum of {shown} - it looks reversed")
        return None
    if rule.get("kind") not in ("limit", "capture"):
        return None
    lo, hi = _f(rule.get("min_value")), _f(rule.get("max_value"))
    if (lo is None) == (hi is None):
        return None
    mine, n = ("min", lo) if hi is None else ("max", hi)
    if rule.get("metric") in ("vs", "bank") and n < 0:
        return None                 # signed by our convention (descent, left bank): a "max descent" is a min
    said = stated_direction(rule.get("source_quote") or "", n)
    if said and said[0] != mine:
        return (f"the text says '{said[1]}' but the rule is a {'minimum' if mine == 'min' else 'maximum'} of {n:g} "
                f"- it looks reversed")
    return None


def check_provenance(rule: dict, pages: dict[int, str], ocr_pages: set[int]) -> tuple[list[str], str | None]:
    """Quote must exist in the SOP; numbers must appear in the quote."""
    issues: list[str] = []
    quote = rule.get("source_quote") or ""
    if not quote:
        return issues, "no source quote - can't be traced to the SOP"
    fq = foreign_quantity(rule)
    if fq:
        return issues, (f"the quote gives {fq}, which the simulator can't measure - it can't become a rule on "
                        f"{METRICS[rule['metric']][3].lower()}")
    page, score = locate_quote(quote, pages, rule.get("source_page"))
    scattered = False
    if score < QUOTE_MIN:
        hint = rule.get("source_page")
        for p in ([hint, (hint or 0) - 1, (hint or 0) + 1] if hint in pages else list(pages)[:0]):
            if p in pages and scattered_match(quote, pages[p]):
                page, score, scattered = p, QUOTE_MIN, True
                break
    rule["quote_score"] = round(score, 3)
    if score < QUOTE_MIN:
        return issues, f"quote not found in the SOP (best match {score:.0%})"
    if page != rule.get("source_page"):
        issues.append(f"Page corrected from {rule.get('source_page')} to {page}")
        rule["source_page"] = page
    if scattered:
        issues.append("The quote's words and numbers are on this page but not in this order (usually a table) - "
                      "compare the rule with the page image")
    elif score < QUOTE_EXACT:
        issues.append(f"Quote matches the SOP text {score:.0%}, not word for word")
    if page in ocr_pages:
        issues.append("From a scanned page (OCR) - compare the numbers with the page image")
    written = rule.get("as_written") or {}
    nums = [_f(v) for k, v in written.items() if k not in ("unit", "when")]
    nums += [_f(c.get("value")) for c in written.get("when", [])]
    in_quote = numbers_in(quote)
    # signs are our convention (descent = negative vs, left bank = negative), not the SOP's;
    # 0 is how "flaps UP" / "idle" is written, so it never has to be in the text
    missing = sorted({abs(n) for n in nums if n is not None and n != 0 and abs(n) not in in_quote})
    if rule.get("kind") == "gaze" and rule.get("origin", "llm") == "llm" and written.get("value") is not None \
            and (missing or not gaze_number_stated(rule)):
        # an invented threshold ("spend 50 % of the time...") is worse than none: drop it. The number
        # must be in the quote as a percentage / as seconds, not just appear in it somewhere.
        said = _f(written.get("value"))
        unit = "%" if GAZE_MEASURES.get(rule.get("gaze_measure"), ("", ""))[1] == "fraction" else " s"
        rule["value"] = None
        rule.pop("as_written", None)
        issues.append(f"The LLM gave {said:g}{unit}, which the quoted text does not say - removed. Set the threshold yourself")
        issues += share_without_number(rule)
    elif missing:
        issues.append(NOT_IN_QUOTE + ", ".join(f"{n:g}" for n in missing))
    wrong_way = direction_conflict(rule)
    if wrong_way:
        issues.append(WRONG_WAY + wrong_way)
    misread = not_a_limit(rule)
    if misread:
        issues.append(NOT_A_LIMIT + misread)
    listed = from_error_list(rule)
    if listed:
        issues.append(ERROR_LIST + listed)
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
    i4 = share_without_number(rule) if not (reject or r2) else []     # no-op when check_provenance already did it
    i5, r5 = check_example_copy(rule)
    return Checked(rule, issues + i2 + i3 + i4 + i5, reject or r3 or r2 or r5)


# The prompts show example rules. A weak model sometimes returns an example's TITLE
# for a passage that says something else ("Clear the area before turning" quoting a
# paragraph about pattern speeds). title -> what the quote must mention for the title
# to be believable.
_EXAMPLE_TITLES = [
    (re.compile(r"^clear the area before turning$", re.I), re.compile(r"\bclear|look(?:ing)?\s+(?:in|for|out)|\bscan", re.I)),
    (re.compile(r"^no more than about 10% of the time on the instruments", re.I), re.compile(r"\b10\s*percent|ten\s+percent", re.I)),
    (re.compile(r"^look outside about 90% of the time", re.I), re.compile(r"\b90\s*percent|ninety\s+percent", re.I)),
    (re.compile(r"^don.t fixate on the altimeter$", re.I), re.compile(r"altimeter", re.I)),
    (re.compile(r"^check airspeed often on final$", re.I), re.compile(r"airspeed", re.I)),
    (re.compile(r"^pattern altitude 1,?000 ft above the airport$", re.I), re.compile(r"\b1,?000\b", re.I)),
    (re.compile(r"^descent rate 500-1000 fpm on final$", re.I), re.compile(r"\b500\b", re.I)),
    (re.compile(r"^final(?: approach)?:? (?:approach )?(?:air)?speed within \+10/-5 kt of the approach speed$", re.I),
     re.compile(r"\+\s*10|10\s*(?:knots|kias|kt)", re.I)),
]


def check_example_copy(rule: dict) -> tuple[list[str], str | None]:
    title, quote = rule.get("title") or "", rule.get("source_quote") or ""
    for t_rx, q_rx in _EXAMPLE_TITLES:
        if t_rx.search(title.strip()) and not q_rx.search(quote):
            return [], ("the title is the prompt's own example, but the quoted text says something else - "
                        "the LLM copied the example instead of reading the passage")
    return [], None


def dedupe(checked: list[Checked], context=None) -> list[Checked]:
    """Chunks can overlap in content (repeated limits tables, summary pages):
    keep the first accepted copy of identical rules; merge phase lists.
    context(rule) -> anything hashable: rules are only merged within the same
    context. The pipeline passes the kind of lesson, so "climb at 70-80 KIAS"
    (normal takeoff) is not swallowed by "descend at 70-80 KIAS" (emergency
    descent through clouds), which happens to have the same numbers."""
    seen: dict[tuple, Checked] = {}
    quoted: set[tuple] = set()
    out: list[Checked] = []
    for c in checked:
        r = c.rule
        # the same passage turned into the same kind of rule again (overlapping
        # sections, or the model repeating itself): keep the first
        qkey = (r.get("kind"), r.get("metric"), r.get("aoi"), r.get("gaze_measure"), norm_text(r.get("source_quote") or ""))
        # (numeric rules are left alone: one sentence can rightly give two limits,
        # "65-75 KIAS flaps up ... 60-70 KIAS flaps down")
        same_passage = r.get("kind") in ("unobservable", "gaze") and bool(qkey[-1])
        if same_passage and qkey in quoted:
            continue
        if same_passage and not c.reject_reason:
            quoted.add(qkey)
        key = (r.get("kind"), r.get("metric"), r.get("aoi"), r.get("gaze_measure"),
               r.get("op"), r.get("gate_phase"), r.get("capture_at"), r.get("ref_speed"), r.get("ref_factor"),
               *(round(r[k], 2) if r.get(k) is not None else None for k in _NUM_FIELDS),
               tuple(sorted((x["metric"], x["op"], round(x["value"], 2)) for x in r.get("conditions") or [])),
               norm_text(r.get("title", "")) if r.get("kind") == "unobservable" else "",
               context(r) if context else None)
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


def assess(rule: dict, phases: list[str], pages: dict[int, str], ocr_pages: set[int],
           nominal: dict | None = None) -> tuple[list[str], str | None]:
    """Full check of a stored-unit rule as the reviewer edited it (draft or saved).
    Returns (warnings, blocking_reason). Mutates `rule` (drops unknown phases,
    corrects source_page, sets quote_score). A rule with a blocking reason
    can't be saved; warnings are shown but don't block."""
    issues, fatal = check_rule(rule, phases)
    if not fatal and rule.get("kind") == "band" and rule.get("tol_hi") is None and rule.get("tol_lo") is None:
        fatal = "set a tolerance (or make it a limit)"
    if not fatal and rule.get("kind") == "gaze" and _f(rule.get("value")) is None:
        fatal = "set the threshold - the source describes this without a number"
    quote = (rule.get("source_quote") or "").strip()
    if quote and pages:
        p_issues, p_fatal = check_provenance(rule, pages, ocr_pages)
        issues += p_issues
        if p_fatal:
            if (rule.get("origin") == "llm" and not rule.get("imported")) or p_fatal.startswith("the quote gives"):
                fatal = fatal or p_fatal
            else:
                # typed in by hand, or imported from a rules file that was already reviewed: warn, don't block
                issues.append("Source text not found in the SOP")
    elif rule.get("origin") == "llm" and not quote:
        issues.append("No source quote")
        rule["quote_score"] = None
    else:
        rule["quote_score"] = None if not quote else rule.get("quote_score")
    if nominal is not None and not fatal and "normal" in clean_lesson_types(rule.get("lesson_types")):
        # (an emergency or instrument-flying rule is not meant to fit a normal circuit)
        from .profile import circuit_conflict
        msg = circuit_conflict(rule, nominal)
        if msg:
            issues.append(msg)
    return issues, fatal


# ---------------------------------------------------------------- set-aside rules
BY_REVIEWER = "Set aside by the reviewer"


def aside_reason(rule: dict, fatal: str | None = None, issues: list[str] | None = None) -> str:
    """Why a rule is set aside, in one line: what blocks it, else the automatic
    check that set it aside (a correct circuit breaks it), else the reviewer."""
    blocked, auto = fatal or rule.get("blocked"), rule.get("auto_aside")
    if blocked and auto:               # e.g. an emergency-procedure rule that is also incomplete
        return (str(auto) if str(blocked) in str(auto) else f"{auto} (also: {blocked})")[:500]
    if blocked:
        return str(blocked)[:500]
    if auto:
        return str(auto)[:500]
    for i in (issues if issues is not None else rule.get("issues") or []):
        if i.startswith("Set aside: "):
            return i[len("Set aside: "):][:500]
        if i.startswith("A correctly flown circuit"):
            return i[:500]
    return BY_REVIEWER


def storable(rule: dict) -> dict:
    """A set-aside rule may be half-finished or malformed (that is often why it
    was set aside). Make it safe to store: anything outside the vocabulary
    becomes empty rather than breaking the save. Rules in use never need
    this -- they have passed assess()."""
    r = dict(rule)
    if r.get("kind") not in KINDS:
        r["kind"] = "unobservable"
    r["title"] = (str(r.get("title") or "").strip() or "Untitled rule")[:120]
    if r.get("op") not in OPS:
        r["op"] = None
    if r.get("capture_at") not in CAPTURE_AT:
        r["capture_at"] = None
    if r.get("gaze_measure") not in GAZE_MEASURES:
        r["gaze_measure"] = None
    if r.get("severity") not in SEVERITIES:
        r["severity"] = "caution"
    for k in _NUM_FIELDS:
        r[k] = _f(r.get(k))
    r["phases"] = [str(p) for p in (r.get("phases") or [])]
    r["lesson_types"] = clean_lesson_types(r.get("lesson_types"))
    if r.get("ref_speed") not in REF_SPEEDS or r.get("metric") != "ias":
        r["ref_speed"] = None
    r["ref_factor"] = (_f(r.get("ref_factor")) or 1.0) if r["ref_speed"] else None
    r["tags"] = [t for t in (r.get("tags") or []) if t in TAGS][:4]
    r["conditions"] = [c for c in (r.get("conditions") or [])
                       if isinstance(c, dict) and c.get("metric") in METRICS and c.get("op") in OPS
                       and _f(c.get("value")) is not None]
    sp = r.get("source_page")
    r["source_page"] = int(sp) if isinstance(sp, (int, float)) and sp >= 1 else None
    return r
