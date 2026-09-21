"""Instructor accounts, PIN + callsign for recording reference profiles."""
from __future__ import annotations
import json
import re
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
STORE_PATH = HERE / "data" / "instructors.json"
MIN_PIN_LEN = 6

_DEFAULT = [
    {"id": "iceman", "callsign": "Iceman", "pin": "123456"},
    {"id": "maverick", "callsign": "Maverick", "pin": "567890"},
]


def _slug(s: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:24]
    return base or uuid.uuid4().hex[:8]


class InstructorRegistry:
    def __init__(self, bootstrap_pin: str = "1234"):
        self._rows: list[dict] = []
        self._load(bootstrap_pin)

    def _load(self, bootstrap_pin: str) -> None:
        if STORE_PATH.is_file():
            try:
                data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
                if isinstance(data, list) and data:
                    self._rows = [self._norm(r) for r in data]
                    return
            except Exception:
                pass
        # First run: seed demo instructors; honour legacy config pin on Iceman.
        self._rows = [self._norm({**_DEFAULT[0], "pin": bootstrap_pin}), self._norm(_DEFAULT[1])]
        self._save()

    def _norm(self, row: dict) -> dict:
        return {
            "id": str(row.get("id") or _slug(row.get("callsign", "expert"))),
            "callsign": str(row.get("callsign", "Expert")).strip()[:32],
            "pin": str(row.get("pin", "")).strip()[:32],
        }

    def _save(self) -> None:
        STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STORE_PATH.write_text(json.dumps(self._rows, indent=2), encoding="utf-8")

    def list_admin(self) -> list[dict]:
        return [{"id": r["id"], "callsign": r["callsign"], "pin": r["pin"]} for r in self._rows]

    def verify_pin(self, pin: str) -> dict | None:
        p = str(pin).strip()
        for row in self._rows:
            if row["pin"] == p:
                return {"id": row["id"], "callsign": row["callsign"]}
        return None

    def add(self, callsign: str, pin: str) -> dict | None:
        cs = callsign.strip()[:32]
        p = pin.strip()[:32]
        if not cs or len(p) < MIN_PIN_LEN:
            return None
        if any(r["pin"] == p for r in self._rows):
            return None
        row = self._norm({"id": _slug(cs), "callsign": cs, "pin": p})
        self._rows.append(row)
        self._save()
        return row

    def update(self, inst_id: str, callsign: str | None = None, pin: str | None = None) -> bool:
        for row in self._rows:
            if row["id"] != inst_id:
                continue
            if callsign is not None:
                cs = callsign.strip()[:32]
                if cs:
                    row["callsign"] = cs
            if pin is not None:
                p = pin.strip()[:32]
                if len(p) < MIN_PIN_LEN:
                    return False
                if any(r["pin"] == p and r["id"] != inst_id for r in self._rows):
                    return False
                row["pin"] = p
            self._save()
            return True
        return False

    def delete(self, inst_id: str) -> bool:
        before = len(self._rows)
        self._rows = [r for r in self._rows if r["id"] != inst_id]
        if len(self._rows) == before:
            return False
        if not self._rows:
            return False
        self._save()
        return True
