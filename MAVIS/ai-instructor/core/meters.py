"""The to-do list's live readouts ("meters") in one standard shape, whichever
engine builds them (familiarization.py, lesson_engine.py):

    {"label": "Bank", "now": "18°", "target": "15–25°", "ok": True,  "hint": ""}

  target  what to aim for ("" for a reference-only reading)
  ok      True = target met right now, False = not yet / outside it,
          None = nothing to meet (reference reading, or just a hint)
  hint    a word on what to watch ("falling", "watch it rise") -- shown
          muted, never as a target and never read out as one

The pilot view (frontend TodoCard.jsx) draws every row the same way:
label, now / target, and one status mark from `ok`.
"""
from __future__ import annotations

# Readouts that describe what to watch rather than a value to reach.
HINTS = {"falling", "rising", "watch it roll", "watch it rise", "watch it settle",
         "slowly up", "settle on"}


def standard(m: dict) -> dict:
    target = (m.get("target") or "").strip()
    out = {"label": m.get("label", ""), "now": m.get("now", ""), "target": target,
           "ok": None, "hint": ""}
    if target.lower() in HINTS:
        out["target"], out["hint"] = "", target
        return out
    if not target:
        return out
    if "pct" in m:                     # progress toward a goal: met once complete
        out["ok"] = m["pct"] >= 1.0
    elif "ok" in m:
        out["ok"] = bool(m["ok"])
    return out


def standard_all(rows: list[dict] | None) -> list[dict]:
    return [standard(m) for m in rows or [] if m]
