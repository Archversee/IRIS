"""Unsaved SOP drafts: local JSON files in MAVIS data/sop/drafts/.

A draft holds the LLM-drafted rules while the instructor reviews and edits
them on /sop/new. Nothing reaches Postgres until Save (store.save_draft);
the draft file is deleted once saved. Files survive a closed tab or a
MAVIS restart, so a review can be resumed.

  <id>.json        draft metadata + rules (small; polled by the page)
  <id>.pages.json  extracted page text (large; read on demand)
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()) + time.strftime("%z")


class DraftStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, did: str, suffix: str = ".json") -> Path:
        if not did or not all(ch in "0123456789abcdef" for ch in did):
            raise KeyError(did)
        return self.root / f"{did}{suffix}"

    def _write(self, path: Path, data) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    def create(self, filename: str, sha: str, pdf_path: str, by: str | None) -> dict:
        d = {"id": uuid.uuid4().hex, "filename": filename, "name": Path(filename).stem,
             "sha256": sha, "pdf_path": pdf_path, "created_by": by,
             "created_at": now_iso(), "updated_at": now_iso(),
             "status": "processing", "stage": "queued", "detail": "", "done": 0, "total": 0,
             "error": None, "warnings": [], "llm_model": None, "aircraft_profile": None,
             "page_count": 0, "ocr_pages": [], "rules": []}
        self._write(self._path(d["id"]), d)
        return d

    def get(self, did: str) -> dict | None:
        try:
            return json.loads(self._path(did).read_text(encoding="utf-8"))
        except (KeyError, FileNotFoundError):
            return None

    def put(self, d: dict) -> dict:
        d["updated_at"] = now_iso()
        self._write(self._path(d["id"]), d)
        return d

    def set_pages(self, did: str, pages: list[dict]) -> None:
        self._write(self._path(did, ".pages.json"), pages)

    def pages(self, did: str) -> list[dict]:
        try:
            return json.loads(self._path(did, ".pages.json").read_text(encoding="utf-8"))
        except (KeyError, FileNotFoundError):
            return []

    def delete(self, did: str) -> bool:
        found = False
        for suf in (".json", ".pages.json"):
            try:
                self._path(did, suf).unlink()
                found = True
            except (KeyError, FileNotFoundError):
                pass
        return found

    def list(self) -> list[dict]:
        out = []
        for p in self.root.glob("*.json"):
            if p.name.endswith(".pages.json") or p.name.endswith(".tmp"):
                continue
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            rules = d.get("rules") or []
            out.append({k: d.get(k) for k in ("id", "name", "filename", "status", "stage", "detail", "done",
                                               "total", "error", "aircraft_profile", "created_at",
                                               "updated_at", "created_by")}
                       | {"n_rules": sum(1 for r in rules if r.get("include")),
                          "n_blocked": sum(1 for r in rules if r.get("include") and r.get("blocked"))})
        return sorted(out, key=lambda x: x.get("updated_at") or "", reverse=True)

    def fail_interrupted(self) -> None:
        """At startup: a draft still 'processing' was cut off by a restart."""
        for p in self.root.glob("*.json"):
            if p.name.endswith(".pages.json"):
                continue
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if d.get("status") == "processing":
                d["status"], d["stage"] = "failed", "failed"
                d["error"] = "MAVIS restarted while this SOP was being processed - run the LLM again."
                self.put(d)
