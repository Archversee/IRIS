"""Self-test for the workload index (app/workload.py) -- no database.

Run:  python -m app.workload_selftest      (from backend/)
Builds made-up eye recordings (a calm stretch, then a busy one) and checks
the index tells them apart. Exits non-zero on failure.
"""
from __future__ import annotations

import math
import random
import sys

from . import lhipa as lh
from .workload import analyse, band_of, blinks, fixations

T0 = 1_700_000_000.0
HZ = 60
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
fix, src = fixations(recording([(10, 1.0, 100, ["Airspeed", "Altimeter"])]))
check("Smart Eye fixation ids used", src == "smarteye", src)
check("one fixation per look", 9 <= len(fix) <= 11, len(fix))

fix, src = fixations(recording([(10, 1.0, 100, ["Airspeed", "Altimeter"])], smarteye=False))
check("I-VT fallback without fixation ids", src == "ivt", src)
check("I-VT finds the looks", 9 <= len(fix) <= 11, len(fix))

b = blinks(recording([(30, 1.0, 5.0, ["Attitude"])]))
check("blinks counted once each", 5 <= len(b) <= 6, len(b))

check("bands", (band_of(0.2), band_of(1.0), band_of(2.0), band_of(None)) == ("low", "medium", "high", None))

# ---------------------------------------------------------------- whole flight
res = analyse(recording([CALM, BUSY, CALM]))
calm, busy = mean_wl(res, 20, 100), mean_wl(res, 135, 165)
check("busy stretch scores higher than calm", calm is not None and busy is not None and busy > calm + 1.0,
      f"calm={calm} busy={busy}")
check("calm stretch reads low", calm is not None and calm < 0.5, calm)
check("busy stretch reads high", busy is not None and busy > 1.5, busy)
check("one value per second", 290 <= len(res["points"]) <= 302, len(res["points"]))
check("summary shares add up", abs(sum(res["summary"][k] for k in ("low", "medium", "high")) - 1) < 1e-9)

# explicit baseline: the calm part only
res_b = analyse(recording([CALM, BUSY, CALM]), baseline=(T0 + 10, T0 + 110))
check("explicit baseline used", res_b["baseline"]["source"] == "window")
check("busy still high against a calm baseline", (mean_wl(res_b, 135, 165) or 0) > 1.5, mean_wl(res_b, 135, 165))

# I-VT path gives the same story
res_ivt = analyse(recording([CALM, BUSY, CALM], smarteye=False))
check("I-VT flight: busy higher", (mean_wl(res_ivt, 135, 165) or 0) > (mean_wl(res_ivt, 20, 100) or 0) + 1.0)

# bad tracking -> null, not a number
res_q = analyse(recording([(60, 1.0, 4.0, ["Attitude", "Airspeed"])], quality=0.1))
check("low-quality data gives no index", all(p["wl"] is None for p in res_q["points"]))
check("... and says why (coverage)", all(p["coverage"] < 0.6 for p in res_q["points"]))

check("empty recording", analyse([])["points"] == [])

# no lock: the live feed sends gaze direction 0,0,0 and no quality -> null, not "low"
lost = recording([(60, 1.0, 4.0, ["Attitude", "Airspeed"])])
for e in lost:
    e.update(gaze_dir_x=0.0, gaze_dir_y=0.0, gaze_dir_z=0.0, gaze_quality=None, fixation_id=0)
check("untracked (0,0,0) data gives no index", all(p["wl"] is None for p in analyse(lost)["points"]))

# blink flags around lost tracking, or held too long, aren't blinks
seq = recording([(5, 1.0, 100, ["Attitude"])])
for e in seq[60:150]:                       # 1.5 s "blink" = eyes shut / face lost
    e["blink"] = True
for e in seq[200:206]:                      # 0.1 s blink straight into lost tracking
    e["blink"] = True
for e in seq[206:240]:
    e.update(gaze_dir_x=0.0, gaze_dir_y=0.0, gaze_dir_z=0.0)
for e in seq[260:266]:                      # a real one
    e["blink"] = True
check("only real blinks counted", len(blinks(seq)) == 1, blinks(seq))

# ---------------------------------------------------------------- LHIPA (app/lhipa.py)
rng = random.Random(7)


def pupil_rows(seconds, hz=60, drop_every=0, gap=None, q=0.8):
    """Unfiltered pupil (metres) on a gently oscillating baseline with noise.
    drop_every: drop every n-th sample (Smart Eye's single-sample dropouts);
    gap: (from_s, to_s) with no pupil at all."""
    out = []
    for i in range(int(seconds * hz)):
        t = i / hz
        mm = 3.0 + 0.2 * math.sin(2 * math.pi * 0.1 * t) + 0.05 * math.sin(2 * math.pi * 3 * t) + rng.gauss(0, 0.03)
        dropped = (drop_every and i % drop_every == 0) or (gap and gap[0] <= t < gap[1])
        out.append({"ts": T0 + t, "blink": False, "pupil_raw_m": None if dropped else mm / 1000, "pupil_raw_q": q})
    return out


def grid_of(rows):
    return lh.PupilGrid([r["ts"] for r in rows], [lh.pupil_mm(r) for r in rows])


check("pupil read in mm from metres", abs(lh.pupil_mm({"pupil_raw_m": 0.003, "pupil_raw_q": 0.9}) - 3.0) < 1e-9)
check("low-quality pupil ignored", lh.pupil_mm({"pupil_raw_m": 0.003, "pupil_raw_q": 0.1}) is None)
check("implausible pupil ignored", lh.pupil_mm({"pupil_raw_m": 0.02}) is None and lh.pupil_mm({"pupil_raw_m": 0.0}) is None)
check("blinking pupil ignored", lh.pupil_mm({"pupil_raw_m": 0.003, "blink": True}) is None)

v, cov = lh.window_lhipa(grid_of(pupil_rows(40)), T0 + 20)
check("LHIPA of a clean 30 s window", v is not None and v > 0 and cov > 0.99, (v, cov))
v, cov = lh.window_lhipa(grid_of(pupil_rows(40, drop_every=4)), T0 + 20)
check("single dropped samples are bridged", v is not None and cov > 0.99, (v, cov))
v, cov = lh.window_lhipa(grid_of(pupil_rows(40, gap=(15, 17))), T0 + 20)
check("a 2 s gap: still enough pupil", v is not None and 0.85 <= cov < 0.95, (v, cov))
v, cov = lh.window_lhipa(grid_of(pupil_rows(40, gap=(10, 18))), T0 + 20)
check("an 8 s gap: no LHIPA", v is None and cov < 0.85, (v, cov))
check("too short a recording: no LHIPA", lh.window_lhipa(grid_of(pupil_rows(5)), T0 + 2.5)[0] is None)

# in the index: present with pupil data, absent (and harmless) without
flight = recording([CALM, BUSY, CALM])
pup = pupil_rows(300)                      # same 60 Hz clock as recording(): match by sample number
for e in flight:
    k = round((e["ts"] - T0) * HZ)
    r = pup[k] if k < len(pup) else None
    if r:
        e["pupil_raw_m"], e["pupil_raw_q"] = r["pupil_raw_m"], r["pupil_raw_q"]
res_p = analyse(flight)
check("pupil source reported", res_p["pupil_source"] == "PupilDiameter", res_p["pupil_source"])
check("LHIPA in the points", sum(p["lhipa"] is not None for p in res_p["points"]) > 200,
      sum(p["lhipa"] is not None for p in res_p["points"]))
check("no pupil data: no LHIPA, index still works", res["pupil_source"] is None
      and all(p["lhipa"] is None for p in res["points"]) and mean_wl(res, 135, 165) > 1.5)
check("LHIPA alone gives no score", all(p["wl"] is None for p in res_p["points"] if p["coverage"] < 0.6))

# the baseline ("usual") comes from scored windows only, LHIPA's too: unscored
# stretches with pupil data (pilot just sat down) mustn't shift it
lost_gaze = [dict(e) for e in flight]
for e in lost_gaze[:60 * HZ]:                # first minute: pupil fine, gaze lost
    e.update(gaze_dir_x=0.0, gaze_dir_y=0.0, gaze_dir_z=0.0, fixation_id=0)
for e in lost_gaze[:60 * HZ]:
    if e.get("pupil_raw_m"):                 # a very different pupil activity while unscored
        e["pupil_raw_m"] += rng.choice((-1, 1)) * rng.uniform(0.2, 0.6) / 1000
res_lg = analyse(lost_gaze)
scored = [p for p in res_lg["points"] if p["coverage"] >= 0.6 and p["lhipa"] is not None]
import statistics as _st  # noqa: E402
every = [p["lhipa"] for p in res_lg["points"] if p["lhipa"] is not None]
base = res_lg["baseline"]["stats"]["lhipa"]["median"]
check("LHIPA baseline ignores unscored windows",
      abs(base - _st.median(p["lhipa"] for p in scored)) < 2e-3 and abs(base - _st.median(every)) > 0.01,
      (base, _st.median(p["lhipa"] for p in scored), _st.median(every)))

print(f"\n{'ALL PASSED' if not _fails else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
