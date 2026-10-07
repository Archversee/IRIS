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
import logging
import os
import threading
import time
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

# Windows can't replace/delete a file while any handle to it is open
# (WinError 5 / 32): the page polling a draft, Defender or the search indexer
# scanning a freshly written file, OneDrive syncing Downloads... Every access
# goes through one process-wide lock (so MAVIS never collides with itself --
# the extractor writes progress from a worker thread while the web server
# reads), and replaces retry briefly when an outside process holds the file.
_IO_LOCK = threading.RLock()
_RETRY_S = (0.05, 0.1, 0.2, 0.3, 0.5, 0.8, 1.0, 1.5)


def _retry(fn, *args):
    for delay in _RETRY_S:
        try:
            return fn(*args)
        except PermissionError as e:
            log.debug("draft file busy (%s), retrying in %.2fs", e, delay)
            time.sleep(delay)
    return fn(*args)          # last try: let the real error surface

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
        body = json.dumps(data, ensure_ascii=False, indent=1)
        with _IO_LOCK:
            tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            _retry(tmp.write_text, body, "utf-8")
            try:
                _retry(os.replace, tmp, path)
            finally:
                try:
                    tmp.unlink()
                except (FileNotFoundError, PermissionError):
                    pass

    def _read(self, path: Path):
        with _IO_LOCK:
            return json.loads(_retry(path.read_text, "utf-8"))

    def create(self, filename: str, sha: str, pdf_path: str, by: str | None,
               doc_type: str = "poh", handbook: str | None = None) -> dict:
        d = {"id": uuid.uuid4().hex, "filename": filename, "name": Path(filename).stem,
             "sha256": sha, "pdf_path": pdf_path, "created_by": by,
             "doc_type": doc_type, "handbook": handbook,
             "created_at": now_iso(), "updated_at": now_iso(),
             "status": "processing", "stage": "queued", "detail": "", "done": 0, "total": 0,
             "error": None, "warnings": [], "llm_model": None, "aircraft_profile": None,
             "page_count": 0, "ocr_pages": [], "rules": []}
        self._write(self._path(d["id"]), d)
        return d

    def get(self, did: str) -> dict | None:
        try:
            return self._read(self._path(did))
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
            return self._read(self._path(did, ".pages.json"))
        except (KeyError, FileNotFoundError):
            return []

    def delete(self, did: str) -> bool:
        found = False
        for suf in (".json", ".pages.json"):
            try:
                with _IO_LOCK:
                    _retry(self._path(did, suf).unlink)
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
                d = self._read(p)
            except Exception:
                continue
            rules = d.get("rules") or []
            out.append({k: d.get(k) for k in ("id", "name", "filename", "status", "stage", "detail", "done",
                                               "total", "error", "aircraft_profile", "created_at",
                                               "updated_at", "created_by", "handbook")}
                       | {"doc_type": d.get("doc_type") or "poh"}
                       | {"n_rules": sum(1 for r in rules if r.get("include")),
                          "n_blocked": sum(1 for r in rules if r.get("include") and r.get("blocked"))})
        return sorted(out, key=lambda x: x.get("updated_at") or "", reverse=True)

    def fail_interrupted(self) -> None:
        """At startup: a draft still 'processing' was cut off by a restart."""
        for p in self.root.glob("*.json"):
            if p.name.endswith(".pages.json"):
                continue
            try:
                d = self._read(p)
            except Exception:
                continue
            if d.get("status") == "processing":
                d["status"], d["stage"] = "failed", "failed"
                d["error"] = "MAVIS restarted while this SOP was being processed - press 'Run again'."
                self.put(d)
