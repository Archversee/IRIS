"""Comparing a flight with an expert's (design spec §15.9): the scorecard's
"Compare against" and saving an instructor's flight as a reference.

Flights are matched by WHERE the aircraft is in the circuit, not by time --
a trainee who flies slower than the expert is still compared leg by leg:
each of the trainee's samples is paired with the nearest point of the
expert's flight on the same leg, in runway coordinates (along / cross, m).

A reference saved from a real flight keeps those points (`samples`: leg,
along, cross, speed, height), so speed and height are compared point by
point. The demo runs (Maverick / Iceman) only have a drawn path and a target
speed per leg (`targets`), so for them the path is compared point by point
and speed / circuit height against those targets.
"""
from __future__ import annotations

import math

CIRCUIT = ("CLIMB_OUT", "CROSSWIND", "DOWNWIND", "BASE", "FINAL")
_LEG = {"CLIMB_OUT": "climb-out", "CROSSWIND": "crosswind", "DOWNWIND": "downwind",
        "BASE": "base", "FINAL": "final"}


def _curve(err: float, scale: float) -> float:
    return 100.0 * math.exp(-(max(0.0, err) / scale) ** 2) if scale > 0 else 0.0


def _airborne_circuit(samples: list[dict]) -> list[dict]:
    return [s for s in samples or []
            if not s.get("on_ground") and s.get("phase") in CIRCUIT
            and s.get("along") is not None and s.get("cross") is not None]


# ---------------------------------------------------------------- saving
def reference_from_recording(rec: dict, min_spacing_m: float = 30.0) -> dict:
    """The parts of a recorded flight a reference keeps: its path, a point
    every ~30 m around the circuit (leg, position, speed, height), the mean
    speed per leg (coaching targets) and where it turned base / final."""
    pts = _airborne_circuit(rec.get("samples"))
    samples, track, last = [], [], None
    for s in pts:
        if last and math.hypot(s["along"] - last["along"], s["cross"] - last["cross"]) < min_spacing_m:
            continue
        last = s
        samples.append({"phase": s["phase"], "along": round(s["along"], 1), "cross": round(s["cross"], 1),
                        "lat": round(s["lat"], 6) if s.get("lat") is not None else None,
                        "lon": round(s["lon"], 6) if s.get("lon") is not None else None,
                        "ias": round(s["ias"] or 0.0, 1), "alt": round(s["alt_msl"] or 0.0)})
        if s.get("lat") is not None:
            track.append([round(s["lat"], 6), round(s["lon"], 6)])
    stats = rec.get("phase_stats") or {}
    targets = {ph: {"ias": round(st["ias"])} for ph, st in stats.items()
               if ph in CIRCUIT and st.get("ias")}
    if (stats.get("DOWNWIND") or {}).get("alt"):
        targets.setdefault("DOWNWIND", {})["alt"] = round(stats["DOWNWIND"]["alt"])
    seg = rec.get("segments") or {}
    turn_points = {}
    # the phase changes once the turn is well under way: the cue comes a
    # little before where this pilot's turn showed
    if (seg.get("BASE") or {}).get("along") is not None:
        turn_points["TURN_BASE"] = {"along_max": round(seg["BASE"]["along"] + 250)}
    if (seg.get("FINAL") or {}).get("cross") is not None:
        turn_points["TURN_FINAL"] = {"cross_max": round(abs(seg["FINAL"]["cross"]) + 250)}
    return {"track": track, "samples": samples, "targets": targets, "turn_points": turn_points}


# ---------------------------------------------------------------- comparing
def _ref_points(ref, geometry) -> tuple[list[dict], bool]:
    """(points, detailed): the expert's flight as points in runway
    coordinates. detailed = real speed / height per point (a recorded
    flight); else only the drawn path (demo runs)."""
    if getattr(ref, "samples", None):
        # positions from lat/lon in TODAY's runway frame: a reference recorded
        # off runway 20 lines up with a flight off runway 02 only by location
        pts = []
        for p in ref.samples:
            if p.get("lat") is not None:
                p = {**p, **dict(zip(("along", "cross"), geometry(p["lat"], p["lon"])))}
            pts.append(p)
        return pts, True
    pts = []
    for lat, lon in ref.track or []:
        g = geometry(lat, lon)
        pts.append({"phase": None, "along": g[0], "cross": g[1], "ias": None, "alt": None})
    return pts, False


def compare(rec: dict, ref, geometry, scales: dict, weights: dict, f: float = 1.0) -> dict:
    """How closely a recorded flight (core/recorder.py to_dict) followed an
    expert reference (ReferenceProfile). geometry(lat, lon) -> (along, cross).

    Returns {similarity, scores {track, speed, altitude}, notes [...], detailed}."""
    mine = _airborne_circuit(rec.get("samples"))
    ref_pts, detailed = _ref_points(ref, geometry)
    if not mine or not ref_pts:
        return {"similarity": None, "scores": {}, "notes": ["Not enough of the circuit was flown to compare."],
                "detailed": detailed}
    by_leg: dict[str, list[dict]] = {}
    for p in ref_pts:
        by_leg.setdefault(p["phase"], []).append(p)
    targets = getattr(ref, "targets", None) or {}
    track_err, spd_err, alt_err = [], [], []
    delta: dict[str, dict[str, list[float]]] = {}       # per leg: cross / along / ias / alt differences
    for s in mine:
        pool = by_leg.get(s["phase"]) if detailed else None
        pool = pool or ref_pts
        near = min(pool, key=lambda p: (p["along"] - s["along"]) ** 2 + (p["cross"] - s["cross"]) ** 2)
        track_err.append(math.hypot(near["along"] - s["along"], near["cross"] - s["cross"]))
        d = delta.setdefault(s["phase"], {"cross": [], "along": [], "ias": [], "alt": []})
        d["cross"].append(s["cross"] - near["cross"])
        d["along"].append(s["along"] - near["along"])
        ref_ias = near["ias"] if detailed else (targets.get(s["phase"]) or {}).get("ias")
        if ref_ias and s.get("ias") is not None:
            spd_err.append(abs(s["ias"] - ref_ias))
            d["ias"].append(s["ias"] - ref_ias)
        ref_alt = (near["alt"] if detailed and s["phase"] in ("CROSSWIND", "DOWNWIND", "BASE", "FINAL")
                   else (targets.get("DOWNWIND") or {}).get("alt") if s["phase"] == "DOWNWIND" else None)
        if ref_alt and s.get("alt_msl") is not None:
            alt_err.append(abs(s["alt_msl"] - ref_alt))
            d["alt"].append(s["alt_msl"] - ref_alt)

    mean = lambda xs: sum(xs) / len(xs) if xs else None
    scores = {"track": round(_curve(mean(track_err), scales["track"] * f))}
    if spd_err:
        scores["speed"] = round(_curve(mean(spd_err), scales["speed"] * f))
    if alt_err:
        scores["altitude"] = round(_curve(mean(alt_err), scales["altitude"] * f))
    w = {k: max(1.0, float(weights.get(k, 1) or 1)) for k in scores}
    similarity = round(sum(scores[k] * w[k] for k in scores) / sum(w.values()))
    return {"similarity": similarity, "scores": scores, "notes": _notes(delta), "detailed": detailed}


def _notes(delta: dict) -> list[str]:
    """The two or three biggest differences, in words: "downwind 150 m
    wider", "final 6 kt faster", "downwind 80 ft higher"."""
    found = []      # (weight, text)
    mean = lambda xs: sum(xs) / len(xs) if len(xs) >= 5 else None
    for leg, d in delta.items():
        name = _LEG.get(leg, leg.lower())
        c = mean(d["cross"])
        if leg in ("DOWNWIND", "CROSSWIND") and c is not None and abs(c) >= 60:
            found.append((abs(c) / 75, f"{name} {abs(round(c, -1)):.0f} m {'wider' if c > 0 else 'closer in'}"))
        if leg in ("FINAL", "CLIMB_OUT") and c is not None and abs(c) >= 30:
            found.append((abs(c) / 40, f"{name} {abs(round(c, -1)):.0f} m off their line"))
        a = mean(d["along"])
        if leg == "BASE" and a is not None and abs(a) >= 80:
            found.append((abs(a) / 100, f"base turned {abs(round(a, -1)):.0f} m "
                                         f"{'earlier' if a > 0 else 'further out'}"))
        v = mean(d["ias"])
        if v is not None and abs(v) >= 4:
            found.append((abs(v) / 5, f"{name} {abs(v):.0f} kt {'faster' if v > 0 else 'slower'}"))
        h = mean(d["alt"])
        if h is not None and abs(h) >= 50:
            found.append((abs(h) / 60, f"{name} {abs(round(h, -1)):.0f} ft {'higher' if h > 0 else 'lower'}"))
    found.sort(key=lambda x: -x[0])
    return [t for _, t in found[:3]] or ["Very close to their circuit all the way round."]
