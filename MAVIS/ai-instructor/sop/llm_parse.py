"""Stage 2 of the backend pipeline: one text chunk -> candidate rules, via the
local Ollama model MAVIS already runs for coaching (no cloud calls).

Decoding is constrained with Ollama's structured-output `format` (a JSON
schema from schema.llm_json_schema), temperature 0. The model only *drafts*:
every candidate still goes through validate.py and then a human reviewer.
"""
from __future__ import annotations

import json
import logging

import httpx

from .extract import CONTEXT_NOTE
from .schema import AOI_ROLES, BOOKS, CAPTURE_AT, GAZE_MEASURES, METRICS, REF_SPEEDS, TAGS, UNITS

log = logging.getLogger(__name__)


PROMPT_VERSION = "5"              # aircraft POH / SOP prompt (4: emergency procedures kept; 5: go-around is not one,
                                  # ranges stay ranges, don't lose the takeoff and climb speeds)
HANDBOOK_PROMPT_VERSION = "H5"    # training-handbook prompt (H4: aircraft-relative speeds, instrument-flying rules;
                                  # H5: go-around is not an emergency, percentages only from "N percent")


def system_prompt(phases: list[str], aircraft: str | list[str] | None = None) -> str:
    """Prompt v4 (history in docs/sop-rules.md, "Prompt history").
    v4: emergency procedures are extracted again -- emergencies get their own
    lessons -- one rule per checklist plus the speeds it gives as targets,
    all tagged "emergency" (the pipeline gives them the lesson type
    "emergency", so a normal circuit never checks them).
    v2 stated what the rules are FOR (normal VFR circuit training in a sim),
    what to skip, a self-check, rule tags and worked right/wrong examples.
    v3 (after reviewing a full C172S run): emergency procedures produce
    nothing at all; a section "WHAT A NUMBER MEANS" (or LESS = maximum, a
    single speed = band, the speed to reach before retracting flaps is not a
    maximum, manoeuvring speed is not a speed limit, KIAS column of a
    KCAS/KIAS table, no == on flap angle); cruise, engine start and ground
    settings are skipped; "unobservable" is for actions only; every field is
    written (null when unused)."""
    if isinstance(aircraft, list):
        aircraft = ", ".join(aircraft) or None
    ac = aircraft or "a light single-engine training aircraft"
    metric_lines = "\n".join(f"  - {k}: {v[3]}" for k, v in METRICS.items())
    tag_lines = "\n".join(f"  - {k}: {v[1]}" for k, v in TAGS.items())
    return f"""You turn an aircraft's Pilot's Operating Handbook (POH) / Standard Operating Procedure (SOP) into checkable rules
for a FLIGHT-TRAINING SIMULATOR. Aircraft: {ac}.

WHAT THE RULES ARE FOR
Trainees fly VFR traffic-pattern (circuit) lessons: pre-takeoff, takeoff roll, climb-out, crosswind, downwind, base, final,
flare/touchdown, rollout. The simulator reads live telemetry and checks each rule during those phases. A flight instructor
reviews every rule you produce. A WRONG rule is worse than a missing rule: it tells a trainee who flew correctly that they
made a mistake.
EMERGENCIES are flown in separate emergency lessons. Rules from emergency procedures are kept, but each one MUST have
"emergency" as its first tag: that is what keeps it out of normal lessons. Use that tag ONLY for emergency / abnormal
procedures: a go-around (balked landing) is a NORMAL procedure and never gets it.

OUTPUT
JSON object {{"rules": [...]}} only. If this text has nothing to extract, return {{"rules": []}}. That is common and correct.
Write EVERY field of every rule; use null (or [] for lists) for a field the rule does not use.

TELEMETRY THE SIMULATOR CAN MEASURE (the only allowed "metric" values):
{metric_lines}
Anything else (oil pressure/temperature, fuel quantity/flow, weight, wind/crosswind, visibility, electrical, vacuum,
autopilot state, radio/transponder settings, lights, switches) CANNOT be measured.

FLIGHT PHASES (exact names): {", ".join(phases)}
Mapping: "takeoff"/"takeoff roll" -> TAKEOFF_ROLL; "after takeoff"/"initial climb"/"climb out" -> CLIMB_OUT;
"traffic pattern"/"circuit" -> CROSSWIND, DOWNWIND, BASE; "approach" -> BASE, FINAL; "landing" -> FINAL, FLARE_TOUCHDOWN;
"after landing" -> ROLLOUT; "before takeoff"/"run-up" -> PRE_TAKEOFF. Empty phases = applies in every phase.

EXTRACT
1. Aircraft LIMITATIONS that apply in every flight: never-exceed speed, max flap-extended speeds (per flap setting),
   max engine RPM, max bank / prohibited manoeuvres if stated in measurable terms.
2. NORMAL-PROCEDURE targets for the phases above: rotation speed, climb speed, approach speeds (flaps up / flaps down),
   flap settings per phase. Do not lose these among the emergency rules: the takeoff checklist's "lift nose wheel at ..."
   and "Climb Speed -- ..." lines and the speed list at the start of Normal Procedures are the most used rules of all.
3. NORMAL-procedure checklists flown in a circuit whose steps cannot be measured (before takeoff, before landing,
   after landing): ONE "unobservable" rule per checklist, quoting its heading and first steps. Do NOT make one rule
   per step. "unobservable" is only for something the pilot must DO: a fact, a description, a demonstrated value or
   general advice is not a rule.
4. EMERGENCY PROCEDURES (the "Emergency Procedures" section, and anything headed engine failure, forced landing,
   precautionary landing, ditching, fire, icing, static source, flat tire, electrical / vacuum failure). From EACH
   emergency checklist:
   a. ONE "unobservable" rule for the checklist, quoting its heading and first steps;
   b. each AIRSPEED the checklist tells the pilot to fly ("Airspeed -- 68 KIAS") as a "band" with that target and null
      tolerances, titled with the emergency ("Engine failure in flight: glide speed").
   First tag "emergency" on every one. Phases: where it happens ("during takeoff roll" -> TAKEOFF_ROLL, "immediately after
   takeoff" -> CLIMB_OUT, "emergency landing" / "forced landing" -> FINAL, otherwise [] = any phase).
   The long text that explains the checklists again later in the section ("amplified procedures") gives no new rules.

SKIP (return nothing for these)
- Supplements for optional equipment: autopilot, GPS/navigator, transponder, ELT, audio panel/intercom, cabin power,
  entertainment, avionics operating instructions.
- Performance tables and charts, weight & balance, servicing/maintenance, system descriptions, definitions, notes
  that do not require the pilot to do anything, cold-weather/winterization, spins, aerobatics, demonstrated crosswind.
- Engine starting, taxi and ground-run settings ("Throttle -- 1000 RPM or LESS" while starting, "800 to 1000 RPM
  recommended" on the ground) and CRUISE settings ("Power -- 2100-2700 RPM"): a circuit has no cruise.
- Short-field and soft-field takeoff and landing technique (their speeds are lower than the normal ones).
- Limits on quantities the simulator cannot measure (see list above). NEVER put such a number on another metric
  (oil pressure 20 PSI is NOT rpm >= 20).
- Ground checks that are not limits: the magneto check at 1800 RPM is a test setting, NOT "rpm <= 1800".

CONDITIONAL NUMBERS
- If a number only applies in a situation the simulator cannot see (autopilot engaged, night, turbulence, a specific
  weight, short/soft-field technique, instrument approach), do NOT turn it into a limit or band for normal flight. Skip it.
- A number from an EMERGENCY procedure is never a limit for normal flight either: it is an emergency rule (EXTRACT 4),
  tagged "emergency".
- Go-around (balked landing): the simulator cannot tell it from a normal climb, so its speed and flap setting must not become
  limits or bands. ONE unobservable "Go-around (balked landing) procedure" rule.
- Flap setting and power ARE measurable, so they may be conditions in "when" (e.g. max 85 KIAS when flaps_deg >= 10).
- SELF-CHECK every limit/band/event/capture before you output it: "Would a pilot flying this phase correctly, exactly as
  this POH describes, stay inside this rule for the whole phase?" If not, the rule is scoped wrong: fix the phases/when,
  or leave it out.

WHAT A NUMBER MEANS (decide this before choosing min, max or target)
- "X or LESS", "maximum X", "do not exceed X", "not more than X" -> max X.   "X or MORE", "minimum X", "at least X" -> min X.
- ONE speed or height to fly ("Climb Speed -- 74 KIAS", "approach at 65 KIAS") -> kind "band", target X, tolerances null.
  NEVER a limit with min = max, and never a limit with the number left in "value".
- A RANGE to fly ("65-75 KIAS", "approach at 65 to 75 KIAS", "500 to 800 ft/min") -> kind "limit", min and max. Never a band
  with one end of the range, or its middle, as the target.
- A speed the pilot must REACH before doing something ("retract flaps after reaching 60 KIAS", "safe flap retraction speed
  of 60 KIAS") is NOT a maximum speed. Skip it.
- Manoeuvring speed (VA) and "abrupt use of the controls is prohibited above X" limit CONTROL MOVEMENTS, not airspeed:
  normal flight is faster. Skip. Only VNE is a "never exceed speed"; do not use that title for anything else.
- A table with two speed columns (KCAS and KIAS, e.g. "VNE 160 163"): use the KIAS (indicated) number, the second one.
- Flap angle in "when": use > or >= ("flaps_deg > 0" = any flaps, "flaps_deg >= 20"); use == only for 0 (flaps up).
  The simulator reports 9.9 or 10.2 degrees, so "== 10" is never true.

RULE KINDS
  limit        stay within min and/or max. For SOP RANGES and stated maxima/minima.
  band         hold target +/- tolerance, when the SOP gives one target speed/height; tol_plus/tol_minus ONLY if the
               SOP states them, otherwise null (the instructor sets them).
  event        must be true by the start of a phase: op, value, gate_phase ("flaps 30 before final" -> flaps_deg >= 30, FINAL).
  capture      measured at one moment ({", ".join(CAPTURE_AT)}) with min and/or max ("lift nose wheel at 55 KIAS" ->
               ias, capture_at liftoff, min 55).
  unobservable required action the simulator cannot see. metric null, unit "none", no numbers.

UNITS: copy numbers EXACTLY as written, with their unit ({", ".join(UNITS)}). Do not convert. "flaps 20" -> flaps_deg 20 deg.
Signs: descent is negative vertical speed ("do not exceed 1000 fpm descent" -> vs min -1000).

TAGS: 1 to 3 per rule, most important first:
{tag_lines}

STRICT
- source_quote: the sentence(s) the rule comes from, copied VERBATIM, max ~300 characters. Every number in the rule must
  appear in the quote.
- page: the number from the nearest preceding [[PAGE n]] marker.
- Never invent numbers, rules or tolerances. No duplicates of the same requirement.
- severity: warning = hard limitation / safety; caution = standard procedure target; advisory = technique/recommendation.
- title: short plain English that says what the pilot must do, e.g. "Max speed with flaps 10-30".

EXAMPLES (text -> rules)
Text: "VFE Maximum Flap Extended Speed: 10° Flaps 110 KIAS; 10° to 30° Flaps 85 KIAS. Do not exceed this speed with flaps down."
Rules: {{"kind":"limit","title":"Max speed with flaps 10","metric":"ias","unit":"kt","max":110,"when":[{{"metric":"flaps_deg","op":">","value":0,"unit":"deg"}}],"phases":[],"severity":"warning","tags":["aircraft_limit","flight_telemetry","pilot_controls"],...}}
       {{"kind":"limit","title":"Max speed with flaps 10-30","metric":"ias","unit":"kt","max":85,"when":[{{"metric":"flaps_deg","op":">","value":10,"unit":"deg"}}],"phases":[],"severity":"warning","tags":["aircraft_limit","flight_telemetry","pilot_controls"],...}}
Text: "NORMAL LANDING 1. Airspeed -- 65-75 KIAS (flaps UP). 2. Wing Flaps -- AS DESIRED. 3. Airspeed -- 60-70 KIAS (flaps DOWN)."
Rules: {{"kind":"limit","title":"Approach speed, flaps up","metric":"ias","unit":"kt","min":65,"max":75,"when":[{{"metric":"flaps_deg","op":"==","value":0,"unit":"deg"}}],"phases":["BASE","FINAL"],"severity":"caution","tags":["flight_telemetry"],...}}
       {{"kind":"limit","title":"Approach speed, flaps down","metric":"ias","unit":"kt","min":60,"max":70,"when":[{{"metric":"flaps_deg","op":">","value":0,"unit":"deg"}}],"phases":["FINAL"],"severity":"caution","tags":["flight_telemetry"],...}}
Text: "BEFORE LANDING 1. Pilot and Passenger Seat Backs -- MOST UPRIGHT POSITION. 2. Seats and Seat Belts -- SECURE. 3. Fuel Selector Valve -- BOTH."
Rules: {{"kind":"unobservable","title":"Before landing checklist","metric":null,"unit":"none","phases":["DOWNWIND"],"severity":"caution","tags":["procedure","configuration"],...}}
Text (example wording): "Final approach speed with 30° flaps -- 65 KIAS."
Rules: {{"kind":"band","title":"Final approach speed, flaps 30","metric":"ias","unit":"kt","target":65,"tol_plus":null,"tol_minus":null,"min":null,"max":null,"value":null,"when":[{{"metric":"flaps_deg","op":">=","value":30,"unit":"deg"}}],"phases":["FINAL"],"severity":"caution","tags":["flight_telemetry"],...}}
Text: "ENGINE FAILURE DURING FLIGHT (Restart Procedures) 1. Airspeed -- 68 KIAS. 2. Fuel Shutoff Valve -- PUSH IN."
Rules: {{"kind":"unobservable","title":"Engine failure in flight checklist","metric":null,"unit":"none","phases":[],"severity":"warning","tags":["emergency","procedure"],...}}
       {{"kind":"band","title":"Engine failure in flight: glide speed","metric":"ias","unit":"kt","target":68,"tol_plus":null,"tol_minus":null,"min":null,"max":null,"when":[],"phases":[],"severity":"warning","tags":["emergency","flight_telemetry"],...}}
Text: "BALKED LANDING 1. Throttle -- FULL OPEN. 2. Wing Flaps -- RETRACT TO 20°. 3. Climb Speed -- 60 KIAS."
Rules: {{"kind":"unobservable","title":"Go-around (balked landing) procedure","metric":null,"unit":"none","phases":["FINAL","FLARE_TOUCHDOWN"],"severity":"caution","tags":["procedure"],...}}
       (The simulator cannot tell a go-around from a normal climb, so its speed and flap numbers do NOT become limits or bands.)
("..." stands for the remaining fields, which you always write out.)

WRONG (never do this)
- "Oil Pressure, Minimum: 20 PSI" -> rpm min 20.                 Oil pressure is not measurable: skip it.
- "ENGINE FAILURE DURING FLIGHT 1. Airspeed -- 68 KIAS" -> ias max 68, tags ["flight_telemetry"].   An emergency speed
  without the "emergency" tag becomes a limit on normal flight (normal climb is faster). It is a band, target 68, first
  tag "emergency".
- One rule for every step of an emergency checklist.   One "unobservable" rule per checklist, plus its airspeeds.
- "Abrupt use of the controls is prohibited above 105 KIAS" -> "Never-exceed speed", ias max 105.   That limits control
  movements, not speed; a normal circuit is flown faster. Skip.
- "Throttle -- 1000 RPM or LESS" -> rpm MIN 1000 during TAKEOFF_ROLL.   "or LESS" is a maximum, and it is an engine-start
  setting, not a takeoff rule. Skip.
- "a safe flap retraction speed of 60 KIAS is reached" -> "Max speed with flaps 10", ias max 60.   It is the speed to
  reach BEFORE retracting flaps, not a maximum. Skip.
- "Climb Speed -- 60 KIAS" -> limit with min 60 and max 60, or limit with value 60.   One speed = band, target 60.
- "CRUISE Power -- 2100-2700 RPM" -> rpm limit during CLIMB_OUT.   Cruise setting: skip.
- "Operation in direct crosswinds of 15 knots has been demonstrated" -> unobservable rule.   A fact, not an action. Skip.
- "Autopilot minimum approach speed -- 80 KIAS" -> ias min 80 at all times.   Autopilot-only; every normal landing breaks it. Skip.
- "The magneto check should be made at 1800 RPM" -> rpm max 1800 at all times.   A ground test setting, not a limit.
- "fly not less than 2000 feet above noise sensitive areas" -> alt_agl 2000 at all times.   Enroute advice; the circuit is lower. Skip.
- "Transponder Mode Selector Knob -- ALT." -> unobservable rule.   Optional-equipment operation. Skip."""


# What to look for in each training handbook (the reviewer picks the book at upload).
HANDBOOK_FOCUS = {
    "afh": """This is the FAA Airplane Flying Handbook. Its chapters on basic flight maneuvers, takeoffs and departure climbs,
airport traffic patterns, and approaches and landings describe how to fly a circuit. Look for: traffic pattern altitude and
the height at which to turn, bank angle for turns in the pattern, when to start the descent, stabilized approach criteria
(descent rate on final), and how the pilot divides attention between outside references and the instruments (integrated
flight instruction: "about 90 percent outside, no more than 10 percent inside") -> gaze rules.
Most of the landing chapter is about TECHNIQUE and about FAULTS: crosswind, short-field, soft-field and power-off approaches,
"Common Errors" lists, and "faulty approaches and landings" (low/high/late roundout, floating, ballooning, bouncing,
porpoising, wheelbarrowing, hard landing, ground loop). None of that is a rule: return nothing for it.""",
    "ifh": """This is the FAA Instrument Flying Handbook: how to fly by reference to the instruments. An eye tracker checks
the pilot's instrument scan, so the scan guidance in this book IS wanted. Its rules are kept for instrument-flying lessons;
you do not have to make them fit a visual circuit. Extract, as gaze rules:
1. The cross-check (scan) TECHNIQUE when the book puts a number on it: "eyes remain on the attitude indicator 80-90 percent
   of the time" -> aoi attitude, min_share, value 80, unit percent.
2. The three common cross-check ERRORS, ONE rule each for the whole section: FIXATION (staring at one instrument) ->
   aoi instruments, max_dwell; OMISSION (leaving an instrument out of the scan) -> aoi instruments, max_gap; EMPHASIS
   (relying on one instrument instead of cross-checking) -> aoi instruments, max_share.
3. The PRIMARY instruments of each basic manoeuvre, where the book says so in a sentence like "the primary instrument for
   pitch is the altimeter": the pilot must keep that instrument in the scan -> aoi = that instrument, max_gap, value null.
   One rule per (manoeuvre, primary instrument); phases: straight-and-level -> DOWNWIND; climbs -> CLIMB_OUT;
   turns -> CROSSWIND, BASE; descents -> BASE, FINAL. Title: "Level flight: keep the altimeter in the scan".
   NOT for "supporting" instruments, and NOT from a sentence that only says what an instrument shows or how it works.
Nothing to extract: navigation systems, IFR procedures, approaches, holds, ATC, partial-panel, unusual attitudes,
helicopter chapters, and trim / control technique.""",
    "phak": """This is the FAA Pilot's Handbook of Aeronautical Knowledge. Its Airport Operations chapter describes traffic
pattern procedures (pattern altitude, entering and leaving the pattern, turns). Its Aeromedical Factors chapter describes the
visual scan for collision avoidance (scanning the sky in sectors, time outside versus inside) -> gaze rules on "outside".
Nearly everything else (aerodynamics, systems, instruments theory, weather, navigation, regulations, physiology) contains
nothing to extract.""",
    "school": """This is a flight school's own handbook / standard operating procedures. Look for the school's circuit procedure
(heights, where to turn, bank limits, descent rates), stabilized approach and go-around criteria, and scan / lookout rules.
Where the school gives a number that differs from FAA guidance, the school's number is the rule.""",
    "other": """This is a pilot training handbook. Look for circuit procedure stated in measurable terms, stabilized approach and
go-around criteria, and scan / lookout guidance.""",
}


def handbook_prompt(phases: list[str], book: str | None = None) -> str:
    """Prompt H3 for training handbooks (FAA or a school's own). Same output
    schema as the POH prompt plus the "gaze" kind and a "note". Rules from a
    handbook apply to EVERY aircraft, so anything aircraft-specific (V-speeds,
    flap and power settings) is left to the aircraft's POH.

    H2 (after the first Instrument Flying Handbook run, where every rule rested
    on a single sentence): tells the model that a rule rests on a PASSAGE --
    read the surrounding paragraphs to find which instrument, which phase and
    any number -- asks for the whole supporting passage as the quote, and for a
    short note saying what the book tells the instructor about the threshold.

    H4: speeds that depend on the aircraft are no longer skipped -- the rule
    names the aircraft speed (ref_speed, ref_factor) and gives its numbers as
    differences from it; emergency procedures are kept, tagged "emergency";
    the Instrument Flying Handbook focus asks for the scan technique, the
    scan errors and the primary instruments of each manoeuvre.

    H3 (after reviewing full AFH and IFH runs): every field is written; a gaze
    rule always names its aoi and gaze_measure; a number in the quoted passage
    must be carried by the rule and percent shares are copied as percent; a
    list "NOT A RULE" (explanations of what an instrument shows, primary /
    supporting instrument statements, control technique, "Common Errors"
    lists, fault recovery such as ballooning and bouncing); "unobservable" is
    rare; nothing from the prompt's own examples may be quoted."""
    book = book if book in BOOKS else "other"
    label = BOOKS[book][0]
    metric_lines = "\n".join(f"  - {k}: {v[3]}" for k, v in METRICS.items())
    aoi_lines = "\n".join(f"  - {k}: {v[1]}" for k, v in AOI_ROLES.items())
    ref_lines = "\n".join(f"  - {k}: {v[3]}" for k, v in REF_SPEEDS.items())
    measure_lines = "\n".join(f"  - {k}: {v[2]}" for k, v in GAZE_MEASURES.items())
    tag_lines = "\n".join(f"  - {k}: {v[1]}" for k, v in TAGS.items())
    return f"""You turn pages of a pilot TRAINING HANDBOOK ({label}) into checkable rules for a FLIGHT-TRAINING SIMULATOR.
Rules from this book apply to EVERY aircraft the school flies, so a rule must never depend on one aircraft type.

WHAT THE RULES ARE FOR
Trainees fly VFR traffic-pattern (circuit) lessons in light single-engine aeroplanes: pre-takeoff, takeoff roll, climb-out,
crosswind, downwind, base, final, flare/touchdown, rollout. The simulator reads live telemetry and an eye tracker shows where
the pilot is looking. A flight instructor reviews every rule you produce. A WRONG rule is worse than a missing rule.
A handbook is a TEXTBOOK: most paragraphs explain, define or describe and contain NO rule. For such text return
{{"rules": []}}. That is the normal, correct result for most pages.

FOCUS FOR THIS BOOK
{HANDBOOK_FOCUS[book]}

OUTPUT
JSON object {{"rules": [...]}} only. Write EVERY field of every rule; use null (or [] for lists) for a field the rule does
not use. The example sentences in this prompt are NOT part of the book: never quote them.

READ THE WHOLE PASSAGE, NOT ONE SENTENCE
A requirement in a handbook is explained over a paragraph or several: one sentence states it, the sentences around it say
WHICH instrument or area is meant, in WHICH situation or phase of flight, WHY, and often HOW OFTEN or HOW LONG. Before you
write a rule, read the paragraph the requirement is in and the paragraphs just before and after it, and use them to choose
the aoi, the gaze_measure, the phases and the value. A number counts if it is anywhere in that passage, not only in the
sentence that states the requirement. One passage = one rule: do not make a separate rule from each of its sentences.
Text under a line "{CONTEXT_NOTE}" is the end of the part you were given before: use it to understand how the
current passage started, but do not create rules from requirements stated only there.

TELEMETRY THE SIMULATOR CAN MEASURE (the only allowed "metric" values):
{metric_lines}
Anything else (wind, visibility, distances, radio, lights, checklists, traffic) CANNOT be measured.

WHERE THE PILOT LOOKS (kind "gaze"). "aoi" is the area of the cockpit:
{aoi_lines}
"gaze_measure" is what is checked:
{measure_lines}
A gaze rule ALWAYS has an "aoi" and a "gaze_measure" from these lists. If the passage does not say WHERE to look (which
instrument, the panel as a whole, or outside), it is not a gaze rule. Use min_share / max_share only when the book gives a
percentage or says "most of the time"; "check ... frequently / periodically / often" -> max_gap; "do not stare / fixate" ->
max_dwell.
Seconds use unit "s"; shares use unit "percent" with the number as printed ("90 percent" -> value 90, unit "percent").
A share must be printed in the passage AS a percentage of time or attention; a number that means something else ("1/10 the
span", "25 percent less drag") is not a threshold.
If the passage states the number, the rule MUST carry it. If the book describes the behaviour WITHOUT a number ("avoid
fixation", "check the airspeed frequently"), still create the gaze rule with value null and unit "none": the instructor
sets the threshold. NEVER invent a number: no 50, no 5, no "reasonable default".

FLIGHT PHASES (exact names): {", ".join(phases)}
Mapping: "takeoff" -> TAKEOFF_ROLL; "after takeoff"/"departure climb" -> CLIMB_OUT; "crosswind leg" -> CROSSWIND;
"downwind leg" -> DOWNWIND; "base leg" -> BASE; "final approach" -> FINAL; "roundout"/"flare"/"touchdown" -> FLARE_TOUCHDOWN;
"after landing"/"landing roll" -> ROLLOUT; "traffic pattern" -> CROSSWIND, DOWNWIND, BASE, FINAL. Empty phases = every phase.

EXTRACT
1. Circuit procedure stated in measurable terms that holds for any light aeroplane: pattern altitude, height to reach before
   turning, bank angle for pattern turns, descent rate on approach, stabilized approach criteria (by what height the
   approach must be stable, descent rate limits), go-around criteria.
2. Scan and lookout guidance -> gaze rules (see above).
3. "unobservable": RARE. Only an action the trainee must perform on EVERY normal circuit that the simulator cannot measure
   (clear the area / look for traffic before a turn, use the checklist, go around when the approach is not stabilized).
   Most parts of the book give none. Never one per sentence.
Values a handbook calls typical ("usually 1,000 feet", "generally 500-1000 fpm") ARE rules for the baseline: copy them,
severity caution. When the passage you quote contains the number that states the requirement, the rule MUST carry that
number in target / min / max / value - a band or limit without its number is useless.

AIRCRAFT-RELATIVE SPEEDS (ref_speed, ref_factor)
A handbook cannot give a speed in knots that is right for every aircraft, so it names the aircraft's own speeds. Do NOT
skip these and do NOT invent a number in knots. Set "ref_speed" to the speed the book names:
{ref_lines}
and "ref_factor" to the multiplier (1 unless the book says e.g. "1.3 VSO"). metric "ias", unit "kt", and target / min /
max are then the DIFFERENCE from that speed in knots (0 = exactly that speed):
  "within +10/-5 knots of the recommended landing speed" -> band, ref_speed "approach", ref_factor 1, target 0, tol_plus 10, tol_minus 5
  "an approach speed of not more than 1.3 VSO"           -> limit, ref_speed "vs0", ref_factor 1.3, max 0, min null
  "climb at VY"                                          -> band, ref_speed "vy", ref_factor 1, target 0, tolerances null
For every rule that does not name an aircraft speed, ref_speed and ref_factor are null.

EMERGENCIES
Emergency and abnormal procedures (engine failure, forced landing, fire...) are flown in separate emergency lessons.
Extract what the pilot must do or hold in them like any other rule, with "emergency" as the FIRST tag. Use that tag ONLY
for those: a go-around, a crosswind landing or a stabilized-approach rule is NORMAL flying and never gets it.

NOT A RULE (return nothing)
- What an instrument shows, is used for, or is "primary" / "supporting" for.
- HOW to move the controls: rudder, aileron, elevator, trim, brake and power technique ("use rudder to keep straight",
  "apply brakes carefully", "hold the pitch attitude").
- Lists of "Common Errors", and what to do after a FAULT (ballooning, bouncing, porpoising, floating, wheelbarrowing, high
  or low roundout, drift, hard landing): advice for when something has already gone wrong.
- What the instructor should teach, how learners typically behave, why something matters.
- The same requirement stated again: one rule, from the passage that states it best.

SKIP (return nothing for these)
- Definitions, explanations, theory, history, physiology, aerodynamics, systems, weather, regulations text.
- Flap settings and power or RPM settings: they depend on the aircraft type and come from its own POH. (SPEEDS that
  depend on the aircraft are NOT skipped: see AIRCRAFT-RELATIVE SPEEDS.) Never create a rule with its number missing.
- Maneuvers that are not part of a circuit lesson: steep turns, stalls, slow flight, spins, chandelles, lazy eights,
  eights-on-pylons and other ground reference maneuvers, night flying, short-field and
  soft-field technique, crosswind technique, power-off approaches, multi-engine, tailwheel, turboprop, jet, seaplane,
  helicopter.
- Instrument (IFR) procedures: departures, holds, approaches, minimums, navigation. (Scan technique for attitude instrument
  flying is NOT skipped: it becomes gaze rules.)

RULE KINDS
  limit        stay within min and/or max ("do not exceed 30 degrees of bank in the pattern" -> bank max 30, min -30;
               bank is signed: left negative, right positive). A RANGE is a limit: "500-1000 fpm descent" -> vs min -1000,
               max -500.
  band         hold ONE target +/- tolerance. tol_plus/tol_minus ONLY if the book states them, otherwise null (instructor
               sets them). The target is never null.
  event        must be true by the start of a phase: op, value, gate_phase.
  capture      measured at one moment ({", ".join(CAPTURE_AT)}) with min and/or max.
  gaze         where the pilot looks: aoi, gaze_measure, value (or null), unit. metric null.
  unobservable required action the simulator cannot see. metric null, unit "none", no numbers.

UNITS: copy numbers EXACTLY as written, with their unit ({", ".join(UNITS)}). Do not convert.
Signs: descent is negative vertical speed ("descent rate no more than 1,000 feet per minute" -> vs min -1000).

TAGS: 1 to 3 per rule, most important first:
{tag_lines}

STRICT
- source_quote: the passage the rule rests on, copied VERBATIM: the sentence that states the requirement plus the
  neighbouring sentences that explain it or carry its number. Usually 2-4 consecutive sentences, up to ~700 characters.
  Consecutive means exactly as printed, nothing skipped. Every number in the rule must appear in the quote.
- note: one or two plain sentences for the instructor who will review the rule: what the surrounding text says that helps
  to set or check it (which instruments it names, how often or how long, in which situation, what goes wrong otherwise).
  Only what the text says. No numbers of your own.
- page: the number from the nearest preceding [[PAGE n]] marker (the page where the quote starts).
- Never invent numbers, rules or tolerances. No duplicates of the same requirement.
- severity: warning = safety limit; caution = standard procedure; advisory = technique / good practice (most gaze rules).
- title: short plain English, e.g. "Pattern altitude 1,000 ft above the airport".

EXAMPLES (text -> rules)
Text: "The traffic pattern altitude is usually 1,000 feet above the elevation of the airport surface."
Rules: {{"kind":"band","title":"Pattern altitude 1,000 ft above the airport","metric":"alt_agl","unit":"ft","target":1000,"tol_plus":null,"tol_minus":null,"min":null,"max":null,"op":null,"value":null,"gate_phase":null,"capture_at":null,"when":[],"aoi":null,"gaze_measure":null,"ref_speed":null,"ref_factor":null,"phases":["DOWNWIND"],"severity":"caution","tags":["flight_telemetry"],"source_quote":"The traffic pattern altitude is usually 1,000 feet above the elevation of the airport surface.","note":"The book calls 1,000 feet the usual pattern altitude; it gives no tolerance.","page":1}}
       (In the other examples "..." stands for the remaining fields, which you always write out.)
Text (example wording): "About 90 percent of the pilot's attention should be on outside visual references, and no more than
about 10 percent inside the flight deck."
Rules: {{"kind":"gaze","title":"Look outside about 90% of the time","aoi":"outside","gaze_measure":"min_share","value":90,"unit":"percent","metric":null,"phases":["CROSSWIND","DOWNWIND","BASE","FINAL"],"severity":"caution","tags":["scan","situational"],...}}
       {{"kind":"gaze","title":"No more than about 10% of the time on the instruments","aoi":"instruments","gaze_measure":"max_share","value":10,"unit":"percent","metric":null,"phases":["CROSSWIND","DOWNWIND","BASE","FINAL"],"severity":"caution","tags":["scan"],...}}
Text (example wording): "On final the airspeed should be within +10/-5 knots of the recommended landing speed."
Rules: {{"kind":"band","title":"Final: airspeed within +10/-5 kt of the approach speed","metric":"ias","unit":"kt","ref_speed":"approach","ref_factor":1,"target":0,"tol_plus":10,"tol_minus":5,"min":null,"max":null,"phases":["FINAL"],"severity":"caution","tags":["flight_telemetry"],...}}
Text (example wording): "A descent rate of generally 500-1000 fpm makes for a safe approach."
Rules: {{"kind":"limit","title":"Descent rate 500-1000 fpm on final","metric":"vs","unit":"fpm","min":-1000,"max":-500,"target":null,"phases":["FINAL"],"severity":"caution","tags":["flight_telemetry"],...}}
Text (example wording): "Fixation, or staring at a single instrument, usually occurs for a reason. A pilot may stare at the
altimeter reading 200 feet below the assigned altitude, wondering how the needle got there. While fixated on the instrument,
the heading begins to drift unnoticed."
Rules: {{"kind":"gaze","title":"Don't fixate on the altimeter","aoi":"altimeter","gaze_measure":"max_dwell","value":null,"unit":"none","metric":null,"phases":[],"severity":"advisory","tags":["scan"],"source_quote":"Fixation, or staring at a single instrument, usually occurs for a reason. A pilot may stare at the altimeter reading 200 feet below the assigned altitude, wondering how the needle got there. While fixated on the instrument, the heading begins to drift unnoticed.","note":"The book names the altimeter as the typical instrument pilots stare at, and says heading drifts unnoticed meanwhile. It gives no time limit.",...}}
       (ONE rule from the three sentences. The 200 feet is part of the story, not a requirement: no altitude rule.)
Text (example wording): "On final approach, the airspeed must be checked frequently."
Rules: {{"kind":"gaze","title":"Check airspeed often on final","aoi":"airspeed","gaze_measure":"max_gap","value":null,"unit":"none","metric":null,"phases":["FINAL"],"severity":"advisory","tags":["scan","flight_telemetry"],...}}
Text (example wording): "Before beginning any turn, look in the direction of the turn and clear the area of traffic."
Rules: {{"kind":"unobservable","title":"Clear the area before turning","metric":null,"unit":"none","phases":["CROSSWIND","DOWNWIND","BASE","FINAL"],"severity":"caution","tags":["situational"],...}}

WRONG (never do this)
- Three rules from three sentences of the same paragraph.   One passage = one rule, with the whole passage as the quote.
- A one-sentence quote when the next sentence says which instrument or how often.   Quote the passage.
- A definition such as "a medium bank is between about 20 and 45 degrees" -> bank limit.   A definition is not a requirement.
- "fly final at 1.3 VSO" -> ias band with target 52, or with target null.   Never a number of your own: ref_speed "vs0",
  ref_factor 1.3, target 0.
- "Maintain altitude within 100 feet during the steep turn" -> alt band.   Steep turns are not flown in a circuit: skip.
- "check the airspeed frequently" -> gaze max_gap value 5.   The book gives no number: value must be null.
- "the primary instrument for pitch is the altimeter" -> gaze altimeter min_share 50.   An explanation, and 50 is invented:
  no rule.
- A gaze rule with aoi null or gaze_measure null.   Not allowed: name both, or do not create the rule.
- "Approximately 90 percent of the pilot's attention should be devoted to outside" -> gaze rule with value null.   The
  number is in the text: value 90, unit "percent".
- "The traffic pattern altitude is usually 1,000 feet" -> band with target null.   The number is in the text: target 1000.
- "When ballooning is excessive, it is best to execute a go-around" -> unobservable rule.   Fault recovery: no rule.
- "The rudder serves the same purpose on the ground as it does in the air" -> unobservable rule.   Explanation: no rule.
- "The most common errors include: failure to clear the area ..." -> rules.   A "Common Errors" list: no rule.
- "airspeed within +10/-5 KIAS of the recommended landing speed" -> ias band with target null and ref_speed null.
  It names an aircraft speed: ref_speed "approach", target 0, tol_plus 10, tol_minus 5.
- "Descend at 500 feet per minute on the ILS" -> vs rule.   Instrument approach: skip."""


# The prompts' own example sentences. A weak model sometimes returns one as the
# "quote" for a page that says nothing of the kind; such a candidate is dropped
# unless the document really contains the sentence (pipeline._run).
EXAMPLE_QUOTES = (
    "VFE Maximum Flap Extended Speed: 10° Flaps 110 KIAS; 10° to 30° Flaps 85 KIAS.",
    "NORMAL LANDING 1. Airspeed -- 65-75 KIAS (flaps UP). 2. Wing Flaps -- AS DESIRED.",
    "Before beginning any turn, look in the direction of the turn and clear the area of traffic.",
    "On final approach, the airspeed must be checked frequently.",
    "Fixation, or staring at a single instrument, usually occurs for a reason.",
    "About 90 percent of the pilot's attention should be on outside visual references",
    "A descent rate of generally 500-1000 fpm makes for a safe approach.",
    "On final the airspeed should be within +10/-5 knots of the recommended landing speed.",
    "Final approach speed with 30° flaps -- 65 KIAS.",
)


def is_example_quote(quote: str | None) -> bool:
    q = " ".join((quote or "").lower().split())
    return bool(q) and any(q.startswith(" ".join(e.lower().split())[:60]) for e in EXAMPLE_QUOTES)


NUM_PREDICT = 3072                # answer limit (tokens) for a POH section
HANDBOOK_NUM_PREDICT = 4096       # handbook rules carry a longer quote and a note


class OllamaParser:
    def __init__(self, url: str, model: str, timeout_s: float = 300.0, num_ctx: int = 8192,
                 think: bool | None = False):
        self.url = url.rstrip("/")
        # Reasoning models (Qwen3.x, Gemma 4, DeepSeek-R1...) must have thinking
        # OFF for structured output, or the JSON can land in the thinking channel.
        # None = don't send the flag at all (models/Ollama builds that reject it).
        self.think = think
        self.model = model
        self.timeout = timeout_s
        self.num_ctx = num_ctx
        self._schema_supported = True

    def for_model(self, model: str | None) -> "OllamaParser":
        """The same Ollama with another installed model (chosen per run on the page)."""
        if not model or model == self.model:
            return self
        return OllamaParser(self.url, model, self.timeout, self.num_ctx, self.think)

    async def installed(self) -> list[str] | None:
        """Names of the models installed in Ollama (None: Ollama isn't reachable).
        Embedding-only models can't draft rules and are left out."""
        try:
            async with httpx.AsyncClient(timeout=3.0, trust_env=False) as cli:
                r = await cli.get(f"{self.url}/api/tags")
                if r.status_code != 200:
                    return None
                names = [str(m.get("name")) for m in r.json().get("models", []) if m.get("name")]
                return sorted(n for n in names if "embed" not in n.lower())
        except Exception:
            return None

    async def available(self) -> bool:
        names = await self.installed()
        return bool(names) and (self.model in names or f"{self.model}:latest" in names)

    # ---- GPU memory. A 24 GB card holds ONE large model. When rules are drafted with a
    # different model than the coach uses, the other one has to leave the GPU first, or
    # Ollama runs part of the new model on the CPU and a section takes minutes.
    async def loaded(self) -> list[dict]:
        """Models Ollama has in memory now: [{"name", "size", "size_vram"}] ([] if unknown)."""
        try:
            async with httpx.AsyncClient(timeout=3.0, trust_env=False) as cli:
                r = await cli.get(f"{self.url}/api/ps")
                return [m for m in r.json().get("models", []) if m.get("name")] if r.status_code == 200 else []
        except Exception:
            return []

    async def unload(self, name: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=10.0, trust_env=False) as cli:
                await cli.post(f"{self.url}/api/generate", json={"model": name, "keep_alive": 0})
        except Exception as e:
            log.info("could not unload %s: %s", name, e)

    async def make_room(self) -> list[str]:
        """Unload every other model so this one fits on the GPU. -> names unloaded."""
        gone = []
        for m in await self.loaded():
            if m["name"] not in (self.model, f"{self.model}:latest"):
                await self.unload(m["name"])
                gone.append(m["name"])
        return gone

    async def gpu_share(self) -> float | None:
        """How much of this model sits on the GPU (1.0 = all of it; None = not loaded / unknown)."""
        for m in await self.loaded():
            if m["name"] in (self.model, f"{self.model}:latest") and m.get("size"):
                return min(1.0, float(m.get("size_vram") or 0) / float(m["size"]))
        return None

    async def _chat(self, system: str, user: str, fmt, num_ctx: int | None = None,
                    num_predict: int | None = None) -> str:
        body = {
            "model": self.model, "stream": False, "format": fmt,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "options": {"temperature": 0, "num_ctx": num_ctx or self.num_ctx,
                        "num_predict": num_predict or NUM_PREDICT},
        }
        if self.think is not None:
            body["think"] = self.think
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as cli:
            r = await cli.post(f"{self.url}/api/chat", json=body)
            if r.status_code == 400 and "think" in body and "think" in r.text.lower():
                # this model/Ollama doesn't know the flag -- stop sending it
                self.think = None
                body.pop("think")
                r = await cli.post(f"{self.url}/api/chat", json=body)
            r.raise_for_status()
            return r.json()["message"]["content"]

    async def parse_chunk(self, chunk_text: str, system: str, schema: dict, num_ctx: int | None = None,
                          num_predict: int | None = None) -> list[dict]:
        """num_ctx / num_predict: this run's context window and answer limit
        (training handbooks read more text per call than a POH)."""
        user = f"SOP TEXT:\n<<<\n{chunk_text}\n>>>\nExtract the rules as JSON."
        fmt = schema if self._schema_supported else "json"
        try:
            out = await self._chat(system, user, fmt, num_ctx, num_predict)
        except httpx.HTTPStatusError as e:
            # Ollama < 0.5 rejects a schema in `format` (400); some builds fail to
            # compile an unusual schema into a grammar (500). Fall back to plain
            # JSON mode once -- validate.py still enforces the vocabulary.
            if self._schema_supported and e.response.status_code in (400, 500):
                log.warning("Ollama rejected schema format, falling back to format=json: %s", e)
                self._schema_supported = False
                out = await self._chat(system, user, "json", num_ctx, num_predict)
            else:
                raise
        return parse_llm_output(out)


def _salvage(out: str) -> list[dict]:
    """An answer cut off at the token limit ('{"rules": [{...}, {...}, {"ki'):
    keep every rule that was written completely instead of losing the section."""
    k = out.find('"rules"')
    lo = out.find("[", k if k >= 0 else 0)
    if lo < 0:
        return []
    dec, i, rules = json.JSONDecoder(), lo + 1, []
    while True:
        while i < len(out) and out[i] in " \t\r\n,":
            i += 1
        if i >= len(out) or out[i] != "{":
            return rules
        try:
            obj, i = dec.raw_decode(out, i)
        except json.JSONDecodeError:
            return rules
        rules.append(obj)


def parse_llm_output(out: str) -> list[dict]:
    """Tolerant JSON extraction: accepts {"rules": [...]} or a bare list, and
    the complete rules of an answer that was cut off."""
    out = (out or "").strip().strip("`")
    if out.startswith("json"):
        out = out[4:]
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        lo, hi = out.find("{"), out.rfind("}")
        if lo < 0 or hi <= lo:
            return []
        try:
            data = json.loads(out[lo:hi + 1])
        except json.JSONDecodeError:
            saved = _salvage(out)
            if not saved:
                raise
            log.warning("LLM answer was cut off: kept its %d complete rule(s)", len(saved))
            data = saved
    rules = data.get("rules", []) if isinstance(data, dict) else data
    return [r for r in rules if isinstance(r, dict)] if isinstance(rules, list) else []
