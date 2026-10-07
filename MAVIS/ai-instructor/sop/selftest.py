"""Self-test for the SOP pipeline's deterministic parts -- no Ollama, no
Postgres, no PDF needed.

Run:  python -m sop.selftest      (from ai-instructor/)
Exits non-zero on failure. Covers: quote verification (the anti-hallucination
gate), unit normalisation, kind requirements, plausibility, dedupe, aircraft
cross-checks, chunking, the automatic set-aside checks and rules-file import.
"""
from __future__ import annotations

import sys

from . import extract, validate
from .llm_parse import handbook_prompt, parse_llm_output, system_prompt
from .schema import aircraft_profiles, known_phases

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

    from .runtime import match_profile
    profs = [{"id": "c172sp_g1000", "name": "Cessna 172SP G1000", "profile": {"sim_match": ["C172", "Skyhawk"]}},
             {"id": "c172_classic", "name": "Cessna 172 Classic", "profile": {"sim_match": ["Skyhawk Classic"]}},
             {"id": "da40", "name": "Diamond DA40", "profile": {"sim_match": ["DA40"]}}]
    check("sim aircraft -> profile", match_profile({"title": "Cessna Skyhawk G1000 Asobo"}, profs)[0] == "c172sp_g1000")
    check("most specific match wins",
          match_profile({"title": "Cessna Skyhawk Classic Asobo"}, profs)[0] == "c172_classic")
    check("ATC type code matches", match_profile({"title": "Foo", "atc_model": "TT:ATCCOM.AC_MODEL_DA40.0.text"}, profs)[0] == "da40")
    check("unknown aircraft -> no profile", match_profile({"title": "Piper Cub"}, profs)[0] is None)
    check("no ident -> no profile", match_profile(None, profs)[0] is None)

    # ---- v2: unit mix-ups, circuit-profile conflicts, tags, page selection
    from .validate import foreign_quantity, normalise_tags
    from .profile import load_nominal, conflicts
    from .extract import parse_pages, compress_pages, detect_section, outline
    oil = {"kind": "limit", "metric": "rpm", "source_quote": "Oil Pressure, Minimum: 20 PSI.",
           "as_written": {"min": 20, "unit": "rpm"}}
    check("oil pressure (PSI) can't become an RPM rule", bool(foreign_quantity(oil)))
    check("real RPM limit is not a unit mix-up", not foreign_quantity(
        {"kind": "limit", "metric": "rpm", "source_quote": "Maximum Engine Speed: 2700 RPM.",
         "as_written": {"max": 2700, "unit": "rpm"}}))
    N = load_nominal("c172sp_g1000")
    check("circuit profile loads from lesson + aircraft", N is not None and N["CLIMB_OUT"]["ias"] == (74.0, 74.0))
    bad = {  # real mistakes from a POH run with prompt v1
        "glide speed as climb limit": {"kind": "limit", "metric": "ias", "max_value": 68, "phases": ["CLIMB_OUT"]},
        "magneto check as RPM limit": {"kind": "limit", "metric": "rpm", "max_value": 1800, "phases": []},
        "autopilot min approach speed": {"kind": "limit", "metric": "ias", "min_value": 80, "phases": []},
        "autopilot off below 200 ft": {"kind": "limit", "metric": "alt_agl", "min_value": 200, "phases": ["BASE", "FINAL"]},
        "2000 ft over noise areas": {"kind": "band", "metric": "alt_agl", "target": 2000, "tol_hi": 0, "tol_lo": 1, "phases": []},
        "coupled approach 90 kt": {"kind": "band", "metric": "ias", "target": 90, "tol_hi": 0, "tol_lo": 1, "phases": ["BASE", "FINAL"]},
    }
    for name, r in bad.items():
        check(f"correct circuit breaks '{name}'", bool(conflicts(r, N)))
    good = {
        "Vne 163": {"kind": "limit", "metric": "ias", "max_value": 163, "phases": []},
        "max RPM 2700": {"kind": "limit", "metric": "rpm", "max_value": 2700, "phases": []},
        "Vfe 85 with flaps > 10": {"kind": "limit", "metric": "ias", "max_value": 85, "phases": [],
                                   "conditions": [{"metric": "flaps_deg", "op": ">", "value": 10}]},
        "approach 60-70 flaps down": {"kind": "limit", "metric": "ias", "min_value": 60, "max_value": 70, "phases": ["FINAL"],
                                      "conditions": [{"metric": "flaps_deg", "op": ">", "value": 0}]},
        "rotate at 55": {"kind": "capture", "metric": "ias", "capture_at": "liftoff", "min_value": 55},
        "flaps 30 by final": {"kind": "event", "metric": "flaps_deg", "op": ">=", "value": 30, "gate_phase": "FINAL"},
    }
    for name, r in good.items():
        check(f"correct circuit keeps '{name}'", not conflicts(r, N), conflicts(r, N))
    check("metric tag always added", normalise_tags(["aircraft_limit"], "limit", "flaps_deg") == ["aircraft_limit", "pilot_controls"])
    check("unknown tags dropped", normalise_tags(["nonsense"], "unobservable", None) == ["procedure"])
    check("page ranges parse", parse_pages("1-3, 7, 9-8", 20) == [1, 2, 3, 7, 8, 9] and compress_pages([1, 2, 3, 7, 8, 9]) == "1-3, 7-9")
    check("POH section from header", detect_section("SECTION 4\nNORMAL PROCEDURES\nblah\n4-5") == 4)
    check("POH section from footer label", detect_section("text\nmore text\nMay 30/00 2-7") == 2)
    ol = outline([{"page_no": i, "section": s, "needs_ocr": False} for i, s in [(1, 1), (2, 2), (3, 2), (4, 4)]])
    check("outline suggests Limitations + Normal Procedures", [o["suggested"] for o in ol] == [False, True, True])

    check("tolerant LLM JSON parse", len(parse_llm_output('```json\n{"rules":[{"kind":"limit"}]}\n```')) == 1)

    # ---- training handbooks: gaze rules, chapters, book detection, prompt
    HB = {5: "Fixation, or staring at a single instrument, is a common error.\n"
             "On final approach, check the airspeed at least every 5 seconds.\n"
             "Spend at least 70 percent of the time looking outside."}
    g = lambda raw: validate.validate_candidate(raw, PH, HB, set())  # noqa: E731
    open_gaze = g({"kind": "gaze", "title": "Don't fixate", "aoi": "instruments", "gaze_measure": "max_dwell",
                   "value": None, "unit": "none", "metric": None, "phases": [], "severity": "advisory", "tags": [],
                   "source_quote": "Fixation, or staring at a single instrument, is a common error.", "page": 5})
    check("gaze rule without a number drafted", open_gaze.status == "draft" and open_gaze.rule["value"] is None,
          (open_gaze.reject_reason, open_gaze.issues))
    check("gaze rule gets the scan tag", open_gaze.rule["tags"] == ["scan"])
    issues, fatal = validate.assess(dict(open_gaze.rule), PH, HB, set())
    check("gaze rule can't be saved without a threshold", bool(fatal) and "threshold" in fatal, fatal)
    gap = g({"kind": "gaze", "title": "Airspeed every 5 s", "aoi": "airspeed", "gaze_measure": "max_gap",
             "value": 5, "unit": "s", "phases": ["FINAL"], "severity": "advisory",
             "source_quote": "On final approach, check the airspeed at least every 5 seconds.", "page": 5})
    issues, fatal = validate.assess(dict(gap.rule), PH, HB, set())
    check("gaze rule with seconds accepted", gap.status == "draft" and gap.rule["value"] == 5 and not fatal,
          (gap.reject_reason, fatal))
    share = g({"kind": "gaze", "title": "Outside 70%", "aoi": "outside", "gaze_measure": "min_share",
               "value": 70, "unit": "percent", "phases": [], "severity": "advisory",
               "source_quote": "Spend at least 70 percent of the time looking outside.", "page": 5})
    check("gaze share percent -> fraction", share.status == "draft" and abs(share.rule["value"] - 0.7) < 1e-9,
          share.reject_reason)
    wrong_u = g({"kind": "gaze", "title": "x", "aoi": "airspeed", "gaze_measure": "max_gap", "value": 70,
                 "unit": "percent", "phases": [], "severity": "advisory",
                 "source_quote": "Spend at least 70 percent of the time looking outside.", "page": 5})
    check("gaze unit mismatch auto-rejected", wrong_u.status == "auto_rejected")
    bad_aoi = g({"kind": "gaze", "title": "x", "aoi": "yoke", "gaze_measure": "max_gap", "value": 5, "unit": "s",
                 "phases": [], "severity": "advisory",
                 "source_quote": "On final approach, check the airspeed at least every 5 seconds.", "page": 5})
    check("unknown AOI role auto-rejected", bad_aoi.status == "auto_rejected")
    silly_g = dict(gap.rule, value=5000)
    check("implausible gaze time blocked", bool(validate.check_rule(silly_g, PH)[1]))
    lim = dict(band.rule, aoi="airspeed", gaze_measure="max_gap")
    validate.check_rule(lim, PH)
    check("non-gaze rule drops gaze fields", lim["aoi"] is None and lim["gaze_measure"] is None)
    check("gaze rules never conflict with the circuit", not conflicts(gap.rule, N))

    from .extract import chapter_outline, detect_book, detect_chapter, suggest_chapter_pages
    check("chapter heading + title", detect_chapter("Chapter 8\nAirport Traffic Patterns\nIntroduction\n...") ==
          ("8", "Airport Traffic Patterns"))
    check("chapter title on the same line", detect_chapter("Chapter 9: Approaches and Landings\nblah") ==
          ("9", "Approaches and Landings"))
    check("chapter from footer label", detect_chapter("text\nmore\nAirplane Flying Handbook\n8-3") == ("8", None))
    check("table of contents is not a chapter page",
          detect_chapter("Contents\nChapter 1 Introduction ...1-1\nChapter 2 Ground Operations ...2-1") == (None, None))
    check("glossary pages", detect_chapter("Glossary\nterm\nG-4")[0] == "G")
    rows = [{"page_no": 1, "chapter": None, "chapter_title": None},
            {"page_no": 2, "chapter": "7", "chapter_title": "Ground Reference Maneuvers"},
            {"page_no": 3, "chapter": "7", "chapter_title": None},
            {"page_no": 4, "chapter": "8", "chapter_title": "Airport Traffic Patterns"},
            {"page_no": 5, "chapter": "8", "chapter_title": None},
            {"page_no": 6, "chapter": "G", "chapter_title": None}]
    co = chapter_outline(rows)
    check("chapter outline groups pages", [(o["chapter"], o["first"], o["last"]) for o in co] ==
          [(None, 1, 1), ("7", 2, 3), ("8", 4, 5), ("G", 6, 6)], co)
    check("traffic-pattern chapter suggested", suggest_chapter_pages(rows) == [4, 5])
    check("glossary named", co[-1]["title"] == "Glossary")
    mk = lambda t: [{"text": t}]  # noqa: E731
    check("AFH recognised", detect_book(mk("Airplane Flying Handbook (FAA-H-8083-3C)")) == ("afh", None))
    check("IFH recognised", detect_book(mk("Instrument Flying Handbook FAA-H-8083-15B")) == ("ifh", None))
    check("PHAK recognised", detect_book(mk("Pilot's Handbook of Aeronautical Knowledge FAA-H-8083-25C")) == ("phak", None))
    check("IPH flagged as no-rules book",
          detect_book(mk("Instrument Procedures Handbook FAA-H-8083-16B. See the Instrument Flying Handbook")) ==
          (None, "iph"))
    check("AMT handbook 8083-30 is not the AFH", detect_book(mk("FAA-H-8083-30 General handbook")) == (None, None))
    check("a POH is no handbook", detect_book(mk("CESSNA MODEL 172S Pilot's Operating Handbook")) == (None, None))

    # a book is identified only by its OWN name, never by the books it cites
    cite = "For more detail refer to the Pilot's Handbook of Aeronautical Knowledge (FAA-H-8083-25). " * 4
    body = lambda n: [{"text": ("Attitude instrument flying text. " * 60) + cite} for _ in range(n)]  # noqa: E731
    wrapped = [{"text": "\n".join(["A line of ordinary body text about the instrument scan."] * 40 +
                                   [f"point {i} is described in the Pilot's Handbook of Aeronautical", "Knowledge (FAA-H-8083-25)."])}
               for i in range(30)]
    check("a citation on the last lines of a page is not a footer", detect_book(wrapped) == (None, None), detect_book(wrapped))
    # the worst case: a wrapped line that is nothing but another book's title, at the foot of a quarter of the pages
    worst = [{"text": "\n".join(["A line of ordinary body text about the instrument scan."] * 40 +
                                 (["Pilot's Handbook of Aeronautical Knowledge (FAA-H-"] if i % 4 == 0 else ["More body text."]) +
                                 [f"6-{i}"])} for i in range(40)]
    check("another book's title at the foot of some pages is not a footer", detect_book(worst) == (None, None), detect_book(worst))
    check("IFH chapters citing the PHAK are not called the PHAK", detect_book(body(30)) == (None, None), detect_book(body(30)))
    check("IFH cover wins over PHAK citations in the text",
          detect_book(mk("Instrument Flying Handbook\n2012\nFAA-H-8083-15B") + body(30)) == ("ifh", None))
    check("a short page of body text is not a cover", detect_book(
        [{"text": "This short page ends a chapter. " * 6 + "See the Pilot's Handbook of Aeronautical Knowledge. " +
          "It carries on for a few more sentences so that it is clearly prose. " * 12}] + body(5)) == (None, None))
    check("a long first page is not a cover", detect_book(
        [{"text": "See the Pilot's Handbook of Aeronautical Knowledge. " + "Body text. " * 300}] + body(5)) == (None, None))
    meta = body(30)
    meta[0]["doc_title"] = "FAA-H-8083-15B Instrument Flying Handbook Chapter 6"
    check("PDF metadata title identifies the book", detect_book(meta) == ("ifh", None))
    foot = [{"text": ("Scan text. " * 80) + cite + f"\nInstrument Flying Handbook\n6-{i}"} for i in range(1, 25)]
    check("running footer identifies the book", detect_book(foot) == ("ifh", None), detect_book(foot))

    # more context per rule: overlap between sections, passage quotes, note, cut-off answers
    from .extract import CONTEXT_NOTE, PageText
    pg = [PageText(i, "\n\n".join(f"P{i} paragraph {k}. " + "word " * 120 for k in range(6)), "text") for i in (1, 2, 3)]
    ov = extract.make_chunks(pg, 6000, overlap=1000)
    check("overlap: later sections start with the end of the previous one",
          CONTEXT_NOTE not in ov[0].text and all(CONTEXT_NOTE in c.text and "P%d paragraph 5" % (c.pages[0] - 1) in c.text
                                                 for c in ov[1:]), [c.text[:80] for c in ov])
    check("overlap keeps sections within the limit", all(len(c.text) <= 6000 + 100 for c in ov), [len(c.text) for c in ov])
    check("no overlap unless asked", all(CONTEXT_NOTE not in c.text for c in extract.make_chunks(pg, 6000)))
    two = {1: "Intro. The attitude indicator is the hub of the scan. After each look at another instrument the pilot",
           2: "returns to the attitude indicator before moving on to the next one. Other text follows here."}
    q = "After each look at another instrument the pilot returns to the attitude indicator before moving on to the next one."
    check("a passage across a page break is found", validate.locate_quote(q, two, 2) == (1, 1.0), validate.locate_quote(q, two, 2))
    noted = g({"kind": "gaze", "title": "Airspeed every 5 s", "aoi": "airspeed", "gaze_measure": "max_gap",
               "value": 5, "unit": "s", "phases": ["FINAL"], "severity": "advisory", "page": 5,
               "note": "The book says airspeed is  the key instrument\non final.",
               "source_quote": "On final approach, check the airspeed at least every 5 seconds."})
    check("LLM note kept and labelled", noted.rule["note"] ==
          "LLM summary of the passage: The book says airspeed is the key instrument on final.", noted.rule["note"])
    check("POH rules have no LLM note", band.rule["note"] is None)
    cut = parse_llm_output('{"rules": [{"kind":"gaze","title":"a"}, {"kind":"limit","title":"b"}, {"kind":"ga')
    check("cut-off answer keeps its complete rules", [r["title"] for r in cut] == ["a", "b"], cut)

    from .llm_parse import handbook_prompt
    from .schema import llm_json_schema
    hp = handbook_prompt(PH, "ifh")
    check("handbook prompt carries the book focus", "FIXATION" in hp and "Instrument Flying Handbook" in hp)
    check("handbook prompt still skips flap and power settings", "Flap settings and power or RPM settings" in hp)
    check("POH schema has no gaze kind", "gaze" not in llm_json_schema(PH)["properties"]["rules"]["items"]
          ["properties"]["kind"]["enum"])
    check("handbook schema has gaze fields", "aoi" in llm_json_schema(PH, gaze=True)["properties"]["rules"]["items"]
          ["properties"])
    hs = llm_json_schema(PH, gaze=True)["properties"]["rules"]["items"]
    check("handbook schema asks for a note", "note" in hs["required"] and "note" not in
          llm_json_schema(PH)["properties"]["rules"]["items"]["properties"])
    check("handbook prompt: read the whole passage", "READ THE WHOLE PASSAGE" in hp and CONTEXT_NOTE in hp)

    # set-aside rules are saved, so they need a reason and a storable shape
    from .validate import BY_REVIEWER, aside_reason, storable
    check("aside reason: what blocks the rule", aside_reason({"blocked": "quote not found in the SOP (best match 40%)"}) ==
          "quote not found in the SOP (best match 40%)")
    check("aside reason: circuit check (draft wording)", aside_reason(
        {"issues": ["Set aside: A correctly flown circuit would break this rule: in Climb out ..."]}).startswith("A correctly flown"))
    check("aside reason: circuit check (re-check wording)", aside_reason(
        {"issues": ["Page corrected from 3 to 4", "A correctly flown circuit in the C172 would break this rule"]}).startswith("A correctly"))
    check("aside reason: reviewer's choice", aside_reason({"issues": ["Quote matches 97%"]}) == BY_REVIEWER)
    junk = storable({"kind": "weird", "title": "", "op": "~", "capture_at": "noon", "gaze_measure": "stare", "severity": "high",
                     "target": "fast", "max_value": "85", "phases": ["FINAL"], "tags": ["scan", "nonsense"],
                     "conditions": [{"metric": "ias", "op": ">", "value": 60}, {"metric": "fuel"}], "source_page": "x"})
    check("malformed set-aside rule made storable", junk["kind"] == "unobservable" and junk["title"] == "Untitled rule"
          and junk["op"] is None and junk["capture_at"] is None and junk["gaze_measure"] is None and junk["severity"] == "caution"
          and junk["target"] is None and junk["max_value"] == 85 and junk["tags"] == ["scan"] and len(junk["conditions"]) == 1
          and junk["source_page"] is None, junk)
    keep = storable(dict(gap.rule))
    check("a good rule is unchanged by storable", all(keep[k] == gap.rule[k] for k in ("kind", "aoi", "gaze_measure", "value", "phases")))

    from .profile import circuit_conflict
    pattern = {"kind": "limit", "metric": "ias", "max_value": 68, "phases": ["CLIMB_OUT"]}
    msg = circuit_conflict(pattern, [("Test plane", N)])
    check("handbook rules checked against every aircraft", bool(msg) and "Test plane" in msg, msg)

    # ---- prompt v3 / H3 and the checks added after reviewing full C172S, AFH and IFH runs
    from . import importer
    from .llm_parse import is_example_quote
    from .schema import llm_json_schema
    sch = llm_json_schema(PH, gaze=True)["properties"]["rules"]["items"]
    check("the LLM must write every field (none can be left out)", set(sch["required"]) == set(sch["properties"]),
          set(sch["properties"]) - set(sch["required"]))
    Q = {7: "BEFORE TAKEOFF 9. Throttle -- 1000 RPM or LESS. Abrupt use of the controls is prohibited above 105 KIAS. "
            "If no power is available, approach at 70 KIAS with flaps up. Climb Speed -- 60 KIAS. "
            "Approximately 90 percent of the pilot's attention should be devoted to outside visual references. "
            "The primary instrument for pitch is the altimeter.",
         # a real POH table as the PDF gives it: the remarks column is woven through the rows
         8: "VA Maneuvering \nSpeed: Do not make full or \n2550 Pounds 102 105 abrupt control \n2200 Pounds 95 98 movements above \n"
            "1900 Pounds 88 90 this speed. \nVFE Maximum Flap \nExtended Speed: Do not exceed this \n10° Flaps 107 110 speed with flaps \n"
            "10° to 30° Flaps 85 85 down. \nMaximum 160 163 Do not exceed this \n----- Window Open speed with windows \nSpeed open."}

    def vq(raw):
        return validate.validate_candidate(raw, PH, Q, set())
    rev = vq({"kind": "limit", "title": "Min RPM during takeoff", "metric": "rpm", "unit": "rpm", "min": 1000,
              "phases": ["TAKEOFF_ROLL"], "source_quote": "Throttle -- 1000 RPM or LESS.", "page": 7})
    check("'1000 RPM or LESS' drafted as a minimum is flagged as reversed",
          any(i.startswith(validate.WRONG_WAY) and "or LESS" in i for i in rev.issues), rev.issues)
    okay = vq({"kind": "limit", "title": "x", "metric": "ias", "unit": "kt", "max": 105, "phases": [],
               "source_quote": "Abrupt use of the controls is prohibited above 105 KIAS.", "page": 7})
    check("a maximum from 'prohibited above' is not flagged", not any(i.startswith(validate.WRONG_WAY) for i in okay.issues), okay.issues)
    check("manoeuvring speed drafted as a speed limit is flagged", any(i.startswith(validate.NOT_A_LIMIT) for i in okay.issues), okay.issues)
    vne = vq({"kind": "limit", "title": "x", "metric": "ias", "unit": "kt", "max": 163, "phases": [],
              "source_quote": "Maximum 160 163 Do not exceed this", "page": 8})
    check("...a real never-exceed speed is not", not any(i.startswith(validate.NOT_A_LIMIT) for i in vne.issues), vne.issues)
    check("'65 min' (minutes) is not read as 'minimum'", validate.stated_direction("hold for 65 min, then", 65) is None)
    same = vq({"kind": "limit", "title": "Approach speed", "metric": "ias", "unit": "kt", "min": 70, "max": 70, "phases": ["FINAL"],
               "source_quote": "approach at 70 KIAS with flaps up", "page": 7})
    check("min = max becomes a target that needs a tolerance", same.rule["kind"] == "band" and same.rule["target"] == 70
          and same.rule["min_value"] is None and same.rule["max_value"] is None, same.rule)
    one = vq({"kind": "limit", "title": "Climb speed", "metric": "ias", "unit": "kt", "value": 60, "phases": ["CLIMB_OUT"],
              "source_quote": "Climb Speed -- 60 KIAS.", "page": 7})
    check("a limit with only a 'value' is kept as a target and set aside", one.rule["kind"] == "band" and one.rule["target"] == 60
          and one.reject_reason and "single value" in one.reject_reason, (one.rule, one.reject_reason))
    share = vq({"kind": "gaze", "title": "Look outside", "aoi": "outside", "gaze_measure": "min_share", "value": 90,
                "unit": "percent", "phases": [], "source_quote": "Approximately 90 percent of the pilot's attention should be "
                "devoted to outside visual references.", "page": 7})
    check("'90 percent' is stored as a share of 0.9", share.reject_reason is None and abs(share.rule["value"] - 0.9) < 1e-9, share.rule)
    bare = vq({"kind": "gaze", "title": "Look outside", "aoi": "outside", "gaze_measure": "min_share", "value": 90,
               "unit": "none", "phases": [], "source_quote": "Approximately 90 percent of the pilot's attention should be "
               "devoted to outside visual references.", "page": 7})
    check("a share given as a bare 90 is read as 90%", bare.reject_reason is None and abs(bare.rule["value"] - 0.9) < 1e-9,
          (bare.rule.get("value"), bare.reject_reason))
    inv = vq({"kind": "gaze", "title": "Altimeter", "aoi": "altimeter", "gaze_measure": "min_share", "value": 50, "unit": "percent",
              "phases": ["DOWNWIND"], "source_quote": "The primary instrument for pitch is the altimeter.", "page": 7})
    check("an invented gaze number (50) is removed, not kept", inv.rule["value"] is None
          and any("does not say" in i for i in inv.issues) and inv.rule["gaze_measure"] == "max_gap", (inv.rule.get("value"), inv.issues))
    tbl = vq({"kind": "limit", "title": "Max speed with flaps 10-30", "metric": "ias", "unit": "kt", "max": 85, "phases": [],
              "when": [{"metric": "flaps_deg", "op": ">", "value": 10, "unit": "deg"}],
              "source_quote": "VFE Maximum Flap Extended Speed: 10° to 30° Flaps 85 85 Do not exceed this speed with flaps down.", "page": 8})
    check("a quote stitched together from a table is found", tbl.reject_reason is None
          and any("not in this order" in i for i in tbl.issues), (tbl.reject_reason, tbl.issues))
    fake = vq({"kind": "limit", "title": "Made up", "metric": "ias", "unit": "kt", "max": 95, "phases": [],
               "source_quote": "VFE Maximum Flap Extended Speed: 40° Flaps 95 Never fly faster with full flaps.", "page": 8})
    check("...but a quote with a number the page doesn't have is still refused", bool(fake.reject_reason), fake.reject_reason)
    u1 = {"kind": "unobservable", "title": "Clear the area", "metric": None, "unit": "none", "phases": [], "page": 7,
          "source_quote": "Abrupt use of the controls is prohibited above 105 KIAS."}
    dd = validate.dedupe([vq(dict(u1)), vq(dict(u1, title="Clear the area again")), vq(dict(u1, kind="limit", metric="ias",
                         unit="kt", max=105))])
    check("two 'not observable' rules from the same passage are one rule", [c.rule["kind"] for c in dd] == ["unobservable", "limit"],
          [c.rule["title"] for c in dd])
    check("the prompt's own example sentences are recognised", is_example_quote(
        "Before beginning any turn, look in the direction of the turn and clear the area of traffic.")
        and not is_example_quote("The learner should execute the appropriate clearing procedure before all turns."))
    poh_rows = [{"page_no": 1, "section": 2}, {"page_no": 2, "section": 3}, {"page_no": 3, "section": 3}, {"page_no": 4, "section": 4}]
    check("POH section 3 (emergency procedures) pages are known", extract.emergency_pages(poh_rows, False) == {2, 3})
    check("an automatic set-aside reason is the saved reason", aside_reason({"auto_aside": "emergency or abnormal procedure",
          "issues": []}) == "emergency or abnormal procedure" and aside_reason({"auto_aside": "x", "blocked": "needs a max"}) == "x (also: needs a max)")

    # ---- lesson types, aircraft-relative speeds, and what the v3 / H3 runs still got wrong
    from .pipeline import lesson_types_for
    from .profile import circuit_conflict as cc, load_nominal, nominals_all
    from .schema import clean_lesson_types, ref_base, resolve_ref
    Q[9] = ("The aircraft speed is within +10 /-5 KIAS of the recommended landing speed specified in the AFM, 1.3VSO, or on "
            "approved placards/markings. When no manufacturer's recommended approach speed is available, a speed of not more "
            "than 1.3 VSO is used. A descent rate (generally 500-1000 fpm for light general aviation aircraft) makes for a safe "
            "approach. The pilot makes necessary pitch and bank adjustments by referencing the attitude and heading indicators. "
            "10° to 30° Flaps 85 KIAS. Normal Climb Out 75-85 KIAS. If 10° wing flaps are used for takeoff, they should be left "
            "down until all obstacles are cleared and a safe flap retraction speed of 60 KIAS is reached. The most common errors "
            "made while performing crosswind takeoffs include the following: \u2022 Failure to adequately clear the area.")
    L = {"kind": "limit", "title": "Max speed with flaps 10-30", "metric": "ias", "unit": "kt", "max": 85, "phases": [], "page": 9,
         "when": [{"metric": "flaps_deg", "op": ">", "value": 10, "unit": "deg"}], "source_quote": "10° to 30° Flaps 85 KIAS."}
    stray = vq(dict(L, value=10, target=0))
    check("fields the rule's kind doesn't use are emptied (stray value on a limit)", stray.rule["value"] is None
          and stray.rule["target"] is None and stray.rule["max_value"] == 85 and "value" not in stray.rule["as_written"], stray.rule)
    check("...so the same limit drafted twice is one rule", len(validate.dedupe([vq(dict(L)), stray])) == 1)
    rng = vq({"kind": "band", "title": "Descent rate", "metric": "vs", "unit": "fpm", "min": -1000, "max": -500, "target": None,
              "phases": ["FINAL"], "page": 9, "source_quote": "A descent rate (generally 500-1000 fpm for light general aviation "
              "aircraft) makes for a safe approach."})
    check("a 'band' that is really a range becomes a limit", rng.rule["kind"] == "limit" and rng.reject_reason is None
          and rng.rule["min_value"] == -1000 and rng.rule["max_value"] == -500, (rng.rule["kind"], rng.reject_reason))
    rel = vq({"kind": "band", "title": "Approach speed +10/-5", "metric": "ias", "unit": "kt", "ref_speed": "approach",
              "ref_factor": 1, "target": None, "tol_plus": 10, "tol_minus": 5, "phases": ["FINAL"], "page": 9,
              "source_quote": "The aircraft speed is within +10 /-5 KIAS of the recommended landing speed specified in the AFM, "
              "1.3VSO, or on approved placards/markings."})
    check("aircraft-relative band: target is 'exactly that speed'", rel.reject_reason is None and rel.rule["ref_speed"] == "approach"
          and rel.rule["target"] == 0 and rel.rule["tol_hi"] == 10 and rel.rule["tol_lo"] == 5
          and not any(i.startswith(validate.NOT_IN_QUOTE) for i in rel.issues), (rel.rule, rel.reject_reason, rel.issues))
    lost = vq({"kind": "band", "title": "Airspeed within +10/-5 KIAS of recommended landing speed", "metric": "ias", "unit": "kt",
               "target": None, "tol_plus": 10, "tol_minus": 5, "ref_speed": None, "ref_factor": None, "phases": ["FINAL"], "page": 9,
               "source_quote": "The aircraft speed is within +10 /-5 KIAS of the recommended landing speed specified in the AFM, "
               "1.3VSO, or on approved placards/markings."})
    check("the LLM left the speed empty but the text names it: the approach speed is used", lost.reject_reason is None
          and lost.rule["ref_speed"] == "approach" and lost.rule["target"] == 0 and any("names the approach speed" in i for i in lost.issues),
          (lost.rule.get("ref_speed"), lost.reject_reason, lost.issues))
    bare = vq(dict(lost.rule["as_written"] and {}, kind="band", title="Airspeed within +10/-5", metric="ias", unit="kt", target=None,
                   tol_plus=None, tol_minus=None, phases=["FINAL"], page=9, source_quote=lost.rule["source_quote"]))
    check("...and with no numbers at all, the +10/-5 tolerance is read from the quote", bare.rule["ref_speed"] == "approach"
          and bare.rule["tol_hi"] == 10 and bare.rule["tol_lo"] == 5 and bare.reject_reason is None, (bare.rule, bare.reject_reason))
    check("named speeds are recognised in the text", validate.infer_ref("a speed of not more than 1.3 VSO is used") == ("vs0", 1.3)
          and validate.infer_ref("climb at VY until 500 feet") == ("vy", 1.0) and validate.infer_ref("hold 65 knots") is None)
    pct = vq({"kind": "gaze", "title": "Scan", "aoi": "outside", "gaze_measure": "min_share", "value": None, "unit": "none", "phases": [],
              "page": 7, "source_quote": "Approximately 90 percent of the pilot's attention should be devoted to outside visual references."})
    check("a share is kept (not turned into seconds) when the quote does give a percentage", pct.rule["gaze_measure"] == "min_share"
          and any("gives a percentage" in i for i in pct.issues), (pct.rule["gaze_measure"], pct.issues))
    got = resolve_ref(rel.rule, {"final": 65})
    check("...resolved for an aircraft: 65 kt +10/-5", got["target"] == 65 and got["tol_hi"] == 10 and got["ref_resolved"] == 65, got)
    check("...an aircraft whose profile lacks the speed: rule not usable there", resolve_ref(rel.rule, {"vy": 74}) is None
          and ref_base(rel.rule, {}) is None)
    vso = vq({"kind": "limit", "title": "Not more than 1.3 VSO", "metric": "ias", "unit": "kt", "ref_speed": "vs0", "ref_factor": 1.3,
              "max": 0, "phases": ["FINAL"], "page": 9, "source_quote": "a speed of not more than 1.3 VSO is used."})
    check("1.3 x VSO: factor kept and found in the quote", vso.rule["ref_factor"] == 1.3 and vso.rule["max_value"] == 0
          and resolve_ref(vso.rule, {"vs0": 40})["max_value"] == 52 and not any(i.startswith(validate.NOT_IN_QUOTE) for i in vso.issues),
          (vso.rule, vso.issues))
    far = dict(rel.rule, target=65.0)
    check("a full speed typed where a difference belongs is refused", "implausibly far" in (validate.check_rule(far, PH)[1] or ""))
    bad = dict(rel.rule, metric="alt_agl")
    validate.check_rule(bad, PH)
    check("a speed reference on a non-airspeed rule is removed", bad["ref_speed"] is None and bad["ref_factor"] is None)
    c172 = load_nominal("c172sp_g1000")
    if c172:
        check("relative rule checked against each aircraft's own speed", cc(rel.rule, nominals_all()) is None
              and "Final" in (cc(vso.rule, nominals_all()) or ""), cc(vso.rule, nominals_all()))
        climb = {"kind": "limit", "metric": "ias", "min_value": 75, "max_value": 85, "phases": ["CLIMB_OUT"]}
        check("'Normal climb 75-85' is not contradicted by a 74 kt climb (5 kt slack)", cc(climb, c172) is None, cc(climb, c172))
        check("...an engine-out glide speed as a climb limit still is", bool(cc(dict(climb, min_value=None, max_value=68), c172)))
        glide = {"kind": "limit", "metric": "ias", "max_value": 68, "phases": ["CLIMB_OUT"], "origin": "manual", "title": "Glide"}
        check("the circuit check leaves emergency-lesson rules alone", any("correctly flown" in i for i in validate.assess(
            dict(glide, lesson_types=["normal"]), PH, {}, set(), c172)[0]) and not any("correctly flown" in i for i in validate.assess(
            dict(glide, lesson_types=["emergency"]), PH, {}, set(), c172)[0]))
    nosh = vq({"kind": "gaze", "title": "Refer to the attitude indicator", "aoi": "attitude", "gaze_measure": "min_share", "value": None,
               "unit": "none", "phases": ["CLIMB_OUT"], "page": 9, "source_quote": "The pilot makes necessary pitch and bank "
               "adjustments by referencing the attitude and heading indicators."})
    check("a share with no percentage in the book becomes 'look at it at least every N s'", nosh.rule["gaze_measure"] == "max_gap"
          and nosh.reject_reason is None and any("no percentage" in i for i in nosh.issues), (nosh.rule["gaze_measure"], nosh.issues))
    retract = vq({"kind": "limit", "title": "Max speed with flaps 10", "metric": "ias", "unit": "kt", "max": 60, "value": 0,
                  "phases": ["TAKEOFF_ROLL"], "page": 9, "source_quote": "If 10° wing flaps are used for takeoff, they should be left "
                  "down until all obstacles are cleared and a safe flap retraction speed of 60 KIAS is reached."})
    check("flap-retraction speed drafted as a maximum speed is flagged", any(i.startswith(validate.NOT_A_LIMIT) and "BEFORE retracting" in i
          for i in retract.issues), retract.issues)
    errs = vq({"kind": "unobservable", "title": "Clear the area before taxiing onto the runway", "metric": None, "unit": "none",
               "phases": ["PRE_TAKEOFF"], "page": 9, "source_quote": "The most common errors made while performing crosswind takeoffs "
               "include the following: \\u2022 Failure to adequately clear the area."})
    check("a rule taken from a 'common errors' list is flagged", any(i.startswith(validate.ERROR_LIST) for i in errs.issues), errs.issues)
    check("'fixation is a common error' is not a list of errors", validate.from_error_list({"source_quote":
          "Fixation, or staring at one instrument, is a common error observed in pilots first learning to utilize trend indicators."}) is None)
    check("an escape code the model wrote for a bullet is turned back into the character", "\u2022" in errs.rule["source_quote"]
          and "\\u2022" not in errs.rule["source_quote"] and errs.reject_reason is None, errs.rule["source_quote"])
    check("lesson type: emergency by tag, by page, or by wording", lesson_types_for({"tags": ["emergency"]}, None, False, False) == ["emergency"]
          and lesson_types_for({"tags": []}, None, True, False) == ["emergency"]
          and lesson_types_for({"title": "Approach speed, engine failure", "tags": []}, None, False, False) == ["emergency"]
          and lesson_types_for({"title": "Climb speed", "source_quote": "Climb Speed -- 70-80 KIAS", "tags": []}, None, False, False) == ["normal"])
    check("lesson type: instrument-handbook shares are for instrument lessons only", lesson_types_for(
        {"kind": "gaze", "gaze_measure": "min_share", "aoi": "attitude"}, "ifh", False, False) == ["instrument"])
    check("lesson type: EVERY rule from the instrument handbook is for instrument lessons only", lesson_types_for(
        {"kind": "gaze", "gaze_measure": "max_dwell", "aoi": "instruments"}, "ifh", False, False) == ["instrument"]
        and lesson_types_for({"kind": "gaze", "gaze_measure": "max_gap", "aoi": "altimeter"}, "afh", False, True) == ["instrument"]
        and lesson_types_for({"kind": "band", "metric": "alt_agl"}, "ifh", False, False) == ["instrument"])
    check("lesson type: a go-around is a normal procedure even when the LLM tags it emergency", lesson_types_for(
        {"title": "Execute go-around if approach is not stabilized below 300 ft AGL", "tags": ["procedure", "emergency"]}, "afh", False, False)
        == ["normal"] and lesson_types_for({"title": "Go-around (balked landing) procedure", "tags": ["emergency"]}, None, False, False) == ["normal"])
    check("lesson type: in a handbook the LLM's emergency tag alone is not enough", lesson_types_for(
        {"title": "Reduce power abeam the numbers", "tags": ["emergency"]}, "afh", False, False) == ["normal"]
        and lesson_types_for({"title": "Wing fire checklist", "tags": ["emergency"]}, None, False, False) == ["emergency"])
    from .schema import missing_ref_speeds, used_in_text
    check("rules say in words where they apply", used_in_text(["instrument"]) == "Instrument-flying lessons only"
          and used_in_text(["emergency"]) == "Emergency lessons only" and used_in_text(None) == "Normal circuit lessons"
          and used_in_text(["normal", "instrument"]) == "Normal circuit and instrument-flying lessons")
    for ac in aircraft_profiles():
        check(f"aircraft profile {ac['id']} gives every speed a rule can be measured from",
              not missing_ref_speeds(ac["profile"].get("v_speeds")), missing_ref_speeds(ac["profile"].get("v_speeds")))
    check("lesson types are never empty or unknown", clean_lesson_types([]) == ["normal"] and clean_lesson_types(["x", "emergency"]) == ["emergency"])
    sch4 = llm_json_schema(PH, gaze=True)["properties"]["rules"]["items"]
    check("handbook schema has ref_speed / ref_factor; the POH schema doesn't", {"ref_speed", "ref_factor"} <= set(sch4["required"])
          and "ref_speed" not in llm_json_schema(PH)["properties"]["rules"]["items"]["properties"])
    hp4, pp4 = handbook_prompt(PH, "afh"), system_prompt(PH, "Cessna 172SP")
    check("prompts: aircraft-relative speeds, emergencies kept", "AIRCRAFT-RELATIVE SPEEDS" in hp4 and 'ref_speed "vs0"' in hp4
          and "EMERGENCY PROCEDURES" in pp4 and "Return NOTHING for these" not in pp4 and '"emergency"' in pp4)
    check("prompt: instrument handbook asks for the scan technique and primary instruments", "80-90 percent" in handbook_prompt(PH, "ifh")
          and "PRIMARY instruments" in handbook_prompt(PH, "ifh"))

    # ---- what the v4 / H4 runs still got wrong
    from .pipeline import coverage_gaps
    from .profile import phase_conflicts, with_provisional_tolerance
    Q[10] = ("When the wing is at a height equal to 1/10 the span, the reduction in induced drag is about 50 percent. "
             "Before joining the downwind leg, adjust course or speed to fit the traffic. Speeds recommended by the airplane "
             "manufacturer should be used. 10. Approach at 65 to 75 KIAS depending upon the amount of the accumulation. "
             "Reduce power to set up a 500 to 800 ft/min rate of descent. Throttle -- 1000 RPM or LESS. Throttle -- 1800 RPM. "
             "The pilot should use the manufacturer's recommended airspeed or 1.3 VSO if there is no manufacturer's recommendation. "
             "When the selected radial cross-check is used, a pilot spends 80 to 90 percent of flight time looking at the attitude "
             "indicator. The traffic pattern altitude is usually 1,000 feet above the elevation of the airport surface.")
    ge = vq({"kind": "gaze", "title": "Monitor instruments for ground effect", "aoi": "instruments", "gaze_measure": "min_share", "value": 10,
             "unit": "percent", "phases": ["TAKEOFF_ROLL"], "page": 10, "source_quote": "When the wing is at a height equal to 1/10 the span, "
             "the reduction in induced drag is about 50 percent."})
    check("a scan percentage must be IN the text as a percentage ('1/10 the span' is not 10 %)", ge.rule["value"] is None
          and any("does not say" in i for i in ge.issues), (ge.rule["value"], ge.issues))
    radial = vq({"kind": "gaze", "title": "Attitude indicator 80-90%", "aoi": "attitude", "gaze_measure": "min_share", "value": 80,
                 "unit": "percent", "phases": [], "page": 10, "source_quote": "When the selected radial cross-check is used, a pilot spends "
                 "80 to 90 percent of flight time looking at the attitude indicator."})
    check("...'80 to 90 percent' is one", radial.rule["value"] == 0.8 and radial.reject_reason is None, (radial.rule["value"], radial.issues))
    copy = vq({"kind": "unobservable", "title": "Clear the area before turning", "metric": None, "unit": "none", "phases": ["BASE"], "page": 10,
               "source_quote": "Before joining the downwind leg, adjust course or speed to fit the traffic. Speeds recommended by the airplane "
               "manufacturer should be used."})
    check("an example title on a passage about something else is refused", "copied the example" in (copy.reject_reason or ""), copy.reject_reason)
    PAGES_X = {1: "The learner should execute the appropriate clearing procedure before all turns."}
    okc = validate.validate_candidate({"kind": "unobservable", "title": "Clear the area before turning", "metric": None, "unit": "none",
                                       "phases": [], "page": 1, "source_quote": PAGES_X[1]}, PH, PAGES_X, set())
    check("...the same title on a passage that is about clearing the area is fine", okc.reject_reason is None, okc.reject_reason)
    icing = vq({"kind": "band", "title": "Icing: approach speed", "metric": "ias", "unit": "kt", "target": 75, "phases": ["FINAL"], "page": 10,
                "source_quote": "10. Approach at 65 to 75 KIAS depending upon the amount of the accumulation."})
    check("a range in the text drafted as one target becomes the range", icing.rule["kind"] == "limit" and icing.rule["min_value"] == 65
          and icing.rule["max_value"] == 75 and icing.rule["target"] is None and icing.reject_reason is None, icing.rule)
    desc = vq({"kind": "band", "title": "Descent rate", "metric": "vs", "unit": "fpm", "target": -600, "phases": [], "page": 10,
               "source_quote": "Reduce power to set up a 500 to 800 ft/min rate of descent."})
    check("...also for a descent rate (kept negative) when the LLM chose a middle value", desc.rule["kind"] == "limit"
          and desc.rule["min_value"] == -800 and desc.rule["max_value"] == -500
          and not any(i.startswith(validate.NOT_IN_QUOTE) for i in desc.issues), (desc.rule, desc.issues))
    orless = vq({"kind": "band", "title": "Takeoff RPM (ground run)", "metric": "rpm", "unit": "rpm", "target": 1000, "phases": ["PRE_TAKEOFF"],
                 "page": 10, "source_quote": "Throttle -- 1000 RPM or LESS."})
    check("'1000 RPM or LESS' drafted as a target becomes a maximum", orless.rule["kind"] == "limit" and orless.rule["max_value"] == 1000
          and orless.rule["min_value"] is None, orless.rule)
    fb = vq({"kind": "band", "title": "Final approach airspeed 1.3 VSO", "metric": "ias", "unit": "kt", "ref_speed": "vs0", "ref_factor": 1.3,
             "target": 0, "phases": ["FINAL"], "page": 10, "source_quote": "The pilot should use the manufacturer's recommended airspeed "
             "or 1.3 VSO if there is no manufacturer's recommendation."})
    check("'manufacturer's speed, or 1.3 VSO if there is none' is measured from the approach speed", fb.rule["ref_speed"] == "approach"
          and fb.rule["ref_factor"] == 1.0 and any("fallback" in i for i in fb.issues), (fb.rule["ref_speed"], fb.issues))
    if c172:
        pat = {"kind": "band", "metric": "alt_agl", "target": 1000, "tol_hi": None, "tol_lo": None, "phases": ["BASE", "DOWNWIND", "FINAL"]}
        bad = phase_conflicts(pat, nominals_all())
        check("'pattern altitude 1,000 ft' doesn't fit Base and Final (you descend there), only Downwind", set(bad) == {"BASE", "FINAL"}, bad)
        runup = {"kind": "band", "metric": "rpm", "target": 1800, "tol_hi": None, "tol_lo": None, "phases": ["PRE_TAKEOFF"]}
        check("a target without a tolerance is still compared with the circuit (run-up 1800 RPM held all of pre-takeoff)",
              cc(runup, c172) is None and bool(cc(with_provisional_tolerance(runup), c172)))
        vy = {"kind": "band", "metric": "ias", "target": 74, "tol_hi": None, "tol_lo": None, "phases": ["CLIMB_OUT"]}
        check("...and a correct one passes", cc(with_provisional_tolerance(vy), c172) is None)
    poh_rules = [{"kind": "limit", "metric": "ias", "max_value": 163, "phases": [], "conditions": [], "lesson_types": ["normal"]},
                 {"kind": "limit", "metric": "ias", "max_value": 85, "phases": [], "conditions": [{"metric": "flaps_deg"}]},
                 {"kind": "band", "metric": "ias", "target": 68, "phases": ["CLIMB_OUT"], "lesson_types": ["emergency"]},
                 {"kind": "limit", "metric": "ias", "min_value": 60, "max_value": 70, "phases": ["FINAL"], "lesson_types": ["normal"]},
                 {"kind": "unobservable", "title": "Before takeoff checklist"}, {"kind": "unobservable", "title": "Before landing checklist"}]
    gaps = coverage_gaps(poh_rules, False, None)
    check("coverage: says what a POH run is missing (an emergency climb speed doesn't count)", gaps == [
        "the maximum engine RPM", "the rotation (lift-off) speed", "the climb speed after takeoff"], gaps)
    check("coverage: nothing to say when everything is there", coverage_gaps(poh_rules + [
        {"kind": "limit", "metric": "rpm", "max_value": 2700, "lesson_types": ["normal"]},
        {"kind": "capture", "metric": "ias", "capture_at": "liftoff", "min_value": 55, "lesson_types": ["normal"]},
        {"kind": "limit", "metric": "ias", "min_value": 70, "max_value": 80, "phases": ["CLIMB_OUT"], "lesson_types": ["normal"]}], False, None) == [])

    # ---- FAA suggestions for rules the document leaves without a number
    from . import suggest as sg
    from .pipeline import auto_review
    def faa(**r):
        got = sg.suggest({"lesson_types": ["normal"], "phases": [], **r})
        return (got["fields"].get("tol_hi", got["fields"].get("value")), got["fields"].get("tol_lo"), got["source"], got["exact"]) if got else None
    B = dict(kind="band", metric="ias", target=74)
    check("FAA: climb speed +10/-5 (PA.IV.A)", faa(**B, phases=["CLIMB_OUT"])[:2] == (10, 5) and "PA.IV.A" in faa(**B, phases=["CLIMB_OUT"])[2])
    check("FAA: approach speed +10/-5 (PA.IV.B), also for a rule measured from the approach speed",
          faa(**B, phases=["FINAL"])[:2] == (10, 5) and "PA.IV.B" in faa(kind="band", metric="ias", target=0, ref_speed="approach")[2])
    check("FAA: pattern speed +/-10, pattern altitude +/-100 (PA.III.B)", faa(**B, phases=["DOWNWIND"])[:2] == (10, 10)
          and faa(kind="band", metric="alt_agl", target=1000, phases=["DOWNWIND"])[:2] == (100, 100))
    check("FAA: best glide +/-10 (PA.IX.B), emergency descent +0/-10 (PA.IX.A)",
          faa(**B, title="Engine failure in flight: glide speed", lesson_types=["emergency"])[:2] == (10, 10)
          and faa(**B, title="Emergency descent through clouds: airspeed", lesson_types=["emergency"])[:2] == (0, 10))
    near = faa(**B, title="Wing fire: airspeed", lesson_types=["emergency"])
    check("FAA: another emergency speed gets the nearest standard, and says so", near[3] is False and "nearest FAA standard" in sg.describe(
        sg.suggest({**B, "title": "Wing fire: airspeed", "lesson_types": ["emergency"]})))
    check("FAA: instrument flying +/-10 kt, +/-200 ft, +/-20 deg (PA.VIII)", faa(**B, lesson_types=["instrument"])[:2] == (10, 10)
          and faa(kind="band", metric="alt_msl", target=3000, lesson_types=["instrument"])[:2] == (200, 200)
          and faa(kind="band", metric="hdg", target=90, lesson_types=["instrument"])[:2] == (20, 20))
    check("FAA: scan - never more than 5 s away from the outside view (AIM 8-1-6)", faa(kind="gaze", aoi="outside", gaze_measure="max_gap", value=None)[0] == 5
          and faa(kind="gaze", aoi="instruments", gaze_measure="max_dwell", value=None)[0] == 5)
    check("FAA has no number for: engine RPM, descent rate, an instrument-lesson scan rule, a rule that already has one",
          faa(kind="band", metric="rpm", target=1800) is None and faa(kind="band", metric="vs", target=-500) is None
          and faa(kind="gaze", aoi="altimeter", gaze_measure="max_gap", value=None, lesson_types=["instrument"]) is None
          and faa(**B, tol_hi=5, tol_lo=5) is None and faa(kind="limit", metric="ias", max_value=163) is None)
    FP = {5: "Best Rate-of-Climb, Sea Level 74 KIAS. The traffic pattern altitude is usually 1,000 feet above the elevation of the airport "
             "surface. The pilot should continue to scan outside to adjust the attitude. Magneto check: Throttle -- 1800 RPM."}
    def drafted(raw):
        c = validate.validate_candidate({"severity": "caution", "tags": [], "page": 5, **raw}, PH, FP, set())
        return {**c.rule, "cid": raw["title"], "origin": "llm", "include": c.reject_reason is None, "blocked": c.reject_reason,
                "issues": list(c.issues), "enabled": True}
    rr = [drafted({"kind": "band", "title": "Vy", "metric": "ias", "unit": "kt", "target": 74, "phases": ["CLIMB_OUT"],
                   "source_quote": "Best Rate-of-Climb, Sea Level 74 KIAS."}),
          drafted({"kind": "band", "title": "Pattern altitude", "metric": "alt_agl", "unit": "ft", "target": 1000, "phases": ["DOWNWIND", "BASE", "FINAL"],
                   "source_quote": "The traffic pattern altitude is usually 1,000 feet above the elevation of the airport surface."}),
          drafted({"kind": "gaze", "title": "Keep scanning outside", "aoi": "outside", "gaze_measure": "max_gap", "value": None, "unit": "none",
                   "phases": [], "source_quote": "The pilot should continue to scan outside to adjust the attitude."}),
          drafted({"kind": "band", "title": "Run-up", "metric": "rpm", "unit": "rpm", "target": 1800, "phases": ["PRE_TAKEOFF"],
                   "source_quote": "Throttle -- 1800 RPM."})]
    n = auto_review(rr, phases=PH, page_map=FP, ocr_pages=set(), nominal=c172, handbook=None)
    vy_r, pat_r, scan_r, run_r = rr
    check("run: a target without a tolerance gets the FAA's, pre-filled, and stays set aside for the reviewer", not vy_r["include"]
          and (vy_r["tol_hi"], vy_r["tol_lo"]) == (10, 5) and vy_r["blocked"] is None and vy_r["suggested"]["shown"] == "+10 / −5 kt"
          and vy_r["auto_aside"].startswith("Tolerance +10 / −5 kt is a suggestion from FAA Private Pilot ACS")
          and "FAA suggestion: tolerance +10 / −5 kt" in vy_r["note"], (vy_r["include"], vy_r["blocked"], vy_r.get("auto_aside"), vy_r.get("note")))
    check("run: pattern altitude keeps Downwind only and gets +/-100 ft", pat_r["phases"] == ["DOWNWIND"] and pat_r["tol_hi"] == 100
          and not pat_r["include"] and pat_r["blocked"] is None, (pat_r["phases"], pat_r["tol_hi"], pat_r["blocked"]))
    check("run: a scan rule without a number gets the AIM's 5 s", scan_r["value"] == 5 and not scan_r["include"] and scan_r["blocked"] is None
          and "Threshold 5 s" in scan_r["auto_aside"], (scan_r["value"], scan_r.get("auto_aside")))
    check("run: no FAA number exists for a run-up RPM - and a correct circuit breaks it anyway", "suggested" not in run_r
          and "correctly flown" in (run_r.get("auto_aside") or ""), run_r.get("auto_aside"))
    check("run: counted for the summary", n.get("suggested") == 3 and n.get("conflict") == 1, n)
    # the generous provisional tolerance lets "1,250 ft" pass the circuit check; the FAA's +/-100 ft does not
    FP2 = {5: "Fly the downwind leg at 1,250 feet above the airport."}
    hi = [{**validate.validate_candidate({"severity": "caution", "tags": [], "page": 5, "kind": "band", "title": "High downwind",
           "metric": "alt_agl", "unit": "ft", "target": 1250, "phases": ["DOWNWIND"],
           "source_quote": "Fly the downwind leg at 1,250 feet above the airport."}, PH, FP2, set()).rule,
           "cid": "hi", "origin": "llm", "include": True, "blocked": None, "issues": [], "enabled": True}]
    n2 = auto_review(hi, phases=PH, page_map=FP2, ocr_pages=set(), nominal=c172, handbook=None)
    check("run: a suggestion that a correct circuit would break is set aside for THAT reason", not hi[0]["include"]
          and "correctly flown" in (hi[0].get("auto_aside") or "") and n2.get("conflict") == 1 and not n2.get("suggested")
          and hi[0]["suggested"]["ready"] is False,
          (hi[0].get("auto_aside"), n2))
    # ---- quotes a model shortened with "..." (qwen3.6 does this): found when every piece is there
    PG = {7: "TAKEOFF\nNORMAL TAK EOFF\n1. Wing Flaps -- 0°-10°.\n2. Throttle -- FULL OPEN.\n3. Mixture -- RICH.\n"
             "4. Elevator Control -- LIFT NOSE WHEEL (at 55 KIAS).\n5. Climb Speed -- 70-80 KIAS.\n6. Wing Flaps -- RETRACT.\n"
             "Takeoff: Normal Climb Out ........................ . 75-85 KIAS",
          8: "PRECAUTIONARY LANDING WITH ENGINE POWER\n1. Passenger Seat Backs -- MOST UPRIGHT POSITION.\n2. Seats and Seat Belts "
             "-- SECURE.\n3. Airspeed -- 65 KIAS.\n4. Wing Flaps -- 20°.\n7. Wing Flaps -- 30° (on final approach).\n8. Airspeed -- 65 KIAS."}
    lq = lambda q, hint=7: validate.locate_quote(q, PG, hint)
    check("quote parts: an ellipsis splits, dot leaders don't",
          validate.quote_parts("NORMAL TAKEOFF ... 5. Climb Speed -- 70-80 KIAS.") == ["NORMAL TAKEOFF", "5. Climb Speed -- 70-80 KIAS."]
          and len(validate.quote_parts("Takeoff: Normal Climb Out ........................ . 75-85 KIAS")) == 1
          and len(validate.quote_parts("Normal Approach, Flaps Up . . . . . . . 65-75 KIAS")) == 1
          and validate.quote_parts("Best glide speed… should be established") == ["Best glide speed", "should be established"])
    check("shortened quote found piece by piece (heading with an OCR gap + a checklist line)",
          lq("NORMAL TAKEOFF ... 4. Elevator Control -- LIFT NOSE WHEEL (at 55 KIAS).")[1] >= validate.QUOTE_MIN
          and lq("NORMAL TAKEOFF ... 5. Climb Speed -- 70-80 KIAS.") == (7, lq("NORMAL TAKEOFF ... 5. Climb Speed -- 70-80 KIAS.")[1])
          and lq("NORMAL TAKEOFF ... 5. Climb Speed -- 70-80 KIAS.")[1] >= validate.QUOTE_MIN,
          (lq("NORMAL TAKEOFF ... 4. Elevator Control -- LIFT NOSE WHEEL (at 55 KIAS)."), lq("NORMAL TAKEOFF ... 5. Climb Speed -- 70-80 KIAS.")))
    check("...also on the next page, with three pieces", lq("PRECAUTIONARY LANDING WITH ENGINE POWER ... 3. Airspeed -- 65 KIAS. ... 8. Airspeed -- 65 KIAS.")
          [0] == 8 and lq("PRECAUTIONARY LANDING WITH ENGINE POWER ... 3. Airspeed -- 65 KIAS. ... 8. Airspeed -- 65 KIAS.")[1] >= validate.QUOTE_MIN)
    check("a piece that isn't in the document still fails the quote (an invented number, an invented sentence)",
          lq("NORMAL TAKEOFF ... 5. Climb Speed -- 90-100 KIAS.")[1] < validate.QUOTE_MIN
          and lq("NORMAL TAKEOFF ... The pilot must never exceed 60 knots during the initial climb.")[1] < validate.QUOTE_MIN
          and lq("Flaps ... Throttle ... Mixture ... Speed")[1] >= validate.QUOTE_MIN          # every word is there: numbers decide
          and lq("ABORTED LANDING ... GEAR UP ... SPOILERS")[1] < validate.QUOTE_MIN,
          (lq("NORMAL TAKEOFF ... 5. Climb Speed -- 90-100 KIAS."), lq("ABORTED LANDING ... GEAR UP ... SPOILERS")))
    climb = validate.validate_candidate({"kind": "limit", "title": "Normal climb speed", "metric": "ias", "unit": "kt", "min": 70, "max": 80,
        "phases": ["CLIMB_OUT"], "severity": "caution", "tags": [], "page": 7, "source_quote": "NORMAL TAKEOFF ... 5. Climb Speed -- 70-80 KIAS."},
        PH, PG, set())
    check("a rule quoting with '...' is accepted, not 'quote not found'", climb.reject_reason is None and climb.rule["quote_score"] >= validate.QUOTE_MIN,
          (climb.reject_reason, climb.rule.get("quote_score")))
    # ---- a long document: a quote is looked for where it can be, not compared with every page
    import time as _time
    filler = ("The pilot should understand that the airplane flies because of the wing and that power plus attitude "
              "gives performance in every phase of the flight, which is why each manoeuvre is practised until it is smooth. ")
    BIG = {n: f"Chapter text of page {n}. " + filler * 45 for n in range(1, 401)}
    BIG[210] += " Prior to engine start, the pilot needs to ensure that the ramp area surrounding the airplane is clear of persons. " \
                "Other sentences. Finally, the pilot should always call CLEAR out of the side window and wait for a response."
    shortq = ("Prior to engine start, the pilot needs to ensure that the ramp area surrounding the airplane is clear of persons. ... "
              "Finally, the pilot should always call CLEAR out of the side window and wait for a response.")
    t0 = _time.perf_counter()
    got = [validate.locate_quote(shortq, BIG, 210), validate.locate_quote(shortq, BIG, 209), validate.locate_quote(shortq, BIG, 37),
           validate.locate_quote(shortq, BIG, None),
           validate.locate_quote("The pilot must always retract the spoilers before the afterburner is engaged at 300 knots. ... "
                                 "Otherwise the gear doors of the airplane may be damaged by the heat.", BIG, 37),
           validate.locate_quote("Finally, the pilot should always call CLEAR out of the side window and wait for a response.", BIG, 12)]
    for _ in range(20):
        validate.locate_quote(shortq, BIG, 37)
    took = _time.perf_counter() - t0
    check("400-page document: shortened quote found on its page, from the next page, from a wrong page and without a page",
          all(g[0] == 210 and g[1] >= validate.QUOTE_MIN for g in got[:4]) and got[5][0] == 210 and got[5][1] >= validate.QUOTE_EXACT, got)
    check("...an invented quote is still not found, with a best match to show", got[4][1] < validate.QUOTE_MIN and got[4][0] is not None, got[4])
    check(f"...and 26 look-ups in a 400-page document take well under 5 s (took {took:.2f} s)", took < 5.0, took)
    # ---- same numbers, different kind of lesson: not duplicates
    from .pipeline import lesson_types_for
    TW = {7: PG[7], 9: "EMERGENCY DESCENT THROUGH CLOUDS\nAdjust the elevator trim for a stabilized descent at 70-80 KIAS."}
    two = [validate.validate_candidate({"kind": "limit", "title": t, "metric": "ias", "unit": "kt", "min": 70, "max": 80, "phases": ph_,
           "severity": "caution", "tags": [], "page": pg_, "source_quote": q}, PH, TW, set())
           for t, ph_, pg_, q in (("Emergency descent through clouds: airspeed", [], 9, "Adjust the elevator trim for a stabilized descent at 70-80 KIAS."),
                                  ("Normal climb speed", ["CLIMB_OUT"], 7, "5. Climb Speed -- 70-80 KIAS."))]
    ctx = lambda r: tuple(lesson_types_for(r, None, r.get("source_page") == 9, False))
    check("dedupe: 'climb 70-80' is not merged into 'emergency descent 70-80'", len(validate.dedupe(two, context=ctx)) == 2
          and len(validate.dedupe(two)) == 1, len(validate.dedupe(two, context=ctx)))
    # ---- "observed for at least 1 second" is not "never longer than 1 s"
    GP = {3: "Each movement should not exceed 10 degrees, and each area should be observed for at least 1 second to enable detection. "
             "No more than 4 to 5 seconds should be spent on the instrument panel. Check the airspeed at least every 5 seconds on final."}
    def gz(measure, value, quote, aoi="outside", unit="s"):
        c = validate.validate_candidate({"kind": "gaze", "title": "g", "aoi": aoi, "gaze_measure": measure, "value": value, "unit": unit,
            "phases": [], "severity": "advisory", "tags": ["scan"], "page": 3, "source_quote": quote}, PH, GP, set())
        return [i for i in c.issues if i.startswith(validate.WRONG_WAY)]
    check("scan rule reversed: 'at least 1 second' drafted as a maximum look of 1 s",
          len(gz("max_dwell", 1, "each area should be observed for at least 1 second to enable detection.")) == 1,
          gz("max_dwell", 1, "each area should be observed for at least 1 second to enable detection."))
    check("...but 'no more than 4 to 5 seconds' as a maximum, and 'at least every 5 seconds' as a longest gap, are right",
          not gz("max_dwell", 5, "No more than 4 to 5 seconds should be spent on the instrument panel.", aoi="instruments")
          and not gz("max_gap", 5, "Check the airspeed at least every 5 seconds on final.", aoi="airspeed"))
    rev = [{**validate.validate_candidate({"kind": "gaze", "title": "Scan 1 s", "aoi": "outside", "gaze_measure": "max_dwell", "value": 1,
            "unit": "s", "phases": [], "severity": "advisory", "tags": ["scan"], "page": 3,
            "source_quote": "each area should be observed for at least 1 second to enable detection."}, PH, GP, set()).rule,
            "cid": "rev", "origin": "llm", "include": True, "blocked": None, "issues": [], "enabled": True}]
    nr = auto_review(rev, phases=PH, page_map=GP, ocr_pages=set(), nominal=None, handbook="afh")
    check("run: the reversed scan rule is set aside with the reason", not rev[0]["include"] and "looks reversed" in (rev[0].get("auto_aside") or "")
          and nr.get("numbers") == 1, (rev[0].get("auto_aside"), nr))
    # ---- "flaps 0 to 10 degrees: 110 KIAS" must not apply with the flaps up
    fl = validate.validate_candidate({"kind": "limit", "title": "Max speed flaps 0-10", "metric": "ias", "unit": "kt", "max": 110, "phases": [],
        "severity": "warning", "tags": [], "page": 9, "source_quote": "0° to 10° 110 KIAS",
        "when": [{"metric": "flaps_deg", "op": ">=", "value": 0, "unit": "deg"}, {"metric": "flaps_deg", "op": "<=", "value": 10, "unit": "deg"}]},
        PH, {9: "Flap limitations: 0° to 10° 110 KIAS (partial flap range)"}, set())
    check("always-true 'flaps >= 0' becomes 'flaps > 0', with a note",
          {"metric": "flaps_deg", "op": ">", "value": 0.0} in fl.rule["conditions"] and any("always true" in i for i in fl.issues), fl.rule["conditions"])
    # ---- a suggestion that only repeats a rule already in use
    DP = {4: "The aircraft speed is within +10 /-5 KIAS of the recommended landing speed. On final use the manufacturer's recommended approach speed."}
    def dr(title, quote, **kw):
        c = validate.validate_candidate({"kind": "band", "title": title, "metric": "ias", "unit": "kt", "ref_speed": "approach", "ref_factor": 1,
            "target": None, "phases": ["FINAL"], "severity": "caution", "tags": [], "page": 4, "source_quote": quote, **kw}, PH, DP, set())
        return {**c.rule, "cid": title, "origin": "llm", "include": c.reject_reason is None, "blocked": c.reject_reason, "issues": list(c.issues), "enabled": True}
    dup = [dr("Final speed", "On final use the manufacturer's recommended approach speed."),
           dr("Stabilized approach speed", "The aircraft speed is within +10 /-5 KIAS of the recommended landing speed.", tol_plus=10, tol_minus=5)]
    nd = auto_review(dup, phases=PH, page_map=DP, ocr_pages=set(), nominal=None, handbook="afh")
    check("run: a suggested rule identical to one in use is set aside as a duplicate, not offered", dup[1]["include"] and not dup[0]["include"]
          and "same check as 'Stabilized approach speed'" in (dup[0].get("auto_aside") or "") and dup[0]["suggested"]["ready"] is False
          and nd.get("duplicate") == 1 and not nd.get("suggested"), (dup[0].get("auto_aside"), nd))
    check("the note keeps one FAA line, however often it is applied", sg.note_with(sg.note_with("LLM summary of the passage: x", sg.suggest(
        {**B, "phases": ["FINAL"]})), sg.suggest({**B, "phases": ["FINAL"]})).count(sg.NOTE_PREFIX) == 1)

    # ---- importing a rules file made with Export > JSON
    exp = {"format": "mavis-sop-rules/1", "exported_at": "2026-10-05T06:26:00Z",
           "rule_set": {"name": "FAA AFH", "scope": "general", "handbook": "afh", "aircraft_profile": None,
                        "source_pdf": "afh.pdf", "llm_model": "qwen2.5:14b-instruct", "prompt_version": "H2", "pages_read": "22-24, 30"},
           "rules": [
               {"kind": "gaze", "title": "Look outside", "aoi": "outside", "gaze_measure": "min_share", "value": 0.9, "phases": ["FINAL"],
                "status": "In use", "enabled": True, "set_aside": False, "origin": "llm", "source_page": 66, "tags": ["scan"],
                "source_quote": "Approximately 90 percent of the pilot's attention should be devoted to outside visual references."},
               {"kind": "limit", "title": "Old", "metric": "ias", "max_value": 60, "status": "Set aside", "set_aside": True,
                "set_aside_reason": "A correctly flown circuit would break this rule: in Climb out ...", "origin": "llm"},
               {"kind": "unobservable", "title": "Mine", "status": "Set aside", "set_aside_reason": "Set aside by the reviewer (John)"},
               {"kind": "gaze", "title": "Hand-edited share", "aoi": "instruments", "gaze_measure": "max_share", "value": 10},
               {"kind": "limit", "title": "Off", "metric": "ias", "max_value": 163, "enabled": False, "status": "Switched off"},
               {"title": "no kind"}, "junk"]}
    import json as _json
    data = importer.read_export(_json.dumps(exp).encode())
    f, w = importer.draft_fields(data, "FAA AFH_all-aircraft-afh_DRAFT.json")
    check("import: header -> handbook draft", f["doc_type"] == "handbook" and f["handbook"] == "afh" and f["name"] == "FAA AFH"
          and f["selected_pages"] == [22, 23, 24, 30] and f["llm_model"] == "qwen2.5:14b-instruct" and f["filename"] == "afh.pdf", f)
    got = [importer.rule_from_export(x)[0] for x in data["rules"]]
    check("import: entries without a kind are skipped", [g is None for g in got] == [False] * 5 + [True, True])
    a, b, c, e, off = got[:5]
    check("import: rule in use keeps every field", a["include"] and a["enabled"] and a["aoi"] == "outside" and a["value"] == 0.9
          and a["source_page"] == 66 and a["origin"] == "llm" and a["imported"] and a["tags"] == ["scan"], a)
    check("import: set-aside rule keeps its reason", not b["include"] and b["auto_aside"].startswith("A correctly flown")
          and b["max_value"] == 60, b)
    check("import: a rule the reviewer set aside stays the reviewer's", not c["include"] and "auto_aside" not in c and c["origin"] == "manual", c)
    check("import: a share typed as 10 is read as 10%", abs(e["value"] - 0.1) < 1e-9, e["value"])
    check("import: a switched-off rule stays switched off", off["include"] and off["enabled"] is False)
    lt = importer.rule_from_export({"kind": "band", "title": "Glide", "metric": "ias", "target": 68, "tags": ["emergency"]})[0]
    lt2 = importer.rule_from_export({"kind": "band", "title": "App", "metric": "ias", "target": 0, "tol_hi": 10, "tol_lo": 5,
                                     "ref_speed": "approach", "ref_factor": 1, "lesson_types": ["normal", "instrument", "nonsense"]})[0]
    check("import: lesson types and aircraft-speed reference kept; older files go by the emergency tag",
          lt["lesson_types"] == ["emergency"] and a["lesson_types"] == ["normal"] and lt2["lesson_types"] == ["normal", "instrument"]
          and lt2["ref_speed"] == "approach" and lt2["ref_factor"] == 1.0 and lt["ref_speed"] is None, (lt, lt2))
    iss, fatal = validate.assess(dict(a), PH, {66: "nothing like the quote"}, set())
    check("import: an imported rule isn't blocked when its quote isn't in the PDF", fatal is None and "Source text not found in the SOP" in iss, (iss, fatal))
    ac = importer.draft_fields(importer.read_export(_json.dumps({"rule_set": {"aircraft_profile": "no_such_plane", "name": "X"},
                               "rules": [{"kind": "limit"}]}).encode()), "x.json")
    check("import: unknown aircraft profile -> reviewer chooses", ac[0]["doc_type"] == "poh" and ac[0]["aircraft_profile"] is None
          and any("no_such_plane" in m for m in ac[1]), ac)
    for bad, why in ((b"%PDF-1.7", "not valid JSON"), (b'{"hello": 1}', "not a rules export"),
                     (b'{"format": "other/9", "rules": []}', "format")):
        try:
            importer.read_export(bad)
            check(f"import refuses {why}", False)
        except ValueError as ex:
            check(f"import refuses a file that is {why}", why in str(ex), str(ex))
    check("import: are these quotes from this PDF?", importer.quotes_found(
        [{"source_quote": "Rotate at 55 KIAS.", "source_page": 1}, {"source_quote": "Completely different text here."}], PAGES) == 0.5)

    pages = [extract.PageText(i, "para " * 400, "text") for i in (1, 2, 3)]
    ch = extract.make_chunks(pages, 3000)
    check("chunks carry page markers", all(c.text.startswith("[[PAGE") for c in ch) and len(ch) >= 3)

    print("\nall passed" if not _fails else f"\n{_fails} FAILED")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
