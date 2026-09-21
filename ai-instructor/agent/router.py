"""Intent router: turn a free-text (or transcribed voice) utterance into a single
tool call, or decide it's a general question to answer.

Order of resolution (each step is a graceful fallback for the next):
  1. deterministic matchers  - fast, 100% reliable, no LLM (optional; the caller
     may have already run its own rule layer, in which case pass allow_regex=False)
  2. local LLM tool-selection - the "understand intent" hand-off: the model sees
     the tool schema + a little context and returns {"tool": name|null, "args": {}}
  3. answer                   - null tool, LLM offline, or invalid args -> plain Q&A

The LLM only ever NAMES a tool and proposes args; ToolRegistry.validate is the gate
before anything runs. One tool per utterance (Phase 1).
"""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass

from .tools import ToolRegistry

log = logging.getLogger(__name__)

_SYS = (
    "You route ONE message from a flight-simulator student to at most one tool, or "
    "to null when it is a general question, chit-chat, or something no tool covers. "
    "Reply with ONLY compact JSON, no prose and no code fences: "
    '{"tool": <name or null>, "args": {...}}. '
    "Prefer null unless a tool clearly applies. Never invent tools or arguments."
)


@dataclass
class Decision:
    kind: str                    # "tool" | "answer"
    tool: str | None = None
    args: dict | None = None
    source: str = "fallback"     # "regex" | "llm" | "fallback"


class IntentRouter:
    def __init__(self, registry: ToolRegistry, llm, llm_ok, matchers=None) -> None:
        """llm: async fn(system, user, timeout) -> str (e.g. Coach._ollama).
        llm_ok: callable -> bool. matchers: list of (compiled_regex, tool_name,
        extract) where extract(match, text) -> args dict (or None)."""
        self.reg = registry
        self.llm = llm
        self.llm_ok = llm_ok
        self.matchers = matchers or []

    async def decide(self, text: str, context: dict | None = None,
                     allow_regex: bool = True, timeout: float = 5.0) -> Decision:
        context = context or {}
        if allow_regex:
            for rx, name, extract in self.matchers:
                m = rx.search(text)
                if m:
                    args = self.reg.validate(name, extract(m, text) if extract else {})
                    if args is not None:
                        return Decision("tool", name, args, "regex")

        if not self.llm_ok():
            return Decision("answer", source="fallback")

        prompt = (f"Tools:\n{self.reg.describe()}\n\n"
                  f"Context: phase={context.get('phase')}, sim_time={context.get('time')}.\n"
                  f'Student said: "{text}"\nJSON:')
        try:
            out = await self.llm(_SYS, prompt, timeout)
            out = (out or "").strip().strip("`")
            data = json.loads(out[out.find("{"):out.rfind("}") + 1])
            name = data.get("tool")
            if not name:
                return Decision("answer", source="llm")
            args = self.reg.validate(name, data.get("args") or {})
            if args is None:
                return Decision("answer", source="llm")
            return Decision("tool", name, args, "llm")
        except Exception as e:               # malformed JSON, timeout, etc.
            log.debug("router LLM selection failed: %s", e)
            return Decision("answer", source="fallback")
