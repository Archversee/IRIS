"""Lesson curriculum manifest (LMS-style navigation)."""
from __future__ import annotations
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
CATALOG_PATH = HERE / "lessons" / "catalog.json"


def load_catalog() -> dict:
    if not CATALOG_PATH.is_file():
        return {"title": "CESSNA 172 Flight Training", "modules": []}
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def flatten_lessons(catalog: dict) -> list[dict]:
    out: list[dict] = []
    for mod in catalog.get("modules", []):
        for les in mod.get("lessons", []):
            out.append({
                **les,
                "module_id": mod.get("id", ""),
                "module_title": mod.get("title", ""),
            })
    return out


def lesson_meta(catalog: dict, lesson_id: str) -> dict | None:
    for item in flatten_lessons(catalog):
        if item.get("id") == lesson_id:
            return item
    return None
