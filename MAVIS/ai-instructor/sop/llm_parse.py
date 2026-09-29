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

from .schema import CAPTURE_AT, METRICS, TAGS, UNITS

log = logging.getLogger(__name__)


PROMPT_VERSION = "2"


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
