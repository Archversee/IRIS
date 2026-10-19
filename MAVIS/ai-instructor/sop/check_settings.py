"""Which flights get checked against the SOP / handbook rules live.

    circuit_flights   the curriculum's flying lessons (circuits, familiarization, ...):
                      an instructor can switch the checks off for these (SOP Rules page)
    free flight       ALWAYS checked -- not a setting

Kept in the same local file as the SOP page's other machine settings
(<sop.pdf_dir>/settings.json, git-ignored), so a choice survives a restart.
The default comes from config.yaml `sop.check_circuit_flights` (true if unset).
"""
from __future__ import annotations

import json
from pathlib import Path

from .schema import APP_ROOT

KEY = "check_circuit_flights"


def _file(cfg: dict | None) -> Path:
    sop_cfg = (cfg or {}).get("sop") or {}
    return APP_ROOT / sop_cfg.get("pdf_dir", "data/sop") / "settings.json"


def _read(cfg) -> dict:
    try:
        v = json.loads(_file(cfg).read_text(encoding="utf-8"))
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def circuit_flights_checked(cfg: dict | None) -> bool:
    v = _read(cfg).get(KEY)
    if isinstance(v, bool):
        return v
    return bool(((cfg or {}).get("sop") or {}).get(KEY, True))


def set_circuit_flights_checked(cfg: dict | None, on: bool) -> None:
    v = _read(cfg)                      # keep the file's other settings (e.g. the SOP model)
    v[KEY] = bool(on)
    f = _file(cfg)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(v, indent=1), encoding="utf-8")


def checks_on_for(cfg: dict | None, free_flight: bool) -> bool:
    """Is a flight that's starting checked live? Free Flight always is."""
    return True if free_flight else circuit_flights_checked(cfg)


def as_dict(cfg: dict | None) -> dict:
    return {"circuit_flights": circuit_flights_checked(cfg), "free_flight": True}
