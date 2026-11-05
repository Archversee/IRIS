"""LHIPA -- the Low/High Index of Pupillary Activity (Duchowski et al., CHI 2020)
over windows of a flight, for the workload index (app/workload.py).

How the pupil oscillates rather than how big it is: the pupil signal is
wavelet-transformed (sym16), the low-frequency detail is divided by the
high-frequency detail, and the number of sharp peaks in that ratio per second
is LHIPA. Higher load -> fewer peaks -> lower LHIPA. Screen brightness moves
the pupil's size more than its oscillation, so it matters less than for raw
pupil size.

The signal is Smart Eye's UNFILTERED combined pupil diameter (`PupilDiameter`
in eye_tracking_data.extra): the filtered one smooths away the fast
oscillation LHIPA measures and repeats its last value through dropouts.

  1. clean    keep samples with a pupil, quality >= Q_MIN, not blinking, and a
              plausible size (MIN_MM..MAX_MM)
  2. grid     resample onto an even grid at the recording's own rate, joining
              gaps up to BRIDGE_S with a straight line (the unfiltered pupil
              drops single samples all the time); longer gaps stay empty
  3. window   LHIPA over WINDOW_S around each point, only when at least
              MIN_COVERAGE of the window is real pupil data (the rest, gaps
              longer than BRIDGE_S, is joined up for the transform alone)

Pure: no database. Needs numpy + PyWavelets.
"""
from __future__ import annotations

import math

import numpy as np
import pywt

WAVELET = "sym16"
Q_MIN = 0.3               # Smart Eye's PupilDiameterQ (0..1) below this = not a pupil reading
MIN_MM, MAX_MM = 1.5, 9.0
BRIDGE_S = 0.5            # gaps up to this long are joined with a straight line
WINDOW_S = 30.0           # LHIPA needs a longer stretch than the other features
MIN_COVERAGE = 0.85       # share of a window that must be real pupil data


def pupil_mm(e: dict) -> float | None:
    """The sample's unfiltered pupil diameter in mm, or None when it isn't a usable reading."""
    if e.get("blink"):
        return None
    try:
        d = float(e.get("pupil_raw_m"))
    except (TypeError, ValueError):
        return None
    q = e.get("pupil_raw_q")
    if q is not None and float(q) < Q_MIN:
        return None
    mm = d * 1000.0
    return mm if MIN_MM <= mm <= MAX_MM else None


class PupilGrid:
    """The cleaned pupil signal on an even time grid: t0 + i / hz.
    value[i] = diameter (mm), joined across short gaps; real[i] = True where
    that comes from (or between) samples no further apart than BRIDGE_S."""

    def __init__(self, ts: list[float], mm: list[float | None]):
        pts = [(t, v) for t, v in zip(ts, mm) if v is not None]
        self.hz = 0.0
        self.t0 = 0.0
        self.value = np.zeros(0)
        self.real = np.zeros(0, dtype=bool)
        if len(pts) < 2:
            return
        t = np.array([p[0] for p in pts])
        v = np.array([p[1] for p in pts])
        dt = np.diff(np.array(ts))
        dt = dt[dt > 0]
        self.hz = float(round(1.0 / np.median(dt))) if len(dt) else 60.0
        self.t0 = float(t[0])
        grid = self.t0 + np.arange(int((t[-1] - t[0]) * self.hz) + 1) / self.hz
        self.value = np.interp(grid, t, v)
        # a grid point is real when the samples either side of it are close enough
        right = np.clip(np.searchsorted(t, grid, side="left"), 0, len(t) - 1)
        left = np.clip(right - 1, 0, len(t) - 1)
        exact = np.abs(t[right] - grid) <= 0.5 / self.hz      # (not np.isclose: its relative
        self.real = exact | ((t[right] - t[left]) <= BRIDGE_S)  #  tolerance is hours at epoch scale)

    def window(self, a: float, b: float) -> tuple[np.ndarray, float]:
        """(signal, share of it that is real) between epoch seconds a and b."""
        if not len(self.value):
            return np.zeros(0), 0.0
        i0 = max(0, int(math.ceil((a - self.t0) * self.hz)))
        i1 = min(len(self.value), int(math.floor((b - self.t0) * self.hz)) + 1)
        if i1 <= i0:
            return np.zeros(0), 0.0
        # share of the whole window (one clipped at the recording's ends counts the missing part as no data)
        share = min(1.0, (i1 - i0) / max(1.0, (b - a) * self.hz))
        return self.value[i0:i1], float(self.real[i0:i1].mean()) * share


def _modmax(d: np.ndarray) -> np.ndarray:
    """Modulus maxima: |d| where it's a local peak, else 0 (the reference
    implementation's edge handling kept as is)."""
    m = np.abs(d)
    n = len(m)
    ll = np.concatenate(([m[0]], m[:-1]))
    rr = m.copy()
    if n > 2:
        rr[:n - 2] = m[1:n - 1]
    peak = (ll <= m) & (m >= rr) & ((ll < m) | (m > rr))
    return np.where(peak, m, 0.0)


def lhipa(signal: np.ndarray, duration_s: float) -> float | None:
    """LHIPA of one evenly-sampled pupil signal (mm) lasting duration_s; None if too short."""
    w = pywt.Wavelet(WAVELET)
    maxlevel = pywt.dwt_max_level(len(signal), filter_len=w.dec_len)
    hif, lof = 1, int(maxlevel / 2)
    if lof <= hif or duration_s <= 0:
        return None
    cd_h = pywt.downcoef("d", signal, WAVELET, "per", level=hif) / math.sqrt(2 ** hif)
    cd_l = pywt.downcoef("d", signal, WAVELET, "per", level=lof) / math.sqrt(2 ** lof)
    idx = np.minimum(int(2 ** lof / 2 ** hif) * np.arange(len(cd_l)), len(cd_h) - 1)
    hi = cd_h[idx]
    cd_lh = np.divide(cd_l, hi, out=np.zeros_like(cd_l), where=hi != 0)
    m = _modmax(cd_lh)
    if len(m) < 2:
        return None
    luniv = float(np.std(m)) * math.sqrt(2.0 * math.log2(len(m)))
    kept = pywt.threshold(m, luniv, mode="less")
    return float(np.count_nonzero(kept)) / duration_s


def window_lhipa(grid: PupilGrid, centre: float, window_s: float = WINDOW_S) -> tuple[float | None, float]:
    """(LHIPA, coverage) of the window_s around `centre`; LHIPA is None below MIN_COVERAGE."""
    a, b = centre - window_s / 2, centre + window_s / 2
    sig, cov = grid.window(a, b)
    if cov < MIN_COVERAGE or len(sig) < 64:
        return None, cov
    return lhipa(sig, len(sig) / grid.hz), cov
