"""Dependency-free self-test for the Phase 1 agent layer.

Run:  python -m agent.selftest      (from ai-instructor/)
Exercises schema validation and every IntentRouter branch with a fake LLM, so it
needs neither Ollama nor the sim. Exits non-zero on failure.
"""
from __future__ import annotations
import asyncio

from .tools import Tool, ToolRegistry
from .router import IntentRouter


def _registry() -> ToolRegistry:
    reg = ToolRegistry()

    async def _noop(**kw):
        return kw

    reg.register(Tool(
        "set_time_of_day", "Change time of day",
        {"hour": {"type": "integer", "description": "0-23"},
         "minute": {"type": "integer", "description": "0-59", "required": False}},
        _noop, examples=["make it darker"]))
    reg.register(Tool(
        "retry_phase", "Re-fly a leg",
        {"phase": {"type": "string", "enum": ["final", "downwind"], "required": False}},
        _noop))
    return reg


class _FakeLLM:
    def __init__(self, reply: str):
        self.reply = reply

    async def __call__(self, system, user, timeout):
        return self.reply


def test_validation():
    reg = _registry()
    assert reg.validate("set_time_of_day", {"hour": "20", "minute": 30}) == {"hour": 20, "minute": 30}
    assert reg.validate("set_time_of_day", {"minute": 30}) is None        # missing required hour
    assert reg.validate("set_time_of_day", {}) is None                    # missing required
    assert reg.validate("retry_phase", {"phase": "base"}) is None         # enum miss
    assert reg.validate("retry_phase", {"phase": "final"}) == {"phase": "final"}
    assert reg.validate("retry_phase", {}) == {}                          # optional absent -> ok
    assert reg.validate("nope", {}) is None                               # unknown tool
    assert "set_time_of_day(" in reg.describe()


async def _router_cases():
    reg = _registry()
    d = await IntentRouter(reg, _FakeLLM('{"tool":"set_time_of_day","args":{"hour":20,"minute":30}}'),
                           lambda: True).decide("make it night", {}, allow_regex=False)
    assert d.kind == "tool" and d.tool == "set_time_of_day" and d.args == {"hour": 20, "minute": 30}

    d = await IntentRouter(reg, _FakeLLM('{"tool":null}'), lambda: True).decide("what is Vne", {}, allow_regex=False)
    assert d.kind == "answer" and d.source == "llm"

    d = await IntentRouter(reg, _FakeLLM('not json'), lambda: True).decide("hmm", {}, allow_regex=False)
    assert d.kind == "answer" and d.source == "fallback"

    d = await IntentRouter(reg, _FakeLLM('{"tool":"set_time_of_day","args":{"minute":30}}'),
                           lambda: True).decide("darker", {}, allow_regex=False)
    assert d.kind == "answer"                                              # invalid args rejected

    d = await IntentRouter(reg, _FakeLLM('{"tool":"x"}'), lambda: False).decide("x", {}, allow_regex=False)
    assert d.kind == "answer" and d.source == "fallback"                  # llm offline


def main():
    test_validation()
    asyncio.run(_router_cases())
    print("agent selftest: all checks passed")


if __name__ == "__main__":
    main()
