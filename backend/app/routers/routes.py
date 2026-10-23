"""MAVIS preset routes (planned circuits) for the Review ground track.

  GET /routes  -> [{key, lesson_id, lesson_ids, label, runway_ident, path: [[lat, lon], ...],
                    runway: [[lat, lon], [lat, lon]]}]

Read straight from MAVIS's lesson files (MAVIS/ai-instructor/lessons/*.yaml +
catalog.json) so Review works without MAVIS running. The circuit is built
the same way MAVIS's own map draws it (frontend/src/live/components/
MapCanvas.jsx): runway-relative along/cross corners, rounded turns, then
converted to lat/lon. Each lesson is offered from both runway ends (e.g. RWY
02 and RWY 20), as MAVIS flips the lesson to whichever end you take off from.
Only full circuits are offered (not the airborne exercises, nor Take-off /
Landing, which fly one piece of it). A lesson that flies the very same circuit
as one already listed (e.g. the Night Circuit) isn't listed again; it's added
to that route's lesson_ids, which is how a session finds its own circuit.
"""
import json
import logging
import math
from pathlib import Path

import yaml
from fastapi import APIRouter

from ..config import settings

log = logging.getLogger(__name__)
router = APIRouter(tags=["routes"])

M_PER_DEG = 111320.0
CORNER_RADIUS_M = 220  # matches MapCanvas.jsx's drawRoundedPattern radius


def _reciprocal(rw: dict) -> dict:
    """Same runway from the other end (cf. MAVIS adapters/sim_replay.py)."""
    h = math.radians(rw["heading_true"])
    lat0, lon0, length = rw["threshold"]["lat"], rw["threshold"]["lon"], rw["length_m"]
    ident = rw.get("ident", "")
    try:
        ident = f"{(int(ident) + 18 - 1) % 36 + 1:02d}"
    except ValueError:
        pass
    return {**rw, "ident": ident, "heading_true": (rw["heading_true"] + 180.0) % 360.0,
            "threshold": {"lat": lat0 + length * math.cos(h) / M_PER_DEG,
                          "lon": lon0 + length * math.sin(h) / (M_PER_DEG * math.cos(math.radians(lat0)))}}


def _to_latlon(rw: dict, pattern: dict, along: float, cross: float) -> list[float]:
    """Inverse of MapCanvas's en() projection."""
    th = math.radians(rw["heading_true"])
    side = 1 if pattern.get("side", "left") == "left" else -1
    cl_off = pattern.get("centerline_offset_m", 0) or 0
    lat0, lon0 = rw["threshold"]["lat"], rw["threshold"]["lon"]
    cp = cross + side * cl_off
    s, co = math.sin(th), math.cos(th)
    e = s * along - co * cp / side
    n = co * along + s * cp / side
    return [round(lat0 + n / M_PER_DEG, 7), round(lon0 + e / (M_PER_DEG * math.cos(math.radians(lat0))), 7)]


def _circuit(lesson: dict, rw: dict) -> dict:
    pat = lesson["pattern"]
    up_a = rw["length_m"] + 420
    off = pat["downwind_offset_nm"] * 1852
    fin = (lesson.get("scoring") or {}).get("final_length_m") or pat.get("base_turn_past_abeam_m") or 500
    corners = [(0, 0), (up_a, 0), (up_a, off), (-fin, off), (-fin, 0)]
    # Rounded corners: each corner replaced by a quadratic curve between the
    # points `r` metres along its two edges (same as drawRoundedPattern).
    pts = []
    n = len(corners)
    for i in range(n):
        p0, p1, p2 = corners[i - 1], corners[i], corners[(i + 1) % n]
        v1 = (p0[0] - p1[0], p0[1] - p1[1])
        v2 = (p2[0] - p1[0], p2[1] - p1[1])
        l1, l2 = math.hypot(*v1) or 1, math.hypot(*v2) or 1
        r = min(CORNER_RADIUS_M, l1 / 2, l2 / 2)
        t1 = (p1[0] + v1[0] / l1 * r, p1[1] + v1[1] / l1 * r)
        t2 = (p1[0] + v2[0] / l2 * r, p1[1] + v2[1] / l2 * r)
        for k in range(9):
            u = k / 8
            a = (1 - u) ** 2 * t1[0] + 2 * (1 - u) * u * p1[0] + u ** 2 * t2[0]
            c = (1 - u) ** 2 * t1[1] + 2 * (1 - u) * u * p1[1] + u ** 2 * t2[1]
            pts.append(_to_latlon(rw, pat, a, c))
    pts.append(pts[0])  # closed loop
    return {"path": pts, "runway": [_to_latlon(rw, pat, 0, 0), _to_latlon(rw, pat, rw["length_m"], 0)]}


def _titles() -> dict:
    try:
        cat = json.loads((Path(settings.mavis_lessons_dir) / "catalog.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {les["id"]: f"{mod.get('title', '')} — {les.get('title', '')}".strip(" —")
            for mod in cat.get("modules", []) for les in mod.get("lessons", [])}


@router.get("/routes")
async def list_routes():
    titles = _titles()
    out = []
    seen: dict[str, dict] = {}   # circuits already listed (same shape = same route)
    for f in sorted(Path(settings.mavis_lessons_dir).glob("*.yaml")):
        try:
            lesson = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            rw = lesson.get("runway")
            if (not rw or not lesson.get("pattern") or lesson.get("type") == "familiarization"
                    or lesson.get("segment")):
                continue
            label = titles.get(f.stem) or lesson.get("name") or f.stem
            for runway in (rw, _reciprocal(rw)):
                circuit = _circuit(lesson, runway)
                sig = json.dumps(circuit)
                if sig in seen:
                    seen[sig]["lesson_ids"].append(f.stem)
                    continue
                route = {
                    "key": f"{f.stem}:{runway['ident']}", "lesson_id": f.stem, "lesson_ids": [f.stem],
                    "label": f"{label} (RWY {runway['ident']})", "runway_ident": runway["ident"],
                    **circuit,
                }
                seen[sig] = route
                out.append(route)
        except Exception as e:  # one bad lesson file shouldn't hide the others
            log.warning("routes: skipping %s: %s", f.name, e)
    return out
