"""The app's shared runtime context, `rt`: one object every runtime/ module reads.

Three kinds of thing live on it:
  * references swapped mid-run -- select_lesson() and _orient_lesson_to_aircraft()
    replace the lesson and everything built from it; init_tts() fills in the
    voice engine once loaded;
  * the shared state and services main.amain() makes once (state, session,
    coach, event_log, ...);
  * the actions each runtime/ module provides (start, finish_lesson, speak, ...),
    which the other modules and main.py call through rt.

Everything is read through `rt` at call time (rt.engine, rt.state, rt.start(...)),
never copied into a module, so nothing holds on to a stale engine after a lesson
switch, and the modules can call each other in any order. __slots__ keeps a
mistyped name an error instead of a silent new attribute.
"""
from __future__ import annotations


class Runtime:
    __slots__ = (
        # swapped mid-run: always read through rt, never held on to
        "lesson", "sim", "engine", "scorer", "refs", "lesson_id", "tts",
        # shared state, services and helpers made once in main.amain()
        "aircraft", "attempts", "board", "catalog", "cfg", "checkpoints", "coach",
        "demo_ctl", "event_log", "flap_style", "free_tracker", "instructors", "loop",
        "pending", "phase_passed", "recorder", "run_gen", "score_seq", "scored",
        "session", "speak_gen", "started", "state", "transcript", "tts_ready",
        "_flight_phase", "_publish_events", "_sim_now",
        # provided by runtime/flight_flow.py
        "night_sequence", "skip_demo", "_airborne_start", "_apply_lesson_clock",
        "_briefing_lines", "_connect_msfs_with_retries", "_demos_wanted", "_fam_finish",
        "_fam_freezes", "_fam_instruct", "_fam_opening", "_lesson_text",
        "_orient_lesson_to_aircraft", "_set_frozen", "_speakable", "_to_runway_start",
        # provided by runtime/instructor_admin.py
        "instructor_add", "instructor_delete", "instructor_lock", "instructor_remove",
        "instructor_rename", "instructor_unlock", "instructor_update",
        # provided by runtime/scoring_flow.py
        "finish_lesson", "score_submit", "_discard_card", "compare_reference", "save_reference",
        # provided by runtime/session.py
        "configure_session", "intro", "mute", "quit_lesson", "relaunch", "relaunch_new",
        "restart", "select_lesson", "set_reference", "start", "_apply_reference",
        "_sync_refs_to_state",
        # provided by runtime/sop_live.py
        "live_rules", "_check_rules_live", "_finish_rule_checks", "_load_sop",
        # provided by runtime/speech.py
        "init_tts", "reading_say", "speak", "stop_speech", "tts_dir", "tts_pool",
    )
