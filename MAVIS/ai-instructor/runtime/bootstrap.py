"""Startup factories: config/lesson loading and the lesson-derived objects
(sim adapter, engine, scorer, event-log labels, web map state)."""
from __future__ import annotations
import logging
from pathlib import Path

import yaml

from core.lesson_engine import LessonEngine
from core.scoring import Scorer
from core.familiarization import (FamiliarizationEngine, FamiliarizationScorer, STEP_TITLES,
                                  field_center, is_familiarization, lesson_steps)

log = logging.getLogger("main")     # same logger name as before the move: log lines unchanged
HERE = Path(__file__).parent.parent  # ai-instructor/, where main.py lives


def load_yaml(p): return yaml.safe_load(Path(HERE / p).read_text(encoding="utf-8"))


def make_adapter(cfg, lesson):
    kind = cfg["sim"]["adapter"]
    if kind == "mock":
        from adapters.sim_mock import MockAdapter
        aircraft = load_yaml(cfg["aircraft_profile"])
        return MockAdapter(lesson, aircraft=aircraft)
    if kind == "msfs":
        from adapters.sim_msfs import MSFSAdapter
        return MSFSAdapter(cfg["sim"]["host"], cfg["sim"]["port"],
                           weather=cfg["sim"].get("weather"))
    if kind == "replay":
        from adapters.sim_replay import ReplayAdapter
        rp = cfg["sim"].get("replay") or {}
        if not rp.get("file"):
            raise SystemExit("sim.replay.file must name the flight log CSV")
        return ReplayAdapter(HERE / rp["file"], lesson["airport"]["elevation_ft"],
                             session=rp.get("session"))
    raise SystemExit(f"unknown sim adapter: {kind}")


def replay_video_for_web(cfg) -> str | None:
    """Media-relative path of the browser-ready copy of the replay recording."""
    rp = cfg["sim"].get("replay") or {}
    if cfg["sim"]["adapter"] != "replay" or not rp.get("video"):
        return None
    media = HERE / cfg["web"]["media_dir"]
    src = media / rp["video"]
    if not src.exists():
        log.warning("replay video not found: %s (trainee view will show no signal)", src)
        return None
    from adapters.replay_video import prepare_video
    return prepare_video(src).relative_to(media).as_posix()


def make_engine(cfg, lesson, aircraft):
    """Circuit lessons run the phase detector; the familiarization lesson
    (type: familiarization) runs its own airborne exercise steps."""
    kind = FamiliarizationEngine if is_familiarization(lesson) else LessonEngine
    return kind(lesson, aircraft, sim_adapter=cfg["sim"]["adapter"])


def make_scorer(lesson, aircraft):
    return (FamiliarizationScorer if is_familiarization(lesson) else Scorer)(lesson, aircraft)


def event_log_setup(lesson, engine) -> tuple[dict, str]:
    """(labels, kind) for the flight event log: the familiarization to-do
    steps under the names the to-do list shows, or the circuit's phases."""
    if is_familiarization(lesson):
        notes = lesson.get("step_notes") or {}
        labels = {sid: (notes.get(sid) or {}).get("title") or STEP_TITLES.get(sid, sid)
                  for sid in engine.detector.ORDER}
        return {**labels, "DEMO": "MAVIS demonstration"}, "exercise"
    return {"DEMO": "MAVIS demonstration"}, "phase"


def lesson_state(lesson) -> dict:
    """state["lesson"]: what the web map needs to draw this lesson."""
    out = {"runway": lesson["runway"], "pattern": lesson["pattern"],
           "airport": lesson["airport"], "scoring": lesson.get("scoring", {})}
    if lesson.get("segment"):
        # Take-off / Landing: the map draws that piece of the circuit only
        out["segment"] = lesson["segment"]
        out["final_m"] = float((lesson.get("airborne_start") or {}).get("final_m", 0))
    if is_familiarization(lesson):
        out.update(type="familiarization")
        if "FIND_AIRFIELD" in lesson_steps(lesson):
            # the "fly back to here" ring -- only for a lesson that ends by
            # finding Paya Lebar (Flight Familiarization), not the others
            lat, lon = field_center(lesson)
            out.update(field={"lat": lat, "lon": lon},
                       finish_radius_nm=float((lesson.get("finish") or {}).get("radius_nm", 2.0)))
    return out


def alt_scale_max(lesson) -> int:
    start = (lesson.get("airborne_start") or {}).get("alt_msl_ft")
    return int((start or 0) * 1.4) or int(lesson["pattern"]["altitude_msl_ft"] * 1.5)


def lesson_for_sim(cfg, sim, lesson):
    """A replayed flight is coached and scored against the runway end it was
    actually flown from; the map and pattern follow the same geometry."""
    if cfg["sim"]["adapter"] == "replay":
        from adapters.sim_replay import flown_from_reciprocal, reciprocal_runway
        if flown_from_reciprocal(sim.rows, lesson):
            lesson = reciprocal_runway(lesson)
            log.info("replay flown from runway %s: lesson geometry reversed",
                     lesson["runway"]["ident"])
    return lesson
