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

from .schema import CAPTURE_AT, METRICS, UNITS

log = logging.getLogger(__name__)


def system_prompt(phases: list[str], aircraft_names: list[str]) -> str:
    metric_lines = "\n".join(f"  - {k}: {v[3]} (dimension {v[0]})" for k, v in METRICS.items())
    return f"""You extract machine-checkable operating rules from an aircraft Standard Operating Procedure (SOP).
The rules will be checked against live flight-simulator telemetry. A flight instructor reviews every rule you produce.

OUTPUT: JSON object {{"rules": [...]}} only. If the text contains no rules, return {{"rules": []}}.

TELEMETRY METRICS you may use (field name: meaning):
{metric_lines}

FLIGHT PHASES (use these exact names): {", ".join(phases)}
Map SOP wording to the closest phase(s): "takeoff"/"take-off roll" -> TAKEOFF_ROLL; "initial climb"/"after takeoff" -> CLIMB_OUT;
"circuit"/"pattern" -> CROSSWIND, DOWNWIND, BASE; "approach" -> BASE, FINAL; "landing" -> FINAL, FLARE_TOUCHDOWN; "after landing" -> ROLLOUT.
Use an empty phases list only if the rule applies at all times.

RULE KINDS:
  band         hold a metric at a target with a tolerance.   Fill target, tol_plus, tol_minus.
               e.g. "Maintain 1000 ft AGL +/-100 ft on downwind" -> metric alt_agl, target 1000, tol_plus 100, tol_minus 100.
  limit        a metric must stay within a min and/or max.    Fill min and/or max.
               e.g. "Do not exceed 85 KIAS with full flaps" -> metric ias, max 85, when [flaps_deg >= 30].
  event        something must become true by a certain phase. Fill op, value, gate_phase.
               e.g. "Flaps 30 set before short final" -> metric flaps_deg, op >=, value 30, gate_phase FINAL.
  capture      a metric measured at one moment ({", ".join(CAPTURE_AT)}). Fill capture_at and min/max.
               e.g. "Rotate at 55 KIAS" -> metric ias, capture_at liftoff, min 55.
  unobservable a required action that telemetry cannot see (radio calls, checklists, briefings, lookout).
               metric null, unit "none", no numbers.

UNITS: report numbers EXACTLY as written in the SOP and give their unit ({", ".join(UNITS)}).
Do not convert units yourself. Percent throttle -> unit "percent". Flap settings like "flaps 20" -> metric flaps_deg, value 20, unit "deg".
"when" lists conditions that must hold for the rule to apply (e.g. flaps extended).

STRICT REQUIREMENTS:
- source_quote: copy the sentence(s) the rule comes from VERBATIM, character for character, max ~300 characters.
- page: the number from the nearest preceding [[PAGE n]] marker.
- Never invent numbers, rules or tolerances that are not in the text. If the SOP gives no tolerance, leave tol_plus/tol_minus null.
- One rule per distinct requirement. Skip headings, definitions, performance tables and pure descriptions.
- severity: warning = safety limit / never exceed; caution = standard procedure target; advisory = technique or recommendation.
- title: a short plain-English name, e.g. "Downwind height".
Known aircraft profiles on this system: {", ".join(aircraft_names) or "unknown"}."""


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
            "options": {"temperature": 0, "num_ctx": self.num_ctx, "num_predict": 4096},
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
