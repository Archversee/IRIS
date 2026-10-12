"""Self-test for the rule evaluator (sop/evaluate.py) -- no Postgres, no sim.

Run:  python -m sop.evaltest      (from ai-instructor/)
Exits non-zero on failure. Part 1 checks each rule kind on hand-made
samples; part 2 flies Lesson 1's circuit with the mock aircraft (on a fake
clock, so it takes a second) and checks a C172 rule set against it.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from .evaluate import (RuleEvaluator, evaluate, pick_strictness, sample_from_flight_row, summarize)

_fails = 0


def check(name: str, cond: bool, extra=""):
    global _fails
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    _fails += 0 if cond else 1


def series(points, hz=5.0, **const):
    """[(seconds, phase, {metric: value})] held until the next point -> samples."""
    out = []
    for (t0, ph, vals), nxt in zip(points, points[1:] + [None]):
        t1 = nxt[0] if nxt else t0 + 1.0
        t = t0
        while t < t1 - 1e-9:
            out.append({"t": t, "phase": ph, **const, **vals})
            t = round(t + 1.0 / hz, 6)
    return out


def by_id(results, rid):
    return next(r for r in results if r["rule_id"] == rid)


def part1():
    band = {"id": 1, "kind": "band", "title": "Vy climb", "phases": ["CLIMB_OUT"], "metric": "ias",
            "target": 74, "tol_hi": 10, "tol_lo": 5, "severity": "caution"}
    # 20 s of climb, a 1 s dip (noise) and a 4 s dip (corrected)
    s = series([(0, "CLIMB_OUT", {"ias": 74}), (8, "CLIMB_OUT", {"ias": 67}), (9, "CLIMB_OUT", {"ias": 74}),
                (12, "CLIMB_OUT", {"ias": 67}), (16, "CLIMB_OUT", {"ias": 75}), (45, "CROSSWIND", {"ias": 75})])
    r = evaluate([band], s, "standard")[0]
    check("band: short blip ignored, 4 s dip corrected -> pass", r["verdict"] == "pass", r["summary"])
    check("band: one recorded excursion (the 4 s one)", len(r["violations"]) == 1, r["violations"])
    r = evaluate([band], s, "strict")[0]
    check("band: strict fails a 4 s excursion", r["verdict"] == "fail", r["summary"])
    r = evaluate([band], s, "lenient")[0]
    check("band: lenient x1.5 tolerance -> 67 kt is inside (74-7.5)", r["verdict"] == "pass"
          and not r["violations"], r)

    # settle time: off-target right after the phase change is not judged
    s = series([(0, "CROSSWIND", {"ias": 74}), (5, "CLIMB_OUT", {"ias": 60}), (9, "CLIMB_OUT", {"ias": 74}),
                (30, "CROSSWIND", {"ias": 74})])
    r = evaluate([band], s, "standard")[0]
    check("band: off-target in the 6 s settle window not judged", r["verdict"] == "pass" and not r["violations"], r)

    # heading wraps
    hb = {"id": 2, "kind": "band", "title": "Runway heading", "phases": [], "metric": "hdg",
          "target": 2, "tol_hi": 5, "tol_lo": 5}
    s = series([(0, "CLIMB_OUT", {"hdg": 359}), (20, "CLIMB_OUT", {"hdg": 359})])
    check("band: heading 359 is 3 deg from 2", evaluate([hb], s)[0]["verdict"] == "pass")

    lim = {"id": 3, "kind": "limit", "title": "Vfe flaps 30", "phases": [], "metric": "ias", "max_value": 85,
           "conditions": [{"metric": "flaps_deg", "op": ">=", "value": 25}], "severity": "warning"}
    s = series([(0, "BASE", {"ias": 90, "flaps_deg": 10}), (5, "FINAL", {"ias": 88, "flaps_deg": 30}),
                (6, "FINAL", {"ias": 70, "flaps_deg": 30}), (10, "FINAL", {"ias": 89, "flaps_deg": 30}),
                (14, "FINAL", {"ias": 70, "flaps_deg": 30})])
    ev = RuleEvaluator([lim], "standard")
    live = [f for x in s for f in ev.step(x)]
    r = ev.results()[0]
    check("limit: condition respected (90 kt with flaps 10 is fine)", r["verdict"] == "fail"
          and len(r["violations"]) == 1, r["violations"])
    check("limit: 1 s exceedance is noise, 4 s is a violation", r["violations"][0]["dur_s"] >= 3.5, r["violations"])
    check("limit: one live finding, warning severity", len(live) == 1 and live[0]["severity"] == "warning", live)

    ev_gate = {"id": 4, "kind": "event", "title": "Flaps 10 by base", "metric": "flaps_deg", "op": ">=",
               "value": 10, "gate_phase": "BASE"}
    ok = series([(0, "DOWNWIND", {"flaps_deg": 0}), (10, "DOWNWIND", {"flaps_deg": 10}), (20, "BASE", {"flaps_deg": 10}),
                 (30, "FINAL", {"flaps_deg": 20})])
    late = series([(0, "DOWNWIND", {"flaps_deg": 0}), (20, "BASE", {"flaps_deg": 0}), (25, "BASE", {"flaps_deg": 10}),
                   (30, "FINAL", {"flaps_deg": 20})])
    check("event: set before the gate -> pass", evaluate([ev_gate], ok)[0]["verdict"] == "pass")
    check("event: 5 s late -> fail", evaluate([ev_gate], late)[0]["verdict"] == "fail")
    never = series([(0, "DOWNWIND", {"flaps_deg": 0}), (20, "DOWNWIND", {"flaps_deg": 0})])
    check("event: gate phase never flown -> not_applicable", evaluate([ev_gate], never)[0]["verdict"] == "not_applicable")

    cap = {"id": 5, "kind": "capture", "title": "Gentle touchdown", "metric": "vs", "capture_at": "touchdown",
           "min_value": -300}
    soft = series([(0, "FINAL", {"vs": -500, "on_ground": False}), (10, "FLARE_TOUCHDOWN", {"vs": -150, "on_ground": False}),
                   (14, "ROLLOUT", {"vs": 0, "on_ground": True})])
    hard = series([(0, "FINAL", {"vs": -500, "on_ground": False}), (14, "ROLLOUT", {"vs": 0, "on_ground": True})])
    check("capture: -150 fpm at touchdown -> pass", evaluate([cap], soft)[0]["verdict"] == "pass")
    r = evaluate([cap], hard)[0]
    check("capture: -500 fpm at touchdown -> fail", r["verdict"] == "fail", r["summary"])

    missing = {"id": 6, "kind": "limit", "title": "RPM", "metric": "rpm", "max_value": 2700}
    check("missing metric -> insufficient_data", evaluate([missing], soft)[0]["verdict"] == "insufficient_data")
    radio = {"id": 7, "kind": "unobservable", "title": "Radio call"}
    gz = {"id": 8, "kind": "gaze", "title": "Scan", "aoi": "outside", "gaze_measure": "min_share", "value": 0.8}
    rs = evaluate([radio, gz], soft)
    check("unobservable -> instructor; gaze -> not_evaluated",
          [x["verdict"] for x in rs] == ["instructor", "not_evaluated"])
    check("summary counts", summarize(rs)["by_verdict"] == {"instructor": 1, "not_evaluated": 1})

    check("strictness: session > trainee > mode", pick_strictness("strict", "lenient", "visitor")[0] == "strict"
          and pick_strictness(None, "lenient", "pilot")[0] == "lenient"
          and pick_strictness(None, None, "pilot")[0] == "strict"
          and pick_strictness(None, None, None)[0] == "standard")

    row = {"ts": datetime(2026, 1, 1, tzinfo=timezone.utc), "altitude_ft": 1100.0, "airspeed_kt": 80.0,
           "throttle_pct": 60.0, "flaps_handle_pct": 50.0, "phase": "DOWNWIND", "rpm": None}
    sm = sample_from_flight_row(row, elevation_ft=65)
    check("flight_data row mapping", sm["alt_agl"] == 1035 and sm["throttle"] == 0.6 and sm["flaps_deg"] == 15
          and sm["on_ground"] is False and sm["rpm"] is None, sm)


def fly_mock_circuit(max_s: float = 900.0) -> tuple[list[dict], list[dict], dict, dict]:
    """Fly Lesson 1's circuit with the mock aircraft through the real
    LessonEngine on a fake clock. -> (samples, telemetry dicts with phase,
    lesson, aircraft). Also used by end-to-end tests."""
    import time
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    clock = {"t": 1_700_000_000.0}
    real_time = time.time
    time.time = lambda: clock["t"]
    samples, tels = [], []
    try:
        from adapters.input_hid import ControlSample
        from adapters.sim_mock import MockAdapter
        from core.lesson_engine import LessonEngine
        from .evaluate import sample_from_telemetry
        lesson = yaml.safe_load((root / "lessons/lesson01_pattern.yaml").read_text(encoding="utf-8"))
        aircraft = yaml.safe_load((root / "profiles/aircraft/c172sp_g1000.yaml").read_text(encoding="utf-8"))
        sim = MockAdapter(lesson, aircraft)
        sim.connect()
        eng = LessonEngine(lesson, aircraft, sim_adapter="mock")
        eng.start_flying()
        sim.release()                            # "cleared for take-off"
        for _ in range(int(max_s * 10)):         # 10 Hz, like the live tick loop
            clock["t"] += 0.1
            tel = sim.get_telemetry()
            for e in eng.tick(tel, ControlSample()):
                sim.comply(e.code, e.data)
            samples.append(sample_from_telemetry(tel.as_dict(), eng.phase))
            tels.append({**tel.as_dict(), "phase": eng.phase})
            if eng.phase == "DEBRIEF":
                break
    finally:
        time.time = real_time
    return samples, tels, lesson, aircraft


def part2():
    samples, _, lesson, aircraft = fly_mock_circuit()
    phases = []
    for s in samples:
        if not phases or phases[-1] != s["phase"]:
            phases.append(s["phase"])
    check("mock circuit reaches DEBRIEF", phases[-1] == "DEBRIEF", phases)
    v = aircraft["v_speeds"]
    rules = [
        {"id": 10, "kind": "band", "title": "Downwind height", "phases": ["DOWNWIND"], "metric": "alt_msl",
         "target": lesson["pattern"]["altitude_msl_ft"], "tol_hi": 100, "tol_lo": 100},
        {"id": 11, "kind": "limit", "title": "Never exceed", "phases": [], "metric": "ias", "max_value": v["vne"],
         "severity": "warning"},
        {"id": 12, "kind": "limit", "title": "Bank in the circuit", "phases": [], "metric": "bank",
         "min_value": -45, "max_value": 45},
        {"id": 13, "kind": "capture", "title": "Lift-off speed", "metric": "ias", "capture_at": "liftoff",
         "min_value": v["vr"] - 10},
        {"id": 14, "kind": "event", "title": "Flaps by final", "metric": "flaps_deg", "op": ">=", "value": 10,
         "gate_phase": "FINAL"},
        {"id": 15, "kind": "band", "title": "Final approach speed", "phases": ["FINAL"], "metric": "ias",
         "target": v["final"], "tol_hi": 10, "tol_lo": 5},
        {"id": 16, "kind": "band", "title": "Climb at exactly Vr", "phases": ["CLIMB_OUT"], "metric": "ias",
         "target": v["vr"], "tol_hi": 2, "tol_lo": 2},
    ]
    res = evaluate(rules, samples, "standard")
    for r in res:
        print(f"     {r['title']:<22} {r['verdict']:<16} {r['summary']}")
    check("mock circuit: Vne never exceeded", by_id(res, 11)["verdict"] == "pass")
    check("mock circuit: every rule judged (no n/a / missing data)",
          all(r["verdict"] in ("pass", "fail") for r in res), [(r["title"], r["verdict"]) for r in res])
    check("mock circuit: a rule it doesn't follow fails", by_id(res, 16)["verdict"] == "fail"
          and by_id(res, 16)["violations"], by_id(res, 16)["summary"])
    check("mock circuit: liftoff captured once", by_id(res, 13)["metrics"].get("n_checks") == 1,
          by_id(res, 13)["metrics"])


def main() -> int:
    part1()
    part2()
    print("\n" + ("all passed" if not _fails else f"{_fails} FAILED"))
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
