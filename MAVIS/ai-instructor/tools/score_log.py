"""Score a logged SimConnect flight offline with the lesson's own phase detector
and scorer, and print the scorecard. (Leaderboard entries are saved by IRIS, in
Postgres, when a run is posted from the live app.)

Input is a CSV of raw SimConnect samples (one row per sample, any rate) with at
least: timestamp_utc (ISO 8601) or ts, latitude, longitude, altitude_ft,
airspeed_kt, heading_true_deg, pitch_deg, bank_deg, flaps_handle_pct,
throttle_pct, elevator_position, rudder_position.

The log is replayed by wall-clock timestamp at the app's tick rate, through the
same MSFS sign conventions the live adapter applies, so the numbers match what
a live run of the same flight would have scored.

    python tools/score_log.py data/recordings/supabase/visitor_log.csv \
        --name "Marc" --reciprocal

--reciprocal scores against the opposite runway (same pattern side), for
flights flown from the other end of the lesson's runway.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from adapters.sim_replay import (load_log, reciprocal_runway, row_time,   # noqa: E402
                                 to_telemetry, VS_LIMIT_FPM)
from core.phase_detector import PhaseDetector     # noqa: E402
from core.scoring import Scorer                   # noqa: E402


def resample(rows: list[dict], hz: float) -> list[dict]:
    """Pick the last sample before each tick so a high-rate log replays at
    the app's tick rate (the smoothness scale is tuned per tick)."""
    out, step = [], 1.0 / hz
    t_next, i = row_time(rows[0]), 0
    while i < len(rows):
        while i + 1 < len(rows) and row_time(rows[i + 1]) <= t_next:
            i += 1
        out.append(rows[i])
        t_next += step
        if row_time(rows[i]) > t_next:
            t_next = row_time(rows[i])
        i += 1
    return out


def telemetry_stream(rows: list[dict], elev_ft: float):
    """Rows as live telemetry, vertical speed recomputed and smoothed the way
    the live adapter does it."""
    vs, prev_alt, prev_t = 0.0, None, None
    for r in rows:
        t, alt = row_time(r), float(r["altitude_ft"])
        if prev_alt is not None and t > prev_t:
            raw = (alt - prev_alt) / (t - prev_t) * 60.0
            raw = max(-VS_LIMIT_FPM, min(VS_LIMIT_FPM, raw))
            vs += 0.25 * (raw - vs)
        prev_alt, prev_t = alt, t
        yield to_telemetry(r, elev_ft, vs, 10.0)


def score(rows, lesson, aircraft, name, mode, lesson_id, hz):
    det = PhaseDetector(lesson, total_circuits=1)
    det.advance("PRE_TAKEOFF")
    scorer = Scorer(lesson, aircraft)
    t0, prev_ground, transitions = row_time(rows[0]), True, []
    for tel in telemetry_stream(resample(rows, hz), lesson["airport"]["elevation_ft"]):
        g = det.geometry(tel)
        new = det.tick(tel, g)
        if new:
            transitions.append((tel.t - t0, new, tel))
        geom = {"along": g.along, "cross": g.cross}
        if det.phase not in ("BRIEFING", "PRE_TAKEOFF", "DEBRIEF"):
            scorer.sample(tel.as_dict(), geom, None, det.phase)
            if tel.on_ground and not prev_ground:
                scorer.on_landing(tel.as_dict(), geom)
        prev_ground = tel.on_ground
        if det.phase == "DEBRIEF":
            break
    return scorer.finalize(name, mode, lesson_id), transitions, det.phase


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", type=Path)
    ap.add_argument("--lesson", type=Path, default=HERE / "lessons" / "lesson01_pattern.yaml")
    ap.add_argument("--aircraft", type=Path, default=HERE / "profiles" / "aircraft" / "c172sp_g1000.yaml")
    ap.add_argument("--session", help="session_id to select when the CSV holds several flights")
    ap.add_argument("--name", default="")
    ap.add_argument("--given", default="")
    ap.add_argument("--family", default="")
    ap.add_argument("--mode", default="visitor", choices=("visitor", "trainee", "pilot"))
    ap.add_argument("--hz", type=float, default=10.0, help="replay tick rate (app default 10)")
    ap.add_argument("--reciprocal", action="store_true", help="score against the opposite runway end")
    a = ap.parse_args()

    lesson = yaml.safe_load(a.lesson.read_text(encoding="utf-8"))
    aircraft = yaml.safe_load(a.aircraft.read_text(encoding="utf-8"))
    if a.reciprocal:
        lesson = reciprocal_runway(lesson)
    rows = load_log(a.csv, a.session)
    card, transitions, final_phase = score(rows, lesson, aircraft, a.name, a.mode, a.lesson.stem, a.hz)
    card.given, card.family = a.given, a.family
    card.ts = row_time(rows[-1])

    print(f"runway {lesson['runway']['ident']} ({lesson['runway']['heading_true']:.0f} deg), "
          f"{len(rows)} samples over {row_time(rows[-1]) - row_time(rows[0]):.0f} s")
    for t, phase, tel in transitions:
        print(f"  t+{t:5.0f}s  {phase:16s} alt {tel.alt_msl:5.0f} ft  ias {tel.ias:3.0f} kt  hdg {tel.hdg:3.0f}")
    if final_phase != "DEBRIEF":
        print(f"  flight ended in {final_phase} (no full stop detected)")
    print(json.dumps(card.to_entry(), indent=2))


if __name__ == "__main__":
    main()
