"""Self-test for the incident analysis (app/incidents.py) -- no database.

Run:  python -m app.incidents_selftest      (from backend/)
Builds made-up flights around a rule breach and checks the findings tell
the right story. Exits non-zero on failure.
"""
from __future__ import annotations

import sys

from .aoi_roles import role_of
from .incidents import analyse

T0 = 1_700_000_000.0          # the breach
ZONES = [{"name": "Airspeed", "x1": 100, "y1": 100, "x2": 300, "y2": 300},
         {"name": "Altimeter", "x1": 700, "y1": 100, "x2": 900, "y2": 300}]
VFE = {"id": 7, "kind": "limit", "title": "Max flap speed", "metric": "ias", "max_value": 85, "severity": "warning"}

_fails = 0


def check(name, cond, extra=""):
    global _fails
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    _fails += 0 if cond else 1


def flight(speed_at, throttle_at, hz=5, span=(-40, 45), bank_at=lambda t: 0.0):
    rows, t = [], span[0]
    while t <= span[1]:
        rows.append({"ts": T0 + t, "airspeed_kt": speed_at(t), "throttle_pct": throttle_at(t), "elevator_position": 0.0,
                     "aileron_position": 0.0, "rudder_position": 0.0, "bank_deg": bank_at(t), "flaps_deg": 30.0,
                     "phase": "BASE" if t < 10 else "FINAL", "altitude_ft": 900})
        t = round(t + 1 / hz, 3)
    return rows


def eye(target_at, hz=30, span=(-40, 45), pupil=4.0):
    rows, t = [], span[0]
    while t <= span[1]:
        where = target_at(t)
        e = {"ts": T0 + t, "blink": False, "pupil_diam_left_mm": pupil, "pupil_diam_right_mm": pupil}
        if where == "OTW":
            e.update(aoi="OTW", gaze_point_x=900, gaze_point_y=500)
        elif where in ("Airspeed", "Altimeter"):
            z = next(z for z in ZONES if z["name"] == where)
            e.update(aoi="Instruments", gaze_point_x=(z["x1"] + z["x2"]) / 2, gaze_point_y=(z["y1"] + z["y2"]) / 2)
        else:
            e.update(aoi="Instruments", gaze_point_x=500, gaze_point_y=700)
        rows.append(e)
        t = round(t + 1 / hz, 4)
    return rows


def codes(a):
    return {f["code"] for f in a["findings"]}


CLOCK = {"offset_s": 0.02, "corrected": True, "checked_at": "2026-10-20T10:00:00+00:00"}


def main() -> int:
    check("zone names -> roles", role_of("Airspeed") == "airspeed" and role_of("OTW") == "outside"
          and role_of("Clock") is None)

    # 1. stared outside through the turn, speed crept up from -8 s, noticed late, MAVIS called it, slow throttle
    speed = lambda t: 80 if t < -8 else (80 + (t + 8) * 0.7 if t < 5 else max(80, 89 - (t - 5) * 1.5))
    thr = lambda t: 60 if t < 4 else 40
    gaze = lambda t: "OTW" if t < 2 else ("Airspeed" if t < 4 else "OTW")
    a = analyse(VFE, {"t0": T0, "t1": T0 + 7.5, "peak": 89.1, "phase": "BASE"},
                flight(speed, thr, bank_at=lambda t: 20 if -15 < t < -2 else 0), eye(gaze), ZONES,
                [{"ts": T0 + 1.5, "who": "coach", "code": "SOP_RULE", "message": "Max flap speed: 86 kt"}],
                pupil_baseline_mm=3.6, clock=CLOCK)
    c = codes(a)
    print("     ", [f["text"] for f in a["findings"]])
    check("1: onset found ~8 s before the breach", 6 <= a["onset"]["lead_s"] <= 9, a["onset"])
    check("1: didn't monitor the airspeed indicator", "NOT_MONITORED" in c)
    check("1: noticed late (first look 2 s after)", "LATE_DETECTION" in c
          and a["gaze"]["first_look_after_onset_s"] == 2.0, a["gaze"])
    check("1: stared outside", "FIXATION" in c)
    check("1: first input is the throttle at +4 s", (a["response"]["first_input"] or {}).get("control") == "throttle"
          and abs(a["response"]["first_input"]["t"] - 4) <= 0.5, a["response"])
    check("1: corrected after MAVIS's callout", "PROMPTED" in c)
    check("1: built up during a turn", "BUSY" in c and "BUILD_UP" in c)
    check("1: high workload (pupil 11 % up is not enough; 4.0 vs 3.6 = 11 %)", "HIGH_WORKLOAD" not in c)
    check("1: no clock warning when the clock was measured", not ({"CLOCK_UNVERIFIED", "CLOCK_OFF"} & c))

    # 2. kept checking the airspeed every 3 s and cut power within 1 s -> good recovery
    speed2 = lambda t: 82 if t < -2 else (82 + (t + 2) * 1.5 if t < 1 else max(82, 86.5 - (t - 1) * 3))
    thr2 = lambda t: 60 if t < 0.2 else 45
    gaze2 = lambda t: "Airspeed" if (t % 3) < 0.8 else "OTW"
    b = analyse(VFE, {"t0": T0, "t1": T0 + 1.5, "peak": 86.5}, flight(speed2, thr2), eye(gaze2), ZONES,
                pupil_baseline_mm=4.0, clock=CLOCK)
    cb = codes(b)
    check("2: monitored -> no NOT_MONITORED / LATE_DETECTION", not ({"NOT_MONITORED", "LATE_DETECTION"} & cb), cb)
    check("2: good recovery", "GOOD_RECOVERY" in cb, cb)

    # 3. no eye data: gaze findings replaced by a data note, flight facts still there
    d = analyse(VFE, {"t0": T0, "t1": T0 + 7.5, "peak": 89.1}, flight(speed, thr), [], ZONES)
    cd = codes(d)
    check("3: no eye data said once, no gaze findings", "NO_EYE_DATA" in cd and not ({"NOT_MONITORED", "FIXATION"} & cd), cd)
    check("3: control response still found", d["response"]["first_input"] is not None)

    # 4. band, over-corrected below the lower limit; and an unmeasured clock
    band = {"id": 9, "kind": "band", "title": "Final approach speed", "metric": "ias", "target": 65, "tol_hi": 10,
            "tol_lo": 5, "severity": "caution"}
    speed4 = lambda t: 70 if t < -3 else (70 + (t + 3) * 2.5 if t < 1 else (78 - (t - 1) * 4 if t < 6 else 58))
    e4 = analyse(band, {"t0": T0, "t1": T0 + 2, "peak": 77.5}, flight(speed4, thr2), eye(gaze2), ZONES,
                 clock={})
    c4 = codes(e4)
    check("4: overshoot below 60 kt", "OVERSHOOT" in c4 and e4["response"]["overshoot"] == 58, e4["response"])
    check("4: unmeasured clock flagged", "CLOCK_UNVERIFIED" in c4)
    check("4: band bounds 60..75", e4["bounds"]["lo"] == 60 and e4["bounds"]["hi"] == 75)

    # 5. no zone drawn for the instrument the rule needs -> say so instead of guessing
    alt_rule = {"id": 3, "kind": "limit", "title": "Max bank", "metric": "bank", "min_value": -30, "max_value": 30}
    f5 = analyse(alt_rule, {"t0": T0, "t1": T0 + 2, "peak": 34}, flight(speed2, thr2, bank_at=lambda t: 34 if 0 <= t <= 2 else 10),
                 eye(gaze2), ZONES, clock=CLOCK)
    check("5: missing attitude zone reported", "NO_ZONE" in codes(f5), codes(f5))

    # 6. the explanation's number check: real numbers pass, invented ones are caught
    from .incident_explain import build_messages, facts_for_llm, unsupported_numbers
    facts = facts_for_llm(a)
    ok_text = "Speed built up for 8 s and peaked at 89 kt; you looked 2.0 s after and corrected in 7.5 s."
    check("6: real numbers accepted", unsupported_numbers(ok_text, facts) == [], unsupported_numbers(ok_text, facts))
    check("6: invented number flagged", unsupported_numbers("You were 23.7 s late.", facts) == ["23.7"])
    msgs = build_messages(a)
    check("6: prompt carries facts, not raw series", "FACTS" in msgs[1]["content"]
          and '"series"' not in msgs[1]["content"] and "gaze_segments" not in msgs[1]["content"])

    print("\n" + ("all passed" if not _fails else f"{_fails} FAILED"))
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
