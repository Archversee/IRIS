"""The references the app swaps out mid-run, in one place.

select_lesson() and _orient_lesson_to_aircraft() replace the lesson and
everything built from it; init_tts() fills in the voice engine once it has
loaded. Code reads them through `rt` at call time (rt.engine, rt.lesson, ...),
so nothing holds on to a stale engine after a lesson switch.
"""
from __future__ import annotations
from typing import Any


class Runtime:
    __slots__ = ("lesson", "sim", "engine", "scorer", "refs", "lesson_id", "tts")

    lesson: dict
    sim: Any            # the sim adapter (mock / msfs / replay)
    engine: Any         # LessonEngine or FamiliarizationEngine
    scorer: Any         # Scorer or FamiliarizationScorer
    refs: Any           # ReferenceRegistry
    lesson_id: str
    tts: Any            # voice.tts.TTS once loaded, else None
