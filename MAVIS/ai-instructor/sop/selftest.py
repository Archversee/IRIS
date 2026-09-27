"""Self-test for the SOP pipeline's deterministic parts -- no Ollama, no
Postgres, no PDF needed.

Run:  python -m sop.selftest      (from ai-instructor/)
Exits non-zero on failure. Covers: quote verification (the anti-hallucination
gate), unit normalisation, kind requirements, plausibility, dedupe, aircraft
cross-checks and chunking.
"""
from __future__ import annotations

import sys

from . import extract, validate
from .llm_parse import parse_llm_output
from .schema import known_phases

PAGES = {
    1: "4.1 Normal Takeoff\nRotate at 55 KIAS. Climb out at Vy, 74 KIAS, +10/-5 knots, until reaching\n"
       "a safe altitude of at least 500 ft AGL.\nDo not exceed 30 degrees angle of bank below 1,000 ft AGL.",
    2: "Maintain circuit altitude of 1,000 ft AGL, +/-100 ft, on the downwind leg.\n"
       "Maximum flap extension speed: do not exceed 85 KIAS with flaps 30.\n"
       "Make the after-takeoff radio call to tower.",
    3: "Landing flaps 30 must be set before crossing 300 ft AGL on final.\n"
       "Rate of descent shall not exceed 1000 fpm below 500 ft AGL.",
}
OCR = {3}
PH = known_phases()

_fails = 0


def check(name: str, cond: bool, extra=""):
    global _fails
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    _fails += 0 if cond else 1


def v(raw):
    return validate.validate_candidate(raw, PH, PAGES, OCR)


def main() -> int:
    band = v({"kind": "band", "title": "Downwind height", "phases": ["DOWNWIND"], "metric": "alt_agl",
              "unit": "ft", "target": 1000, "tol_plus": 100, "tol_minus": 100, "severity": "caution",
              "source_quote": "Maintain circuit altitude of 1,000 ft AGL, +/-100 ft, on the downwind leg.", "page": 2})
    check("verbatim band rule accepted", band.status == "draft" and band.rule["quote_score"] == 1.0, band.reject_reason)
    check("band stored in ft", band.rule["target"] == 1000 and band.rule["tol_hi"] == 100)

    halluc = v({"kind": "limit", "title": "Vne", "phases": [], "metric": "ias", "unit": "kt", "max": 163,
                "severity": "warning", "source_quote": "Never exceed 163 KIAS under any circumstances.", "page": 1})
    check("invented quote auto-rejected", halluc.status == "auto_rejected", halluc.reject_reason)

    wrong_page = v({"kind": "capture", "title": "Rotate", "phases": ["TAKEOFF_ROLL"], "metric": "ias", "unit": "kt",
                    "capture_at": "liftoff", "min": 55, "severity": "caution",
                    "source_quote": "Rotate at 55 KIAS.", "page": 2})
    check("page corrected", wrong_page.status == "draft" and wrong_page.rule["source_page"] == 1,
          wrong_page.issues)

    sloppy = v({"kind": "band", "title": "Vy", "phases": ["CLIMB_OUT"], "metric": "ias", "unit": "kt",
                "target": 74, "tol_plus": 10, "tol_minus": 5, "severity": "caution",
                "source_quote": "Climb out at Vy, 74 KIAS, +10/-5 kts, until reaching a safe altitude", "page": 1})
    check("near-verbatim quote accepted with note", sloppy.status == "draft"
          and any("word for word" in i for i in sloppy.issues), (sloppy.reject_reason, sloppy.issues))

    wrong_num = v({"kind": "limit", "title": "Bank", "phases": ["CLIMB_OUT"], "metric": "bank", "unit": "deg",
                   "max": 45, "severity": "warning",
                   "source_quote": "Do not exceed 30 degrees angle of bank below 1,000 ft AGL.", "page": 1})
    check("number not in quote flagged", any("not in the quoted text" in i and "45" in i for i in wrong_num.issues),
          wrong_num.issues)

    metres = v({"kind": "limit", "title": "Height", "phases": [], "metric": "alt_agl", "unit": "m", "min": 500,
                "severity": "caution", "source_quote": "a safe altitude of at least 500 ft AGL.", "page": 1})
    check("metres converted to feet", abs(metres.rule["min_value"] - 1640.42) < 0.1)

    bad_unit = v({"kind": "limit", "title": "Speed", "phases": [], "metric": "ias", "unit": "ft", "max": 85,
                  "severity": "caution", "source_quote": "do not exceed 85 KIAS with flaps 30.", "page": 2})
    check("unit/metric mismatch auto-rejected", bad_unit.status == "auto_rejected")

    flaps = v({"kind": "limit", "title": "Vfe full", "phases": [], "metric": "ias", "unit": "kt", "max": 85,
               "when": [{"metric": "flaps_deg", "op": ">=", "value": 30, "unit": "deg"}], "severity": "warning",
               "source_quote": "Maximum flap extension speed: do not exceed 85 KIAS with flaps 30.", "page": 2})
    check("conditional limit kept", flaps.status == "draft" and flaps.rule["conditions"][0]["value"] == 30)

    event = v({"kind": "event", "title": "Landing flaps", "phases": ["FINAL"], "metric": "flaps_deg", "unit": "deg",
               "op": ">=", "value": 30, "gate_phase": "FINAL", "severity": "caution",
               "source_quote": "Landing flaps 30 must be set before crossing 300 ft AGL on final.", "page": 3})
    check("event from OCR page flagged", event.status == "draft" and any("OCR" in i for i in event.issues))

    radio = v({"kind": "unobservable", "title": "Radio call", "phases": ["CLIMB_OUT"], "metric": None,
               "unit": "none", "severity": "advisory",
               "source_quote": "Make the after-takeoff radio call to tower.", "page": 2})
    check("unobservable kept without numbers", radio.status == "draft" and radio.rule["metric"] is None)

    no_metric = v({"kind": "limit", "title": "Checklist", "phases": [], "metric": "fuel", "unit": "none",
                   "severity": "advisory", "source_quote": "Make the after-takeoff radio call to tower.", "page": 2})
    check("unknown metric downgraded to unobservable", no_metric.rule["kind"] == "unobservable")

    silly = v({"kind": "limit", "title": "Speed", "phases": [], "metric": "ias", "unit": "kt", "max": 8500,
               "severity": "caution", "source_quote": "do not exceed 85 KIAS with flaps 30.", "page": 2})
    check("implausible value auto-rejected", silly.status == "auto_rejected")

    no_target = v({"kind": "band", "title": "x", "phases": ["DOWNWIND"], "metric": "ias", "unit": "kt",
                   "severity": "caution", "source_quote": "do not exceed 85 KIAS with flaps 30.", "page": 2})
    check("band without target auto-rejected", no_target.status == "auto_rejected")

    phase = v({**{"kind": "limit", "title": "Sink", "metric": "vs", "unit": "fpm", "min": -1000,
                  "severity": "warning", "page": 3,
                  "source_quote": "Rate of descent shall not exceed 1000 fpm below 500 ft AGL."},
               "phases": ["FINAL", "APPROACH"]})
    check("unknown phase dropped", phase.rule["phases"] == ["FINAL"])

    d = validate.dedupe([band, v({"kind": "band", "title": "Circuit alt", "phases": ["BASE"], "metric": "alt_agl",
                                  "unit": "ft", "target": 1000, "tol_plus": 100, "tol_minus": 100,
                                  "severity": "caution", "page": 2,
                                  "source_quote": "Maintain circuit altitude of 1,000 ft AGL"})])
    check("duplicates merged", len(d) == 1 and set(d[0].rule["phases"]) == {"DOWNWIND", "BASE"})

    warns = validate.aircraft_checks(
        [{"title": "a", "metric": "ias", "kind": "limit", "max_value": 180, "phases": []},
         {"title": "b", "metric": "flaps_deg", "kind": "event", "value": 25, "phases": []}],
        {"v_speeds": {"vne": 163, "vs0": 40}, "flaps": {"detents_deg": [0, 10, 20, 30]}})
    check("aircraft cross-check", len(warns) == 2, warns)

    check("tolerant LLM JSON parse", len(parse_llm_output('```json\n{"rules":[{"kind":"limit"}]}\n```')) == 1)

    pages = [extract.PageText(i, "para " * 400, "text") for i in (1, 2, 3)]
    ch = extract.make_chunks(pages, 3000)
    check("chunks carry page markers", all(c.text.startswith("[[PAGE") for c in ch) and len(ch) >= 3)

    print("\nall passed" if not _fails else f"\n{_fails} FAILED")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
