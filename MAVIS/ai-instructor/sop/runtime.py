"""Which SOP applies to the flight that's starting.

At lesson start MAVIS asks the sim which aircraft is loaded (MSFS: TITLE /
ATC MODEL / ATC TYPE), matches it against each profiles/aircraft/*.yaml's
`sim_match` list, and loads the verified SOP ruleset bound to that profile.
Many SOPs can exist side by side -- one active per aircraft profile -- and
the one that's used is decided by the plane actually being flown.

Fallbacks, in order:
  1. sop.aircraft_override in config.yaml (forces a profile id)
  2. aircraft reported by the sim, matched via `sim_match`
  3. sim reported nothing (mock / replay / older adapter) -> config's aircraft_profile
If the sim reports an aircraft that matches NO profile, no SOP is applied:
using a C172 SOP on some other type would be worse than none.

Training-handbook rules (FAA handbooks or the school's own, scope 'general')
apply to every aircraft, so they are loaded on top of the aircraft's rules --
and on their own when the aircraft has no rules or isn't recognised.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .schema import aircraft_profiles

log = logging.getLogger(__name__)


def match_profile(ident: dict | None, profiles: list[dict]) -> tuple[str | None, str | None]:
    """-> (profile_id, matched_pattern). Longest pattern wins (most specific)."""
    if not ident:
        return None, None
    hay = " | ".join(str(v) for v in ident.values() if v).lower()
    if not hay.strip():
        return None, None
    best: tuple[int, str, str] | None = None
    for a in profiles:
        pats = (a["profile"].get("sim_match") or []) + [a["name"]]
        for p in pats:
            p = str(p).strip()
            if p and p.lower() in hay and (best is None or len(p) > best[0]):
                best = (len(p), a["id"], p)
    return (best[1], best[2]) if best else (None, None)


class SopRuntime:
    def __init__(self, cfg: dict, default_profile: str):
        from .store import RuleStore
        self.store = RuleStore((cfg.get("database") or {}).get("url"))
        self.default = Path(default_profile).stem
        self.override = ((cfg.get("sop") or {}).get("aircraft_override") or "").strip() or None
        self.active: dict | None = None      # {"rules": [...], ...} for the evaluator

    def resolve(self, ident: dict | None) -> dict:
        profiles = aircraft_profiles()
        names = {a["id"]: a["name"] for a in profiles}
        detected = " / ".join(str(v) for v in (ident or {}).values() if v) or None
        if self.override:
            pid, source, why = self.override, "override", "sop.aircraft_override in config.yaml"
        elif detected:
            pid, pat = match_profile(ident, profiles)
            source, why = "sim", (f"matched '{pat}'" if pid else "no aircraft profile matches it")
        else:
            pid, source, why = self.default, "config", "the simulator didn't report an aircraft"
        return {"aircraft_id": pid, "aircraft_name": names.get(pid, pid), "source": source,
                "detected": detected, "why": why}

    async def load(self, ident: dict | None) -> dict:
        """status: active (the aircraft has rules) | none (it has none) |
        no_profile (aircraft not recognised) | unavailable (DB down). Handbook
        rules ride along in every case but 'unavailable': n_rules counts all
        loaded rules, n_handbook_rules / handbooks the handbook part."""
        info = self.resolve(ident)
        self.active = None
        try:
            act = await asyncio.wait_for(self.store.active_rules(info["aircraft_id"]), timeout=4)
        except Exception as e:
            log.warning("SOP load failed: %s", e)
            return {**info, "status": "unavailable", "error": str(e)}
        self.active = act
        layers = (act or {}).get("layers") or []
        books = [x for x in layers if x["scope"] == "general"]
        extra = {"n_rules": len(act["rules"]) if act else 0,
                 "n_handbook_rules": sum(x["n_rules"] for x in books),
                 "handbooks": [x["name"] for x in books]}
        if not info["aircraft_id"]:
            return {**info, **extra, "status": "no_profile"}
        if not act or not act.get("rule_set_id"):
            return {**info, **extra, "status": "none"}
        return {**info, **extra, "status": "active", "rule_set_id": act["rule_set_id"], "name": act["name"],
                "filename": act["filename"], "updated_at": act["updated_at"]}
