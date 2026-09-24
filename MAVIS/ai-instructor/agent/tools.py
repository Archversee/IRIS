"""Typed tools the agent may call, plus a registry that describes them to the
LLM and validates the arguments the LLM proposes.

A tool's `params` is a small JSON-schema-lite dict:
    {argname: {"type": "integer|number|boolean|string",
               "description": str,
               "enum": [...]        # optional allowed values
               "required": bool}}   # optional, defaults True
Keep tools SAFE: anything irreversible or risky should set needs_confirm=True so
the caller can gate it (Phase 1 tools are all user-intended and reversible).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass
class Tool:
    name: str
    description: str
    params: dict[str, dict]
    handler: Callable[..., Awaitable[Any]]
    needs_confirm: bool = False
    examples: list[str] = field(default_factory=list)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def describe(self) -> str:
        """Compact, LLM-friendly spec of every tool (name, args, one example)."""
        lines: list[str] = []
        for t in self._tools.values():
            parts = []
            for k, spec in t.params.items():
                typ = spec.get("type", "string")
                if spec.get("enum"):
                    typ = "|".join(str(e) for e in spec["enum"])
                opt = "" if spec.get("required", True) else "?"
                parts.append(f"{k}:{typ}{opt}")
            sig = ", ".join(parts) if parts else ""
            ex = f'  (e.g. "{t.examples[0]}")' if t.examples else ""
            lines.append(f'- {t.name}({sig}): {t.description}{ex}')
        return "\n".join(lines)

    def validate(self, name: str, args: dict | None) -> dict | None:
        """Coerce + check args against the tool schema. Returns a clean dict, or
        None if the tool is unknown / required args are missing / types or enums
        don't match. None means 'don't run it' - the caller falls back to Q&A."""
        tool = self.get(name)
        if tool is None:
            return None
        args = args or {}
        out: dict[str, Any] = {}
        for key, spec in tool.params.items():
            if key in args and args[key] is not None:
                val = args[key]
                typ = spec.get("type", "string")
                try:
                    if typ == "integer":
                        val = int(val)
                    elif typ == "number":
                        val = float(val)
                    elif typ == "boolean":
                        val = bool(val)
                    else:
                        val = str(val)
                except (TypeError, ValueError):
                    return None
                if spec.get("enum") and str(val) not in [str(e) for e in spec["enum"]]:
                    return None
                out[key] = val
            elif spec.get("required", True):
                return None
        return out
