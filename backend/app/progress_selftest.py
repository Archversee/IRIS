"""Self-test for the Progress logic (app/progress.py) -- no database.

Run:  python -m app.progress_selftest      (from backend/)
"""
from __future__ import annotations

import sys

from .progress import insights, pilot_of, scan_stats

_fails = 0


def check(name, cond, extra=""):
    global _fails
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    _fails += 0 if cond else 1


ZONES = [{"name": "Airspeed", "x1": 0, "y1": 0, "x2": 100, "y2": 100},
         {"name": "Altimeter", "x1": 200, "y1": 0, "x2": 300, "y2": 100}]


def flight(phases):
    """[(seconds, phase)] -> 5 Hz rows"""
    rows, t0 = [], 1_700_000_000.0
    for (start, ph), nxt in zip(phases, phases[1:] + [(phases[-1][0] + 1, None)]):
        t = start
        while t < nxt[0]:
            rows.append({"ts": t0 + t, "phase": ph})
            t += 0.2
    return rows


def eye(span, where):
    rows, t0, t = [], 1_700_000_000.0, span[0]
    while t < span[1]:
        w = where(t)
        e = {"ts": t0 + t, "blink": False}
        if w == "OTW":
            e.update(aoi="OTW")
        else:
            z = next(z for z in ZONES if z["name"] == w)
            e.update(aoi="Instruments", gaze_point_x=z["x1"] + 50, gaze_point_y=50)
        rows.append(e)
        t = round(t + 1 / 30, 4)
    return rows


def main() -> int:
    check("pilot from pilot_name", pilot_of({"pilot_name": "Alex", "name": "x"}) == "Alex")
    check("pilot from MAVIS name", pilot_of({"name": "Your First Circuit – Sam"}) == "Sam")
    check("date-stamped unnamed session has no pilot", pilot_of({"name": "MAVIS session – 20 Oct 02:05"}) is None)

    # airborne 0-120 s; base+final 60-120 s; airspeed glanced for 0.5 s every 4 s on approach, every 10 s before
    fl = flight([(0, "CLIMB_OUT"), (30, "DOWNWIND"), (60, "BASE"), (90, "FINAL"), (120, "ROLLOUT")])

    def where(t):
        period = 4 if t >= 60 else 10
        if (t % period) < 0.5:
            return "Airspeed"
        if (t % 7) < 0.5:
            return "Altimeter"
        return "OTW"
    s = scan_stats(eye((0, 125), where), fl, ZONES)
    ap, air = s["approach"], s["airborne"]
    check("approach: airspeed checked about every 4 s", ap and abs(ap["airspeed_check_every_s"] - 4) <= 0.6, ap)
    check("airborne: outside share ~80 %", air and 0.7 <= air["share"]["outside"] <= 0.9, air and air["share"])
    check("eye coverage ~100 %", s["eye_coverage"] and s["eye_coverage"] >= 0.95, s["eye_coverage"])
    check("no eye on the ground counted", s["airborne"]["seconds"] <= 121, s["airborne"]["seconds"])

    def f(rules, causes=None, breaches=0, every=None, outside=None):
        scan = None
        if every or outside:
            scan = {"approach": {"airspeed_check_every_s": every} if every else None,
                    "airborne": {"share": {"outside": outside}} if outside else None}
        return {"rules": rules, "causes": causes or {}, "breaches": breaches, "scan": scan}

    hist = [f({"Vfe": "fail", "Bank": "pass", "Height": "pass"}, {"NOT_MONITORED": 2}, 2, 11.0, 0.95),
            f({"Vfe": "fail", "Bank": "pass", "Height": "pass"}, {"NOT_MONITORED": 1, "SLOW_ACTION": 1}, 2, 10.5, 0.93),
            f({"Vfe": "pass", "Bank": "fail", "Height": "pass"}, {"NOT_MONITORED": 1}, 1, 7.0, 0.85),
            f({"Vfe": "pass", "Bank": "fail", "Height": "pass"}, {"OVERSHOOT": 1}, 1, 5.5, 0.82),
            f({"Vfe": "pass", "Bank": "pass", "Height": "fail"}, {"NOT_MONITORED": 1}, 1, 5.0, 0.80)]
    ins = insights(hist)
    kinds = {(i["kind"], (i["evidence"] or {}).get("rule")) for i in ins}
    for i in ins:
        print("     ", i["kind"], "|", i["text"])
    check("Vfe improving", ("improving", "Vfe") in kinds)
    check("Bank persistent? no -- met in the latest flight, failed 2 of last 3 -> persistent", ("persistent", "Bank") in kinds)
    check("Height regression", ("regression", "Height") in kinds)
    check("top cause = not monitoring", any(i["kind"] == "cause" and i["evidence"]["code"] == "NOT_MONITORED" for i in ins))
    check("airspeed checked more often", any(i["kind"] == "scan" and i["evidence"].get("metric") == "airspeed_check_every_s"
                                             and i["evidence"]["better"] for i in ins))
    check("outside share trend reported", any(i["evidence"].get("metric") == "outside_share" for i in ins))
    two = insights(hist[:2])
    check("2 flights: no rule trends yet (needs 3)", not any(i["kind"] in ("improving", "persistent", "regression")
                                                            for i in two), two)
    check("no breaches, 1 flight -> says there's not enough", insights([f({"Vfe": "pass"})])[0]["kind"] == "info")

    print("\n" + ("all passed" if not _fails else f"{_fails} FAILED"))
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
