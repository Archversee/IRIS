"""Flight recorder (design spec §15.2).

Captures a normalized, downsampled per-tick series of telemetry + controls for a
whole circuit. ONE recorder serves both trainee runs (groundwork for re-scoring
against a chosen reference, §15.9) and, later, expert reference profiles (§15.4).

Slice 1: capture only. It changes no guidance or scoring behaviour - it just
records. The derived `segments` (first sample of each phase = turn-point
groundwork) and `phase_stats` (mean speed/altitude per leg) are conveniences the
ReferenceProfile layer (slice 2) will build on.
"""
from __future__ import annotations
import json
import time
from pathlib import Path

# telemetry fields copied straight from the Telemetry dict each sample
_TEL_FIELDS = ("lat", "lon", "alt_msl", "alt_agl", "ias", "vs", "hdg", "bank",
               "pitch", "flaps_deg", "throttle", "rpm", "aileron", "elevator", "rudder")


def _num(v):
    if v is None:
        return None
    try:
        return round(float(v), 5)
    except (TypeError, ValueError):
        return None


class FlightRecorder:
    def __init__(self, min_dt: float = 0.2):
        """min_dt: minimum seconds between stored samples (downsample; the tick
        loop runs ~10 Hz, we keep ~5 Hz to keep recordings small)."""
        self.min_dt = min_dt
        self.reset()

    def reset(self) -> None:
        self._samples: list[dict] = []
        self._t0: float | None = None
        self._last = -1e9

    def sample(self, tel: dict, ctrl: dict | None, geom: dict | None,
               phase: str, t: float | None = None) -> None:
        now = time.time() if t is None else t
        if self._t0 is None:
            self._t0 = now
        if now - self._last < self.min_dt:
            return
        self._last = now
        s = {k: _num(tel.get(k)) for k in _TEL_FIELDS}
        s["on_ground"] = bool(tel.get("on_ground", False))
        s["phase"] = phase
        s["along"] = _num(geom.get("along")) if geom else None
        s["cross"] = _num(geom.get("cross")) if geom else None
        s["t"] = round(now - self._t0, 2)
        # prefer raw HID stick/rudder when present (truer to what the pilot did)
        if ctrl and ctrl.get("available"):
            s["aileron"] = _num(ctrl.get("roll"))
            p = ctrl.get("pitch")
            s["elevator"] = _num(-p) if p is not None else s["elevator"]
            s["rudder"] = _num(ctrl.get("yaw"))
        self._samples.append(s)

    @property
    def n(self) -> int:
        return len(self._samples)

    def duration(self) -> float:
        return self._samples[-1]["t"] if self._samples else 0.0

    def segments(self) -> dict:
        """First sample (index + entry state) of each phase - turn-point groundwork."""
        out: dict[str, dict] = {}
        for i, s in enumerate(self._samples):
            ph = s["phase"]
            if ph not in out:
                out[ph] = {"i": i, "t": s["t"], "along": s["along"], "cross": s["cross"],
                           "alt_msl": s["alt_msl"], "ias": s["ias"]}
        return out

    def phase_stats(self) -> dict:
        """Mean airborne speed/altitude per phase - reference-target groundwork."""
        agg: dict[str, dict] = {}
        for s in self._samples:
            if s["on_ground"]:
                continue
            a = agg.setdefault(s["phase"], {"ias": [0.0, 0], "alt": [0.0, 0]})
            if s["ias"] is not None:
                a["ias"][0] += s["ias"]; a["ias"][1] += 1
            if s["alt_msl"] is not None:
                a["alt"][0] += s["alt_msl"]; a["alt"][1] += 1
        return {ph: {"ias": round(a["ias"][0] / a["ias"][1], 1) if a["ias"][1] else None,
                     "alt": round(a["alt"][0] / a["alt"][1]) if a["alt"][1] else None}
                for ph, a in agg.items()}

    def to_dict(self, meta: dict | None = None) -> dict:
        return {"meta": {**(meta or {}), "n": self.n, "duration_s": self.duration()},
                "segments": self.segments(), "phase_stats": self.phase_stats(),
                "samples": self._samples}

    def save(self, path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), separators=(",", ":")), encoding="utf-8")
