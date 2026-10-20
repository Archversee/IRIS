"""Explain an incident in plain words with a local reasoning model (post-flight).

The model never sees raw telemetry: it gets the FACTS app/incidents.py
measured (findings + their numbers), the rule's own SOP quote, and a short
instructor-written guide (KNOWLEDGE below). It is told to use only those
numbers, and every number in its answer is checked against the facts --
anything it made up is flagged to the reader. Explanations are stored, and an
instructor can mark each one helpful / not helpful (incident_explanations),
which is the record for improving the prompt or the model later.

Model: debrief_settings() -- backend/.env EXPLAIN_*, else MAVIS's config.yaml
`debrief:` block, else its SOP model, else its coach model.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.error
import urllib.request
from pathlib import Path

import yaml

from . import db
from .config import settings

log = logging.getLogger(__name__)
PROMPT_VERSION = "1"

# Small, curated, instructor-editable. Keep it about HOW to read the facts --
# the facts themselves come from the analysis.
KNOWLEDGE = """\
How a deviation develops (situational awareness): 1) PERCEIVE -- the pilot must look at the instrument or
outside reference that shows the value; 2) COMPREHEND -- understand it is heading for a limit; 3) ACT --
a timely, correct-direction control input; 4) MONITOR -- confirm it is coming back and stop the correction
before it overshoots.
Common causes, by stage: not monitoring the right instrument (scan breakdown); attention fixed on one thing
for several seconds (fixation, often outside during turns or on the runway on final); looked but acted late
(slow recognition, high workload, or waiting for confirmation); over-control (correction too large, then an
overshoot or a second breach); distraction by a configuration change (flaps, power) or a phase change.
Scan technique (FAA Airplane / Instrument Flying Handbooks): in visual flight most time is spent outside,
with brief, regular checks of the key instruments -- airspeed and altitude every few seconds in the circuit,
airspeed especially on base and final. Attitude is set by the outside horizon or attitude indicator first,
then confirmed on the performance instruments.
Pupil size grows with workload but also with darker scenes; treat it as a hint, never as proof.
Coaching tone: name what happened, the likely reason, and one concrete thing to do next time.
"""

_SYSTEM = """You are a flight instructor writing a short debrief note about ONE moment in a training flight
where a standard operating procedure (SOP) rule was broken. Write for the student pilot.

STRICT RULES:
- Use ONLY the facts given. Every number you write must appear in the facts. Never invent times, speeds,
  heights or instruments.
- If the facts say eye data or the clock is unreliable, say gaze timings are uncertain and don't build on them.
- 4 to 7 sentences, no headings, no bullet lists, no markdown.
- Structure: what happened -> the most likely reason (perceive / comprehend / act / handling) -> what went
  well, if anything -> one concrete thing to practise.
- Calm, specific, encouraging. No blame."""


def _mavis_cfg() -> dict:
    try:
        return yaml.safe_load((Path(settings.mavis_lessons_dir).parent / "config.yaml").read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def debrief_settings() -> dict:
    """The debrief model and options: backend/.env EXPLAIN_* > MAVIS config.yaml `debrief:` > `sop:` > `coach:`.
    Read on every call, so editing config.yaml needs no restart."""
    cfg = _mavis_cfg()
    d, sop, coach = cfg.get("debrief") or {}, cfg.get("sop") or {}, cfg.get("coach") or {}

    def num(key, default, lo=None, hi=None):
        try:
            v = float(d.get(key, default))
        except (TypeError, ValueError):
            v = default
        return min(max(v, lo), hi) if lo is not None else v
    return {
        "url": (settings.explain_ollama_url or d.get("ollama_url") or sop.get("ollama_url") or coach.get("ollama_url")
                or "http://localhost:11434").rstrip("/"),
        "model": settings.explain_model or d.get("model") or sop.get("model") or coach.get("model") or "",
        "think": d.get("think", True) is not False,
        "timeout_s": num("timeout_s", settings.explain_timeout_s),
        "num_ctx": int(num("num_ctx", 16384)),
        "auto_on_save": d.get("auto_on_save", True) is not False,
        "max_auto_notes": int(num("max_auto_notes", 15)),
        "window_before_s": num("window_before_s", 30.0, 10.0, 120.0),
        "window_after_s": num("window_after_s", 30.0, 10.0, 120.0),
    }


def llm_target() -> tuple[str, str]:
    d = debrief_settings()
    return d["url"], d["model"]


def facts_for_llm(a: dict) -> dict:
    """The analysis minus the time series: only what the model may use."""
    g, r = a["gaze"], a["response"]
    return {
        "rule": {"title": a["rule"]["title"], "severity": a["rule"].get("severity"),
                 "sop_quote": a["rule"].get("source_quote")},
        "limit": a["bounds"], "phase": a["breach"].get("phase"),
        "outside_limit_for_s": a["breach"]["dur_s"],
        "worst_value": a["breach"].get("peak_text") or a["breach"].get("peak"),
        "build_up_before_breach_s": a["onset"]["lead_s"],
        "instrument_that_shows_it": g.get("primary_label"),
        "last_look_at_it_before_breach_s": g.get("last_look_before_s"),
        "first_look_after_build_up_started_s_rel_to_breach": g.get("first_look_after_onset_s"),
        "gaze_share_30s_before": [{"looking_at": x["name"], "share": x["share"]} for x in g.get("share_before", [])[:5]],
        "longest_unbroken_look_30s_before": g.get("longest_look"),
        "eye_data_coverage": g.get("coverage"),
        "first_control_input": r.get("first_input"), "back_within_limits_after_s": r.get("recovered_s"),
        "overshoot_value": r.get("overshoot"), "broke_it_again_after_s": r.get("rebreached_s"),
        "pupil_vs_usual": a["workload"].get("vs_baseline"),
        "mavis_callouts": [{"t_rel_to_breach_s": c["t"], "text": c["message"]} for c in a["context"]["callouts"]],
        "findings": [f["text"] for f in a["findings"]],
        "times": "seconds relative to the moment the limit was crossed (negative = before)",
    }


def build_messages(a: dict) -> list[dict]:
    facts = facts_for_llm(a)
    user = (f"Background for reading the facts:\n{KNOWLEDGE}\nFACTS (JSON):\n"
            f"{json.dumps(facts, ensure_ascii=False, indent=1)}\n\nWrite the debrief note now.")
    return [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}]


_NUM = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")


def unsupported_numbers(text: str, facts: dict) -> list[str]:
    """Numbers in the answer that aren't in the facts (allowing rounding and
    small counting words like 'one' being written as 1)."""
    known: list[float] = []
    for n in _NUM.findall(json.dumps(facts)):
        try:
            known.append(float(n))
        except ValueError:
            pass
    known_pct = [k * 100 for k in known]       # 0.49 share written as 49 %
    bad = []
    for n in _NUM.findall(text):
        v = float(n)
        if abs(v) <= 3 and float(v).is_integer():   # "one thing", "2 things", a single digit step
            continue
        if not any(abs(abs(v) - abs(k)) <= max(0.51, abs(k) * 0.02) for k in known + known_pct):
            bad.append(n)
    return bad


def _call_ollama(url: str, model: str, messages: list[dict], think: bool = True, num_ctx: int = 16384,
                 timeout_s: float | None = None) -> dict:
    body = {"model": model, "messages": messages, "stream": False, "think": think,
            "options": {"temperature": 0.3, "num_ctx": num_ctx}}
    timeout = timeout_s or settings.explain_timeout_s
    req = urllib.request.Request(f"{url}/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        if e.code == 400 and "think" in detail:          # not a reasoning model: ask again without thinking
            body["think"] = False
            req = urllib.request.Request(f"{url}/api/chat", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        raise RuntimeError(f"Ollama answered {e.code}: {detail}") from e


async def ask_model(messages: list[dict]) -> tuple[str, str, bool]:
    """-> (answer text, model, whether it reasoned first). RuntimeError with a readable reason."""
    d = debrief_settings()
    if not d["model"]:
        raise RuntimeError("No model set: set debrief.model in MAVIS's config.yaml (or EXPLAIN_MODEL in backend/.env)")
    try:
        resp = await asyncio.to_thread(_call_ollama, d["url"], d["model"], messages, d["think"], d["num_ctx"],
                                       d["timeout_s"])
    except Exception as e:
        raise RuntimeError(f"The debrief model at {d['url']} ({d['model']}) isn't reachable: {e}") from e
    msg = resp.get("message") or {}
    text = re.sub(r"<think>.*?</think>", "", msg.get("content") or "", flags=re.S).strip()
    if not text:
        raise RuntimeError("The model returned an empty answer")
    return text, d["model"], bool(msg.get("thinking"))


async def explain(a: dict) -> dict:
    text, model, reasoned = await ask_model(build_messages(a))
    return {"text": text, "model": model, "reasoned": reasoned,
            "unsupported_numbers": unsupported_numbers(text, facts_for_llm(a)), "prompt_version": PROMPT_VERSION}


SCHEMA = """
create table if not exists incident_explanations (
    id             bigint generated always as identity primary key,
    result_id      bigint not null references rule_results(id) on delete cascade,
    idx            int not null,                 -- which breach of that rule (violations[idx])
    model          text,
    prompt_version text,
    reasoned       boolean,
    text           text not null,
    unsupported_numbers text[] not null default '{}',
    requested_by   text,
    created_at     timestamptz not null default now(),
    rating         text check (rating in ('helpful', 'not_helpful')),
    rating_note    text,
    rated_by       text,
    rated_at       timestamptz
);
create index if not exists incident_explanations_result on incident_explanations (result_id, idx, created_at desc);
alter table incident_explanations add column if not exists pre_s real;
alter table incident_explanations add column if not exists post_s real
"""


async def ensure_schema() -> None:
    try:
        async with db.acquire() as conn:
            for stmt in (s.strip() for s in SCHEMA.split(";")):
                if stmt:
                    await conn.execute(stmt)
    except Exception as e:  # rule_results not created yet
        log.warning("incident explanations table not created: %s", e)
