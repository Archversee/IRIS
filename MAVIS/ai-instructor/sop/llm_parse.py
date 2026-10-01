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

from .schema import AOI_ROLES, BOOKS, CAPTURE_AT, GAZE_MEASURES, METRICS, TAGS, UNITS

log = logging.getLogger(__name__)


PROMPT_VERSION = "2"              # aircraft POH / SOP prompt
HANDBOOK_PROMPT_VERSION = "H1"    # training-handbook prompt


def system_prompt(phases: list[str], aircraft: str | list[str] | None = None) -> str:
    """Prompt v2. Changes from v1 (see docs/sop-rules.md, "Prompt history"):
    states what the rules are FOR (normal VFR circuit training in a sim), what
    to skip (equipment supplements, emergencies-as-limits, autopilot-only and
    unmeasurable quantities), a self-check ("would a correct circuit break
    this?"), rule tags, and worked right/wrong examples taken from real mistakes."""
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

OUTPUT
JSON object {{"rules": [...]}} only. If this text has nothing to extract, return {{"rules": []}}. That is common and correct.

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
   flap settings per phase, go-around (balked landing) speed and flap setting.
3. NORMAL checklists whose steps cannot be measured: ONE "unobservable" rule per checklist (e.g. "Before landing
   checklist"), quoting its heading or first step. Do NOT make one rule per step.

SKIP (return nothing for these)
- Supplements for optional equipment: autopilot, GPS/navigator, transponder, ELT, audio panel/intercom, cabin power,
  entertainment, avionics operating instructions.
- Performance tables and charts, weight & balance, servicing/maintenance, system descriptions, definitions, notes
  that do not require the pilot to do anything, cold-weather/winterization, ditching, spins, aerobatics.
- Limits on quantities the simulator cannot measure (see list above). NEVER put such a number on another metric
  (oil pressure 20 PSI is NOT rpm >= 20).
- Ground checks that are not limits: the magneto check at 1800 RPM is a test setting, NOT "rpm <= 1800".

CONDITIONAL AND EMERGENCY NUMBERS
- If a number only applies in a situation the simulator cannot see (engine failure, fire, autopilot engaged, icing,
  night, turbulence, a specific weight, short/soft-field technique, instrument approach), do NOT turn it into a limit or band
  for normal flight. Either skip it, or create an "unobservable" rule tagged "emergency" (one per emergency checklist).
- Flap setting and power ARE measurable, so they may be conditions in "when" (e.g. max 85 KIAS when flaps_deg >= 10).
- SELF-CHECK every limit/band/event/capture before you output it: "Would a pilot flying this phase correctly, exactly as
  this POH describes, stay inside this rule for the whole phase?" If not, the rule is scoped wrong: fix the phases/when,
  or leave it out.

RULE KINDS
  limit        stay within min and/or max. Prefer this for SOP RANGES: "Climb speed 70-80 KIAS" -> min 70, max 80.
  band         hold target +/- tolerance. Only when the SOP gives one target speed/height; tol_plus/tol_minus ONLY if the
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
- title: short plain English, e.g. "Max speed with flaps 10-30".

EXAMPLES (text -> rules)
Text: "VFE Maximum Flap Extended Speed: 10° Flaps 110 KIAS; 10° to 30° Flaps 85 KIAS. Do not exceed this speed with flaps down."
Rules: {{"kind":"limit","title":"Max speed with flaps 10","metric":"ias","unit":"kt","max":110,"when":[{{"metric":"flaps_deg","op":">","value":0,"unit":"deg"}}],"phases":[],"severity":"warning","tags":["aircraft_limit","flight_telemetry","pilot_controls"],...}}
       {{"kind":"limit","title":"Max speed with flaps 10-30","metric":"ias","unit":"kt","max":85,"when":[{{"metric":"flaps_deg","op":">","value":10,"unit":"deg"}}],"phases":[],"severity":"warning","tags":["aircraft_limit","flight_telemetry","pilot_controls"],...}}
Text: "NORMAL LANDING 1. Airspeed -- 65-75 KIAS (flaps UP). 2. Wing Flaps -- AS DESIRED. 3. Airspeed -- 60-70 KIAS (flaps DOWN)."
Rules: {{"kind":"limit","title":"Approach speed, flaps up","metric":"ias","unit":"kt","min":65,"max":75,"when":[{{"metric":"flaps_deg","op":"==","value":0,"unit":"deg"}}],"phases":["BASE","FINAL"],"severity":"caution","tags":["flight_telemetry"],...}}
       {{"kind":"limit","title":"Approach speed, flaps down","metric":"ias","unit":"kt","min":60,"max":70,"when":[{{"metric":"flaps_deg","op":">","value":0,"unit":"deg"}}],"phases":["FINAL"],"severity":"caution","tags":["flight_telemetry"],...}}
Text: "BEFORE LANDING 1. Pilot and Passenger Seat Backs -- MOST UPRIGHT POSITION. 2. Seats and Seat Belts -- SECURE. 3. Fuel Selector Valve -- BOTH."
Rules: {{"kind":"unobservable","title":"Before landing checklist","metric":null,"unit":"none","phases":["DOWNWIND"],"severity":"caution","tags":["procedure","configuration"],...}}

WRONG (never do this)
- "Oil Pressure, Minimum: 20 PSI" -> rpm min 20.                 Oil pressure is not measurable: skip it.
- "ENGINE FAILURE DURING FLIGHT 1. Airspeed -- 68 KIAS" -> ias max 68 during CLIMB_OUT.   Emergency number applied to normal
  flight (normal climb is faster). Make one unobservable "Engine failure in flight" rule tagged emergency, or skip.
- "Autopilot minimum approach speed -- 80 KIAS" -> ias min 80 at all times.   Autopilot-only; every normal landing breaks it. Skip.
- "The magneto check should be made at 1800 RPM" -> rpm max 1800 at all times.   A ground test setting, not a limit.
- "fly not less than 2000 feet above noise sensitive areas" -> alt_agl 2000 at all times.   Enroute advice; the circuit is lower. Skip.
- "Transponder Mode Selector Knob -- ALT." -> unobservable rule.   Optional-equipment operation. Skip."""


# What to look for in each training handbook (the reviewer picks the book at upload).
HANDBOOK_FOCUS = {
    "afh": """This is the FAA Airplane Flying Handbook. Its chapters on basic flight maneuvers, takeoffs and departure climbs,
airport traffic patterns, and approaches and landings describe how to fly a circuit. Look for: traffic pattern altitude and
the height at which to turn, bank angle for turns in the pattern, when to start the descent, stabilized approach criteria,
when to go around, and how the pilot divides attention between outside references and the instruments (integrated flight
instruction) -> gaze rules.""",
    "ifh": """This is the FAA Instrument Flying Handbook. Its chapters on attitude instrument flying describe the instrument
cross-check (scan) and the common scan errors: FIXATION (staring at one instrument) -> gaze max_dwell; OMISSION (leaving an
instrument out of the scan) -> gaze max_gap; EMPHASIS (relying on one instrument) -> gaze max_share. Which instruments are
primary/supporting for straight-and-level flight, turns, climbs and descents -> gaze max_gap rules on those instruments for the
matching circuit phases (climbs -> CLIMB_OUT; turns -> CROSSWIND, BASE; level flight -> DOWNWIND; descents -> BASE, FINAL).
In a visual circuit the pilot mostly looks outside: these rules describe HOW to scan the panel when looking inside, they do
NOT mean the pilot should be heads-down. Most of this book is about flying in cloud: navigation systems, IFR procedures,
approaches, holds, ATC and helicopter chapters contain nothing to extract.""",
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
    """Prompt H1 for training handbooks (FAA or a school's own). Same output
    schema as the POH prompt plus the "gaze" kind. Rules from a handbook apply to
    EVERY aircraft, so anything aircraft-specific (V-speeds, flap and power
    settings) is left to the aircraft's POH."""
    book = book if book in BOOKS else "other"
    label = BOOKS[book][0]
    metric_lines = "\n".join(f"  - {k}: {v[3]}" for k, v in METRICS.items())
    aoi_lines = "\n".join(f"  - {k}: {v[1]}" for k, v in AOI_ROLES.items())
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
JSON object {{"rules": [...]}} only.

TELEMETRY THE SIMULATOR CAN MEASURE (the only allowed "metric" values):
{metric_lines}
Anything else (wind, visibility, distances, radio, lights, checklists, traffic) CANNOT be measured.

WHERE THE PILOT LOOKS (kind "gaze"). "aoi" is the area of the cockpit:
{aoi_lines}
"gaze_measure" is what is checked:
{measure_lines}
Seconds use unit "s"; shares use unit "percent". If the book states the number, copy it exactly. If the book describes the
behaviour WITHOUT a number ("avoid fixation", "check the airspeed frequently", "spend most of the time looking outside"),
still create the gaze rule with value null and unit "none": the instructor sets the threshold. Never invent a number.

FLIGHT PHASES (exact names): {", ".join(phases)}
Mapping: "takeoff" -> TAKEOFF_ROLL; "after takeoff"/"departure climb" -> CLIMB_OUT; "crosswind leg" -> CROSSWIND;
"downwind leg" -> DOWNWIND; "base leg" -> BASE; "final approach" -> FINAL; "roundout"/"flare"/"touchdown" -> FLARE_TOUCHDOWN;
"after landing"/"landing roll" -> ROLLOUT; "traffic pattern" -> CROSSWIND, DOWNWIND, BASE, FINAL. Empty phases = every phase.

EXTRACT
1. Circuit procedure stated in measurable terms that holds for any light aeroplane: pattern altitude, height to reach before
   turning, bank angle for pattern turns, descent rate on approach, stabilized approach criteria (by what height the
   approach must be stable, descent rate limits), go-around criteria.
2. Scan and lookout guidance -> gaze rules (see above).
3. Actions a trainee must perform in the circuit that the simulator cannot measure (look for traffic before every turn,
   clear the area): ONE "unobservable" rule each. Keep these few; do not make one per sentence.
Values a handbook calls typical ("usually 1,000 feet") ARE rules for the baseline: copy them, severity caution.

SKIP (return nothing for these)
- Definitions, explanations, theory, history, physiology, aerodynamics, systems, weather, regulations text.
- ANYTHING THAT DEPENDS ON THE AIRCRAFT TYPE: V-speeds (Vx, Vy, Vref, Va, 1.3 VSO, "the recommended speed", "the speed in
  the POH/AFM"), flap settings, power or RPM settings. These come from the aircraft's own POH.
- Maneuvers that are not part of a circuit lesson: steep turns, stalls, slow flight, spins, chandelles, lazy eights,
  eights-on-pylons and other ground reference maneuvers, emergencies and forced landings, night flying, short-field and
  soft-field technique, multi-engine, tailwheel, turboprop, jet, seaplane, helicopter.
- Instrument (IFR) procedures: departures, holds, approaches, minimums, navigation. (Scan technique for attitude instrument
  flying is NOT skipped: it becomes gaze rules.)

RULE KINDS
  limit        stay within min and/or max ("do not exceed 30 degrees of bank in the pattern" -> bank max 30, min -30;
               bank is signed: left negative, right positive).
  band         hold target +/- tolerance. tol_plus/tol_minus ONLY if the book states them, otherwise null (instructor sets them).
  event        must be true by the start of a phase: op, value, gate_phase.
  capture      measured at one moment ({", ".join(CAPTURE_AT)}) with min and/or max.
  gaze         where the pilot looks: aoi, gaze_measure, value (or null), unit. metric null.
  unobservable required action the simulator cannot see. metric null, unit "none", no numbers.

UNITS: copy numbers EXACTLY as written, with their unit ({", ".join(UNITS)}). Do not convert.
Signs: descent is negative vertical speed ("descent rate no more than 1,000 feet per minute" -> vs min -1000).

TAGS: 1 to 3 per rule, most important first:
{tag_lines}

STRICT
- source_quote: the sentence(s) the rule comes from, copied VERBATIM, max ~300 characters. Every number in the rule must
  appear in the quote.
- page: the number from the nearest preceding [[PAGE n]] marker.
- Never invent numbers, rules or tolerances. No duplicates of the same requirement.
- severity: warning = safety limit; caution = standard procedure; advisory = technique / good practice (most gaze rules).
- title: short plain English, e.g. "Pattern altitude 1,000 ft above the airport".

EXAMPLES (text -> rules)
Text: "The traffic pattern altitude is usually 1,000 feet above the elevation of the airport surface."
Rules: {{"kind":"band","title":"Pattern altitude 1,000 ft above the airport","metric":"alt_agl","unit":"ft","target":1000,"tol_plus":null,"tol_minus":null,"phases":["DOWNWIND"],"severity":"caution","tags":["flight_telemetry"],...}}
Text (example wording): "Avoid fixation: do not stare at any single instrument."
Rules: {{"kind":"gaze","title":"Don't fixate on one instrument","aoi":"instruments","gaze_measure":"max_dwell","value":null,"unit":"none","metric":null,"phases":[],"severity":"advisory","tags":["scan"],...}}
Text (example wording): "On final approach, the airspeed must be checked frequently."
Rules: {{"kind":"gaze","title":"Check airspeed often on final","aoi":"airspeed","gaze_measure":"max_gap","value":null,"unit":"none","metric":null,"phases":["FINAL"],"severity":"advisory","tags":["scan","flight_telemetry"],...}}
Text (example wording): "Before beginning any turn, look in the direction of the turn and clear the area of traffic."
Rules: {{"kind":"unobservable","title":"Clear the area before turning","metric":null,"unit":"none","phases":["CROSSWIND","DOWNWIND","BASE","FINAL"],"severity":"caution","tags":["situational"],...}}

WRONG (never do this)
- A definition such as "a medium bank is between about 20 and 45 degrees" -> bank limit.   A definition is not a requirement.
- "Climb at Vy" or "fly final at 1.3 VSO" -> ias rule.   Depends on the aircraft type: skip.
- "Maintain altitude within 100 feet during the steep turn" -> alt band.   Steep turns are not flown in a circuit: skip.
- "check the airspeed frequently" -> gaze max_gap value 5.   The book gives no number: value must be null.
- "Descend at 500 feet per minute on the ILS" -> vs rule.   Instrument approach: skip."""


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

    async def available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0, trust_env=False) as cli:
                r = await cli.get(f"{self.url}/api/tags")
                if r.status_code != 200:
                    return False
                names = {m.get("name") for m in r.json().get("models", [])}
                return self.model in names or f"{self.model}:latest" in names
        except Exception:
            return False

    async def _chat(self, system: str, user: str, fmt) -> str:
        body = {
            "model": self.model, "stream": False, "format": fmt,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "options": {"temperature": 0, "num_ctx": self.num_ctx, "num_predict": 3072},
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

    async def parse_chunk(self, chunk_text: str, system: str, schema: dict) -> list[dict]:
        user = f"SOP TEXT:\n<<<\n{chunk_text}\n>>>\nExtract the rules as JSON."
        fmt = schema if self._schema_supported else "json"
        try:
            out = await self._chat(system, user, fmt)
        except httpx.HTTPStatusError as e:
            # Ollama < 0.5 rejects a schema in `format` (400); some builds fail to
            # compile an unusual schema into a grammar (500). Fall back to plain
            # JSON mode once -- validate.py still enforces the vocabulary.
            if self._schema_supported and e.response.status_code in (400, 500):
                log.warning("Ollama rejected schema format, falling back to format=json: %s", e)
                self._schema_supported = False
                out = await self._chat(system, user, "json")
            else:
                raise
        return parse_llm_output(out)


def parse_llm_output(out: str) -> list[dict]:
    """Tolerant JSON extraction: accepts {"rules": [...]} or a bare list."""
    out = (out or "").strip().strip("`")
    if out.startswith("json"):
        out = out[4:]
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        lo, hi = out.find("{"), out.rfind("}")
        if lo < 0 or hi <= lo:
            return []
        data = json.loads(out[lo:hi + 1])
    rules = data.get("rules", []) if isinstance(data, dict) else data
    return [r for r in rules if isinstance(r, dict)] if isinstance(rules, list) else []
