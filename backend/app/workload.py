"""Eye-based workload index over a whole flight, for the Review timeline.
After the flight only (windows are centred, so each value also sees the
seconds after it); MAVIS would need a backward-looking version of its own.

Pure: data in, numbers out (no database). routers/workload.py feeds it the
session's eye samples and AOI zones and caches the result.

  1. clean     drop blinks, untracked samples (gaze direction 0,0,0 -- the live
               feed's "no lock") and samples whose gaze quality < Q_MIN
  2. fixations Smart Eye's own (fixation_id > 0, one id = one fixation); if a
               recording has none, I-VT on gaze direction (< IVT_DEG_S = fixating)
               Each fixation gets the AOI most of its samples fell in (the
               Review page's naming: an Instruments sample inside a drawn zone
               is that zone).
  3. windows   WINDOW_S long, one every STEP_S, centred on each second.
               A window with less than MIN_COVERAGE of usable samples is null.
  4. features  fix_rate      fixations per second of usable data
               mean_fix_ms   mean fixation duration (shown, not in the index:
                             it's close to 1 / fix_rate, so it would count twice)
               blink_rate    blinks per minute
               h_s           stationary entropy: how spread the looking is over
                             AOIs, -sum p_i log2 p_i (p_i = share of fixation time)
               h_t           transition entropy: how unpredictable the next AOI
                             is, -sum pi_i sum_j p_ij log2 p_ij
  5. baseline  robust z per feature: (v - median) / max(1.4826 * MAD, Z_FLOOR),
               median / MAD over the baseline span (the whole flight unless one
               is given), so 0 = usual for this pilot on this flight
  6. index     WL = sum w_k * sign_k * z_k / sum w_k  (WEIGHTS), smoothed with a
               centred SMOOTH_S moving average, banded Low / Medium / High.
               The entropies are left out (weight 0) until checked against real
               flights: whether the gaze narrows or scatters under load depends
               on the phase. They are still returned for the timeline.
"""
from __future__ import annotations

import bisect
import math
import statistics
from collections import Counter

from .incidents import _num, _t, gaze_target

WORKLOAD_VERSION = "2"
Q_MIN = 0.3               # gaze quality (0..1) below this = not usable
WINDOW_S = 20.0           # each value covers this long ...
STEP_S = 1.0              # ... one value every this long
MIN_COVERAGE = 0.6        # share of a window that must be usable samples, else null
MAX_DT = 0.2              # one sample stands for at most this long (dropouts don't count)
MIN_FIX_S = 0.06          # shorter "fixations" are noise
IVT_DEG_S = 30.0          # I-VT fallback: gaze slower than this = fixating
MIN_BLINK_GAP_S = 0.1     # blink flags closer than this are the same blink
MAX_BLINK_S = 0.5         # a longer run of blink flags isn't a blink
SMOOTH_S = 5.0            # centred moving average over the index
BANDS = ((0.5, "low"), (1.5, "medium"), (math.inf, "high"))   # WL below 0.5 low, below 1.5 medium, else high

# feature -> (direction: +1 = higher means more load, weight in the index)
WEIGHTS = {
    "fix_rate": (+1, 1.0),       # more instruments checked per second
    "blink_rate": (-1, 0.5),     # blinks held back under visual demand
    "mean_fix_ms": (-1, 0.0),    # shorter looks; ~1/fix_rate, so not counted twice
    "h_s": (-1, 0.0),            # gaze narrowing (landing study); direction unverified
    "h_t": (-1, 0.0),
}
# smallest spread a feature's baseline may have, so a very steady baseline
# can't turn a small change into a huge z
Z_FLOOR = {"fix_rate": 0.2, "blink_rate": 3.0, "mean_fix_ms": 40.0, "h_s": 0.15, "h_t": 0.15}
FEATURES = tuple(WEIGHTS)


# ---------------------------------------------------------------- 1-2 clean + fixations
def _tracked(e: dict) -> bool:
    """False while Smart Eye has no lock: the live feed then sends gaze direction
    0,0,0 (and no quality at all), not nulls."""
    d = [_num(e.get(k)) for k in ("gaze_dir_x", "gaze_dir_y", "gaze_dir_z")]
    return not (None not in d and not any(d))


def _usable(e: dict) -> bool:
    if e.get("blink") or not _tracked(e):
        return False
    q = _num(e.get("gaze_quality"))
    return q is None or q >= Q_MIN


def _dir(e: dict):
    v = [_num(e.get(k)) for k in ("gaze_dir_x", "gaze_dir_y", "gaze_dir_z")]
    if None in v:
        return None
    n = math.sqrt(sum(c * c for c in v))
    return [c / n for c in v] if n > 0 else None


def _fix_labels(eye: list[dict], ts: list[float]) -> tuple[list, str]:
    """Per sample: which fixation it belongs to (any hashable id) or None, and
    where the fixations came from ("smarteye" / "ivt" / "none")."""
    if any((_num(e.get("fixation_id")) or 0) > 0 for e in eye):
        return [int(_num(e.get("fixation_id")) or 0) or None if _usable(e) else None for e in eye], "smarteye"
    dirs = [_dir(e) for e in eye]
    if sum(d is not None for d in dirs) < 2:
        return [None] * len(eye), "none"
    labels: list = [None] * len(eye)
    fid, prev = 0, None          # prev: index of the last usable sample with a direction
    for i, e in enumerate(eye):
        if dirs[i] is None or not _usable(e):
            prev = None
            continue
        if prev is not None and 0 < ts[i] - ts[prev] <= MAX_DT:
            dot = max(-1.0, min(1.0, sum(a * b for a, b in zip(dirs[i], dirs[prev]))))
            fixating = math.degrees(math.acos(dot)) / (ts[i] - ts[prev]) < IVT_DEG_S
        else:
            fixating = False
        if fixating:
            if labels[prev] is None:
                fid += 1
                labels[prev] = fid
            labels[i] = labels[prev]
        prev = i
    return labels, "ivt"


def fixations(eye: list[dict], zones: list[dict]) -> tuple[list[dict], str]:
    """-> ([{t0, t1, aoi}], source); eye sorted by ts."""
    ts = [_t(e["ts"]) for e in eye]
    labels, source = _fix_labels(eye, ts)
    out: list[dict] = []
    cur, aois = None, Counter()
    for i, lab in enumerate(labels + [None]):
        if cur is not None and lab != cur["id"]:
            if cur["t1"] - cur["t0"] >= MIN_FIX_S:
                aoi = aois.most_common(1)[0][0] if aois else None
                out.append({"t0": cur["t0"], "t1": cur["t1"], "aoi": aoi})
            cur, aois = None, Counter()
        if lab is None:
            continue
        if cur is None:
            cur = {"id": lab, "t0": ts[i], "t1": ts[i]}
        cur["t1"] = ts[i]
        name = gaze_target(eye[i], zones)
        if name:
            aois[name] += 1
    return out, source


def blinks(eye: list[dict]) -> list[float]:
    """Start time of each blink. Only blinks with tracking just before and just
    after, and shorter than MAX_BLINK_S: around lost tracking the blink flag
    flickers, and a long "blink" is closed eyes or a lost face, not a blink."""
    out, i, n = [], 0, len(eye)
    last_end = -math.inf
    while i < n:
        if not eye[i].get("blink"):
            i += 1
            continue
        j = i
        while j + 1 < n and eye[j + 1].get("blink"):
            j += 1
        t0, t1 = _t(eye[i]["ts"]), _t(eye[j]["ts"])
        before = i > 0 and _tracked(eye[i - 1]) and not eye[i - 1].get("blink")
        after = j + 1 < n and _tracked(eye[j + 1])
        if before and after and t1 - t0 <= MAX_BLINK_S and t0 - last_end >= MIN_BLINK_GAP_S:
            out.append(t0)
        last_end = t1
        i = j + 1
    return out


# ---------------------------------------------------------------- 3-4 windows + features
def _entropy(counts) -> float | None:
    tot = sum(counts)
    if tot <= 0:
        return None
    return -sum(c / tot * math.log2(c / tot) for c in counts if c > 0)


def window_features(fix: list[dict], blink_ts: list[float], usable_s: float) -> dict:
    """Features of the fixations / blinks inside one window with usable_s seconds of usable data."""
    out = dict.fromkeys(FEATURES)
    if usable_s <= 0:
        return out
    out["fix_rate"] = len(fix) / usable_s
    out["blink_rate"] = len(blink_ts) / usable_s * 60.0
    if fix:
        out["mean_fix_ms"] = statistics.fmean(f["t1"] - f["t0"] for f in fix) * 1000.0
    dwell: Counter = Counter()
    for f in fix:
        if f["aoi"]:
            dwell[f["aoi"]] += f["t1"] - f["t0"]
    out["h_s"] = _entropy(dwell.values())
    seq = [f["aoi"] for f in fix if f["aoi"]]
    pairs: dict[str, Counter] = {}
    for a, b in zip(seq, seq[1:]):
        pairs.setdefault(a, Counter())[b] += 1
    n = sum(sum(c.values()) for c in pairs.values())
    if n:
        out["h_t"] = sum(sum(c.values()) / n * _entropy(c.values()) for c in pairs.values())
    return out


def _usable_prefix(eye: list[dict], ts: list[float]) -> list[float]:
    """cum[i] = usable seconds in samples [0, i) (each sample stands for the gap to the next, capped)."""
    cum = [0.0]
    for i, e in enumerate(eye):
        dt = min(ts[i + 1] - ts[i], MAX_DT) if i + 1 < len(ts) else 0.0
        cum.append(cum[-1] + (dt if _usable(e) and dt > 0 else 0.0))
    return cum


# ---------------------------------------------------------------- 5-6 baseline + index
def _robust(vals: list[float], floor: float) -> tuple[float, float] | None:
    if not vals:
        return None
    med = statistics.median(vals)
    mad = statistics.median(abs(v - med) for v in vals) * 1.4826
    return med, max(mad, floor)


def _r(v, nd=3):
    return None if v is None else round(v, nd)


def band_of(wl: float | None) -> str | None:
    if wl is None:
        return None
    return next(name for hi, name in BANDS if wl < hi)


def _smooth(vals: list[float | None], half: int) -> list[float | None]:
    out = []
    for i, v in enumerate(vals):
        if v is None:
            out.append(None)
            continue
        near = [x for x in vals[max(0, i - half): i + half + 1] if x is not None]
        out.append(sum(near) / len(near))
    return out


def analyse(eye: list[dict], zones: list[dict], *, baseline: tuple[float, float] | None = None,
            window_s: float = WINDOW_S, step_s: float = STEP_S) -> dict:
    """eye: samples sorted by ts (ts, aoi, gaze_point_x/y, blink, gaze_quality,
    fixation_id, gaze_dir_x/y/z). baseline: (from, to) epoch seconds of a calm
    stretch to compare against; None = the whole flight.
    -> {points: [{t, wl, wl_raw, band, coverage, <features>, z: {...}}], baseline, ...}"""
    eye = [e for e in eye if e.get("ts") is not None]
    res = {"version": WORKLOAD_VERSION, "window_s": window_s, "step_s": step_s,
           "weights": {k: {"sign": s, "weight": w} for k, (s, w) in WEIGHTS.items()},
           "bands": {"medium": BANDS[0][0], "high": BANDS[1][0]}, "points": [], "baseline": None,
           "fixation_source": None, "summary": None}
    if len(eye) < 2:
        return res
    ts = [_t(e["ts"]) for e in eye]
    fix, source = fixations(eye, zones)
    res["fixation_source"] = source
    blink_ts = blinks(eye)
    cum = _usable_prefix(eye, ts)
    mids = [(f["t0"] + f["t1"]) / 2 for f in fix]

    half = window_s / 2
    t = float(math.ceil(ts[0]))
    pts = []
    while t <= ts[-1]:
        # clipped to the recording at its two ends (at least half a window)
        a, b = max(t - half, ts[0]), min(t + half, ts[-1])
        i0, i1 = bisect.bisect_left(ts, a), bisect.bisect_left(ts, b)
        usable = cum[i1] - cum[i0]
        cov = usable / max(b - a, half)
        p = {"t": t, "coverage": round(cov, 3)}
        if cov >= MIN_COVERAGE:
            f0, f1 = bisect.bisect_left(mids, a), bisect.bisect_left(mids, b)
            k0, k1 = bisect.bisect_left(blink_ts, a), bisect.bisect_left(blink_ts, b)
            feats = window_features(fix[f0:f1], blink_ts[k0:k1], usable)
            if source == "none":       # no fixations at all: only blinks can be said
                feats = {k: (v if k == "blink_rate" else None) for k, v in feats.items()}
        else:
            feats = dict.fromkeys(FEATURES)
        p.update(feats)
        pts.append(p)
        t += step_s

    in_base = [p for p in pts if baseline is None or baseline[0] <= p["t"] <= baseline[1]]
    stats = {}
    for k in FEATURES:
        r = _robust([p[k] for p in in_base if p[k] is not None], Z_FLOOR[k])
        stats[k] = {"median": r[0], "spread": r[1]} if r else None
    res["baseline"] = {"source": "window" if baseline else "flight",
                       "from": baseline[0] if baseline else ts[0], "to": baseline[1] if baseline else ts[-1],
                       "windows": sum(1 for p in in_base if p["coverage"] >= MIN_COVERAGE), "stats": stats}

    raw = []
    for p in pts:
        z = {k: (p[k] - stats[k]["median"]) / stats[k]["spread"] if p[k] is not None and stats[k] else None
             for k in FEATURES}
        p["z"] = z
        num = den = 0.0
        for k, (sign, w) in WEIGHTS.items():
            if w > 0 and z[k] is not None:
                num += w * sign * z[k]
                den += w
        raw.append(num / den if den else None)
    smooth = _smooth(raw, max(0, round(SMOOTH_S / step_s / 2)))
    for p, r, s in zip(pts, raw, smooth):
        p["wl_raw"] = r
        p["wl"] = s
        p["band"] = band_of(s)
        for k in (*FEATURES, "wl_raw", "wl"):
            p[k] = _r(p[k])
        p["z"] = {k: _r(v) for k, v in p["z"].items()}
    res["points"] = pts

    banded = Counter(p["band"] for p in pts if p["band"])
    n = sum(banded.values())
    res["summary"] = ({"low": banded["low"] / n, "medium": banded["medium"] / n, "high": banded["high"] / n,
                       "peak": max(p["wl"] for p in pts if p["wl"] is not None)} if n else None)
    return res
