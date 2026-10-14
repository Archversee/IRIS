"""Lesson titles from MAVIS's curriculum catalog, for sessions that have no flight record
(flown before flights were logged): lesson_id -> "Flight Familiarization" etc."""
import json
import logging
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

CATALOG = Path(__file__).resolve().parents[2] / "MAVIS" / "ai-instructor" / "lessons" / "catalog.json"


@lru_cache(maxsize=1)
def _titles() -> dict[str, str]:
    try:
        cat = json.loads(CATALOG.read_text(encoding="utf-8"))
    except Exception as e:           # not checked out beside the backend, or unreadable
        log.info("lesson catalog not available (%s)", e)
        return {}
    return {les["id"]: les.get("title", les["id"])
            for mod in cat.get("modules", []) for les in mod.get("lessons", []) if "id" in les}


def lesson_title(lesson_id: str | None) -> str | None:
    return _titles().get(lesson_id) if lesson_id else None
