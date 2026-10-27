"""Self-test for the workload index (app/workload.py) -- no database.

Run:  python -m app.workload_selftest      (from backend/)
Builds made-up eye recordings (a calm stretch, then a busy one) and checks
the index tells them apart. Exits non-zero on failure.
"""
from __future__ import annotations

import math
import sys

from .workload import analyse, band_of, blinks, fixations, window_features

T0 = 1_700_000_000.0
HZ = 60
ZONES = [{"name": "Airspeed", "x1": 0, "y1": 0, "x2": 100, "y2": 100},
         {"name": "Altimeter", "x1": 200, "y1": 0, "x2": 300, "y2": 100},
         {"name": "Attitude", "x1": 400, "y1": 0, "x2": 500, "y2": 100}]
SPOT = {"Airspeed": (50, 50), "Altimeter": (250, 50), "Attitude": (450, 50)}
DIR = {"Airspeed": (-0.3, 0.0), "Altimeter": (0.0, 0.0), "Attitude": (0.3, 0.0), "OTW": (0.0, 0.4)}

_fails = 0


def check(name, cond, extra=""):
    global _fails
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    _fails += 0 if cond else 1


def recording(segments, *, smarteye=True, quality=0.9):
    """segments: [(seconds, look_s, blink_every_s, [aoi cycle])] -> eye samples.
    Looks of look_s at each AOI in turn, a 40 ms saccade between them, a
    150 ms blink every blink_every_s."""
    rows, t, fid = [], 0.0, 0
    for dur, look_s, blink_every, cycle in segments:
        end, k, next_blink = t + dur, 0, t + blink_every
        while t < end:
            aoi = cycle[k % len(cycle)]
            k += 1
            fid += 1
            look_end = min(t + look_s, end)
            while t < look_end:
                blink = next_blink <= t < next_blink + 0.15
                if t >= next_blink + 0.15:
                    next_blink += blink_every
                x, y = SPOT.get(aoi, (960, 540))
                dx, dy = DIR[aoi]
                rows.append({"ts": T0 + t, "aoi": "OTW" if aoi == "OTW" else "Instruments",
                             "gaze_point_x": x, "gaze_point_y": y, "blink": blink, "gaze_quality": quality,
                             "fixation_id": (0 if blink else fid) if smarteye else None,
                             "gaze_dir_x": dx, "gaze_dir_y": dy, "gaze_dir_z": 1.0})
                t = round(t + 1 / HZ, 6)
            sac_end = t + 0.04                  # saccade: direction sweeps, no fixation id
            while t < sac_end and t < end:
                rows.append({"ts": T0 + t, "aoi": None, "gaze_point_x": None, "gaze_point_y": None, "blink": False,
                             "gaze_quality": quality, "fixation_id": 0 if smarteye else None,
                             "gaze_dir_x": 2.0 * math.sin(t * 50), "gaze_dir_y": 1.0, "gaze_dir_z": 0.2})
                t = round(t + 1 / HZ, 6)
    return rows


CALM = (120, 1.2, 4.0, ["Attitude", "OTW", "Attitude", "Altimeter", "Attitude", "Airspeed"])
BUSY = (60, 0.35, 12.0, ["Attitude", "Airspeed", "Attitude", "Altimeter"])


def mean_wl(res, lo, hi):
    v = [p["wl"] for p in res["points"] if lo <= p["t"] - T0 <= hi and p["wl"] is not None]
    return sum(v) / len(v) if v else None


# ---------------------------------------------------------------- pieces
fix, src = fixations(recording([(10, 1.0, 100, ["Airspeed", "Altimeter"])]), ZONES)
check("Smart Eye fixation ids used", src == "smarteye", src)
check("one fixation per look", 9 <= len(fix) <= 11, len(fix))
check("fixation AOI from the drawn zones", fix[0]["aoi"] == "Airspeed" and fix[1]["aoi"] == "Altimeter",
      [f["aoi"] for f in fix[:2]])

fix, src = fixations(recording([(10, 1.0, 100, ["Airspeed", "Altimeter"])], smarteye=False), ZONES)
check("I-VT fallback without fixation ids", src == "ivt", src)
check("I-VT finds the looks", 9 <= len(fix) <= 11, len(fix))

b = blinks(recording([(30, 1.0, 5.0, ["Attitude"])]))
check("blinks counted once each", 5 <= len(b) <= 6, len(b))

one = window_features([{"t0": 0, "t1": 1, "aoi": "A"}, {"t0": 1, "t1": 2, "aoi": "B"}], [], 2.0)
check("stationary entropy of two equal AOIs = 1 bit", abs(one["h_s"] - 1.0) < 1e-9, one["h_s"])
alt = window_features([{"t0": i, "t1": i + 1, "aoi": "AB"[i % 2]} for i in range(8)], [], 8.0)
check("strict alternation has transition entropy 0", alt["h_t"] == 0.0, alt["h_t"])

check("bands", (band_of(0.2), band_of(1.0), band_of(2.0), band_of(None)) == ("low", "medium", "high", None))

# ---------------------------------------------------------------- whole flight
res = analyse(recording([CALM, BUSY, CALM]), ZONES)
calm, busy = mean_wl(res, 20, 100), mean_wl(res, 135, 165)
check("busy stretch scores higher than calm", calm is not None and busy is not None and busy > calm + 1.0,
      f"calm={calm} busy={busy}")
check("calm stretch reads low", calm is not None and calm < 0.5, calm)
check("busy stretch reads high", busy is not None and busy > 1.5, busy)
check("one value per second", 290 <= len(res["points"]) <= 302, len(res["points"]))
check("summary shares add up", abs(sum(res["summary"][k] for k in ("low", "medium", "high")) - 1) < 1e-9)

# explicit baseline: the calm part only
res_b = analyse(recording([CALM, BUSY, CALM]), ZONES, baseline=(T0 + 10, T0 + 110))
check("explicit baseline used", res_b["baseline"]["source"] == "window")
check("busy still high against a calm baseline", (mean_wl(res_b, 135, 165) or 0) > 1.5, mean_wl(res_b, 135, 165))

# I-VT path gives the same story
res_ivt = analyse(recording([CALM, BUSY, CALM], smarteye=False), ZONES)
check("I-VT flight: busy higher", (mean_wl(res_ivt, 135, 165) or 0) > (mean_wl(res_ivt, 20, 100) or 0) + 1.0)

# bad tracking -> null, not a number
res_q = analyse(recording([(60, 1.0, 4.0, ["Attitude", "Airspeed"])], quality=0.1), ZONES)
check("low-quality data gives no index", all(p["wl"] is None for p in res_q["points"]))
check("... and says why (coverage)", all(p["coverage"] < 0.6 for p in res_q["points"]))

check("empty recording", analyse([], ZONES)["points"] == [])

print(f"\n{'ALL PASSED' if not _fails else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
