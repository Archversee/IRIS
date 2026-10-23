"""Self-test for the Progress logic (app/progress.py) -- no database.

Run:  python -m app.progress_selftest      (from backend/)
"""
from __future__ import annotations

import math
import sys

from .progress import (_reversals, coaching_summary, insights, learning_curve, phase_scan_table, pilot_of,
                       rule_impact, session_stats)

_fails = 0
T0 = 1_700_000_000.0


def check(name, cond, extra=""):
    global _fails
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    _fails += 0 if cond else 1


ZONES = [{"name": "Airspeed", "x1": 0, "y1": 0, "x2": 100, "y2": 100},
         {"name": "Altimeter", "x1": 200, "y1": 0, "x2": 300, "y2": 100}]


def flight(phases, wobble=0.0, jerky=False):
    """[(start s, phase)] -> 5 Hz rows: climbs out, level downwind at 1000 ft / 85 kt, lands at the end"""
    rows = []
    end = phases[-1][0] + 10
    for (start, ph), nxt in zip(phases, phases[1:] + [(end, None)]):
        t = start
        while t < nxt[0]:
            ias = 85 + wobble * math.sin(t / 3)
            stick = (0.2 if int(t * 2) % 2 else -0.2) if jerky else 0.02 * math.sin(t / 20)
            rows.append({"ts": T0 + t, "phase": ph, "airspeed_kt": ias, "altitude_ft": 1000 + wobble * 5 * math.sin(t / 7),
                         "vertical_speed_fpm": -500.0 if ph == "FINAL" else 0.0, "bank_deg": 0.0, "pitch_deg": 2.0,
                         "throttle_pct": 60.0, "elevator_position": stick, "aileron_position": 0.0,
                         "rudder_position": 0.0, "flaps_deg": 10.0, "on_ground": ph in ("ROLLOUT", "TAKEOFF_ROLL")})
            t = round(t + 0.2, 3)
    return rows


def eye(span, where):
    rows, t = [], span[0]
    while t < span[1]:
        w = where(t)
        e = {"ts": T0 + t, "blink": False}
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

    check("reversals: smooth sine = 2 per cycle", _reversals([0.3 * math.sin(i / 20) for i in range(300)], 0.04) <= 5)
    check("reversals: back-and-forth = many", _reversals([0.2 if i % 2 else -0.2 for i in range(100)], 0.04) >= 90)
    check("reversals: noise below the step = none", _reversals([0.01 * (i % 2) for i in range(100)], 0.04) == 0)

    phases = [(0, "CLIMB_OUT"), (60, "DOWNWIND"), (120, "BASE"), (150, "FINAL"), (190, "ROLLOUT")]

    def where(t):
        period = 4 if 120 <= t < 190 else 10
        if (t % period) < 0.5:
            return "Airspeed"
        if (t % 7) < 0.5:
            return "Altimeter"
        return "OTW"
    callouts = [{"ts": T0 + 160, "code": "SPEED_FAST", "severity": "nudge", "who": "instructor", "phase": "FINAL"},
                {"ts": T0 + 100, "code": "ALT_HIGH", "severity": "nudge", "who": "instructor", "phase": "DOWNWIND"},
                {"ts": T0 + 30, "code": None, "severity": None, "who": "instructor", "phase": "CLIMB_OUT"}]
    fl = flight(phases)
    for r in fl:                                   # after the speed callout: throttle back, speed drops
        if T0 + 162 <= r["ts"] <= T0 + 190:
            r["throttle_pct"] = 40.0
            r["airspeed_kt"] = 85 - min(10, (r["ts"] - T0 - 162) * 1.0)
    fl[-1]["on_ground"] = True
    s = session_stats(fl, eye((0, 195), where), ZONES, callouts)
    final_scan = s["phases"]["FINAL"]["scan"]
    dw = s["phases"]["DOWNWIND"]
    check("final: airspeed checked about every 4 s", abs(final_scan["check_every_s"]["airspeed"] - 4) <= 0.6, final_scan)
    check("downwind: airspeed about every 10 s", abs(dw["scan"]["check_every_s"]["airspeed"] - 10) <= 1, dw["scan"])
    check("outside share ~80-90 %", 0.75 <= dw["scan"]["share"]["outside"] <= 0.92, dw["scan"]["share"])
    check("spread between 0 and 1", 0 < dw["scan"]["spread"] < 1, dw["scan"]["spread"])
    check("downwind height steady (sd ~0)", dw["handling"]["alt_sd"] is not None and dw["handling"]["alt_sd"] < 1, dw)
    check("climb has no height-steadiness number", s["phases"]["CLIMB_OUT"]["handling"]["alt_sd"] is None)
    check("approach numbers present", s["approach"] and s["approach"]["vs_mean"] == -500, s["approach"])
    check("landing found at touchdown, sink rate from just before", s["landing"] and s["landing"]["vs_fpm"] == -500,
          s["landing"])
    co = {c["code"]: c for c in s["callouts"]}
    check("callout without a code is skipped", len(s["callouts"]) == 2, s["callouts"])
    check("speed callout: throttle reaction ~2 s", co["SPEED_FAST"]["reaction_s"] == 2.0, co["SPEED_FAST"])
    check("speed callout: helped (speed dropped)", co["SPEED_FAST"]["helped"] is True)
    check("height callout: ignored (no input, no change)", co["ALT_HIGH"]["reaction_s"] is None
          and co["ALT_HIGH"]["helped"] is False, co["ALT_HIGH"])
    import random
    random.seed(3)
    jit = flight(phases)
    for r in jit:
        r["elevator_position"] = random.uniform(-0.04, 0.04)
    sj = session_stats(jit, [], ZONES, [callouts[1]])
    check("stick jitter isn't a reaction", sj["callouts"][0]["reaction_s"] is None, sj["callouts"])
    for r in jit:
        r["elevator_position"] = 0.12 * math.sin((r["ts"] - T0) * 2.3)
    sj = session_stats(jit, [], ZONES, [callouts[1]])
    check("a constantly busy stick isn't a reaction", sj["callouts"][0]["reaction_s"] is None, sj["callouts"])
    summ = {c["code"]: c for c in coaching_summary(s["callouts"])}
    check("coaching summary counts ignored", summ["ALT_HIGH"]["ignored"] == 1 and summ["SPEED_FAST"]["helped"] == 1)

    wobbly = session_stats(flight(phases, wobble=8.0, jerky=True), [], ZONES, [])
    check("wobbly flight: bigger speed spread", wobbly["phases"]["DOWNWIND"]["handling"]["ias_sd"] > 4)
    check("jerky stick: many reversals/min", wobbly["phases"]["DOWNWIND"]["handling"]["reversals_per_min"]["elevator"] > 50)
    check("no eye data: no scan, no coverage", "scan" not in wobbly["phases"]["DOWNWIND"] and wobbly["eye_coverage"] is None)

    def h(at, verdicts, causes=None, breaches=0):
        return {"at": at, "rules": {t: {"verdict": v, "breaches": 1 if v == "fail" else 0} for t, v in verdicts.items()},
                "causes": causes or {}, "breaches": breaches}
    hist = [h("2026-01-01T09:00:00", {"Vfe": "fail", "Bank": "pass", "Height": "pass"}, {"NOT_MONITORED": 1}, 1),
            h("2026-01-02T09:00:00", {"Vfe": "fail", "Bank": "pass", "Height": "pass"}, {"NOT_MONITORED": 1}, 1),
            h("2026-01-15T09:00:00", {"Vfe": "pass", "Bank": "fail", "Height": "fail"}, {"NOT_MONITORED": 2}, 2),
            h("2026-01-16T09:00:00", {"Vfe": "pass", "Bank": "pass", "Height": "pass"}),
            h("2026-01-30T09:00:00", {"Vfe": "pass", "Bank": "fail", "Height": "fail"}, {"SLOW_ACTION": 2}, 2)]
    lc = {r["title"]: r for r in learning_curve(hist)}
    check("learning curve: Vfe broken twice in a row", lc["Vfe"]["repeats"] == 2 and lc["Vfe"]["broken"] == 2)
    check("relapses after a break counted", lc["Bank"]["relapses"]["after_break"] == 2, lc["Bank"]["relapses"])
    ins = insights(hist, [s, wobbly, wobbly], phase_scan_table([s]))
    for i in ins:
        print("     ", i["kind"], "|", i["text"])
    kinds = {i["kind"] for i in ins}
    check("forgetting between lessons spotted", "forgetting" in kinds)
    check("Vfe improving", any(i["kind"] == "improving" and "Vfe" in i["text"] for i in ins))
    check("top cause", "cause" in kinds)
    check("handling vs expert (wobbly speed)", any(i["kind"] == "handling" and "wanders" in i["text"] for i in ins))

    imp = rule_impact([
        {"pilot": "A", "rules": {"Vfe": {"verdict": "fail", "severity": "warning", "breach_t": [100.0]},
                                 "Bank": {"verdict": "fail", "severity": "caution", "breach_t": [130.0]},
                                 "Radio": {"verdict": "pass", "severity": "advisory", "breach_t": []}}},
        {"pilot": "B", "rules": {"Vfe": {"verdict": "fail", "severity": "warning", "breach_t": [50.0]},
                                 "Bank": {"verdict": "pass", "severity": "caution", "breach_t": []},
                                 "Radio": {"verdict": "fail", "severity": "advisory", "breach_t": [10.0]}}}])
    check("rule impact: Vfe first", imp[0]["title"] == "Vfe", imp)
    check("rule impact: Vfe knock-on 50 %", imp[0]["knock_on"] == 0.5, imp[0])

    print("\n" + ("all passed" if not _fails else f"{_fails} FAILED"))
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
