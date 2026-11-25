"""Lesson curriculum manifest (LMS-style navigation).

A lesson's id is its file name: <module><number in the sidebar>_<name>. Ids are
also saved with flights, rule settings and expert runs, so a renamed lesson
keeps its earlier ids in the catalog's `old_ids` and they still find it."""
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


def old_ids(lesson_id: str) -> list[str]:
    """The ids this lesson had before it was renamed."""
    meta = lesson_meta(load_catalog(), lesson_id)
    return list((meta or {}).get("old_ids") or [])


def current_id(lesson_id: str | None) -> str | None:
    """A saved lesson id as it is now (unchanged if it was never renamed)."""
    for item in flatten_lessons(load_catalog()):
        if lesson_id in (item.get("old_ids") or []):
            return item["id"]
    return lesson_id


def lesson_meta(catalog: dict, lesson_id: str) -> dict | None:
    for item in flatten_lessons(catalog):
        if item.get("id") == lesson_id:
            return item
    return None
