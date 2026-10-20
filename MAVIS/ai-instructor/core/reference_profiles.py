"""Expert reference profiles (design spec §15).

Planned = geometric circuit; expert runs are individual JSON files under
profiles/experts/. Multiple runs per callsign (Maverick run 1, run 2, …) are
all loaded; the trainee picker lists each run; the instructor area renames or
deletes them after PIN unlock.
"""
from __future__ import annotations
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
EXPERTS_DIR = HERE / "profiles" / "experts"

# Demo showcase runs, written once if missing (never overwrite instructor edits).
DEMO_RUNS: tuple[tuple[str, str, str, str, int, str], ...] = (
    ("maverick_run1", "Maverick", "Maverick - Wide downwind", "maverick_wide", 1, "2026-03-12"),
    ("maverick_run2", "Maverick", "Maverick - Smooth final", "maverick_smooth", 2, "2026-03-18"),
    ("iceman_run1", "Iceman", "Iceman - Textbook", "iceman", 1, "2026-02-28"),
    ("iceman_run2", "Iceman", "Iceman - Tight circuit", "iceman_tight", 2, "2026-03-05"),
)

DEMO_STYLE_BY_ID: dict[str, str] = {row[0]: row[3] for row in DEMO_RUNS}

# Coaching targets per demo style (§15.11.2 MVP, hand-authored for showcase).
_DEMO_TARGETS: dict[str, dict[str, dict]] = {
    "maverick_wide": {
        "CLIMB_OUT": {"ias": 76}, "CROSSWIND": {"ias": 76},
        "DOWNWIND": {"ias": 88, "alt": 1120}, "BASE": {"ias": 78}, "FINAL": {"ias": 67},
    },
    "maverick_smooth": {
        "CLIMB_OUT": {"ias": 75}, "CROSSWIND": {"ias": 75},
        "DOWNWIND": {"ias": 86, "alt": 1110}, "BASE": {"ias": 76}, "FINAL": {"ias": 64},
    },
    "iceman": {
        "CLIMB_OUT": {"ias": 74}, "CROSSWIND": {"ias": 74},
        "DOWNWIND": {"ias": 82, "alt": 1080}, "BASE": {"ias": 72}, "FINAL": {"ias": 63},
    },
    "iceman_tight": {
        "CLIMB_OUT": {"ias": 74}, "CROSSWIND": {"ias": 74},
        "DOWNWIND": {"ias": 80, "alt": 1070}, "BASE": {"ias": 71}, "FINAL": {"ias": 62},
    },
}

_DEMO_TURN_POINTS: dict[str, dict[str, dict]] = {
    "maverick_wide": {
        "TURN_BASE": {"along_max": -530}, "TURN_FINAL": {"cross_max": 280},
    },
    "maverick_smooth": {
        "TURN_BASE": {"along_max": -515}, "TURN_FINAL": {"cross_max": 265},
    },
    "iceman": {
        "TURN_BASE": {"along_max": -500}, "TURN_FINAL": {"cross_max": 220},
    },
    "iceman_tight": {
        "TURN_BASE": {"along_max": -485}, "TURN_FINAL": {"cross_max": 210},
    },
}


def demo_coaching_for(style: str) -> tuple[dict[str, dict], dict[str, dict]]:
    return _DEMO_TARGETS.get(style, {}), _DEMO_TURN_POINTS.get(style, {})


def _pattern_to_latlon(rw: dict, pat: dict, along: float, cross: float) -> tuple[float, float]:
    lat0, lon0 = rw["threshold"]["lat"], rw["threshold"]["lon"]
    th = math.radians(rw["heading_true"])
    side = 1 if pat.get("side", "left") == "left" else -1
    cl = pat.get("centerline_offset_m", 0)
    cross_adj = cross + side * cl
    sin_t, cos_t = math.sin(th), math.cos(th)
    e = (along * sin_t - cross_adj * cos_t) / side
    n = (along * cos_t + cross_adj * sin_t) / side
    lat = lat0 + n / 111320.0
    lon = lon0 + e / (111320.0 * math.cos(math.radians(lat0)))
    return lat, lon


def latlon_to_pattern(rw: dict, pat: dict, lat: float, lon: float) -> tuple[float, float]:
    """Inverse of _pattern_to_latlon: (along, cross) metres in the lesson's
    pattern frame, cross pattern-side-positive (the frame Scorer works in)."""
    lat0, lon0 = rw["threshold"]["lat"], rw["threshold"]["lon"]
    th = math.radians(rw["heading_true"])
    side = 1 if pat.get("side", "left") == "left" else -1
    cl = pat.get("centerline_offset_m", 0)
    n = (lat - lat0) * 111320.0
    e = (lon - lon0) * 111320.0 * math.cos(math.radians(lat0))
    sin_t, cos_t = math.sin(th), math.cos(th)
    along = side * (e * sin_t + n * cos_t)
    cross_adj = side * (n * sin_t - e * cos_t)
    return along, cross_adj - side * cl


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _lerp2(p0: tuple[float, float], p1: tuple[float, float], t: float) -> tuple[float, float]:
    return (_lerp(p0[0], p1[0], t), _lerp(p0[1], p1[1], t))


def _sample_traffic_pattern(
    corners: list[tuple[float, float]],
    radius: float,
    n_edge: int = 24,
    n_fillet: int = 12,
) -> list[tuple[float, float]]:
    """Sample a traffic pattern that departs and finishes on the threshold."""
    n = len(corners)
    fillets: list[tuple[tuple[float, float], tuple[float, float], tuple[float, float]] | None] = [None]
    for i in range(1, n):
        p0, p1, p2 = corners[i - 1], corners[i], corners[(i + 1) % n]
        v1 = (p0[0] - p1[0], p0[1] - p1[1])
        v2 = (p2[0] - p1[0], p2[1] - p1[1])
        l1, l2 = math.hypot(*v1) or 1.0, math.hypot(*v2) or 1.0
        r = min(radius, l1 / 2, l2 / 2)
        u1 = (v1[0] / l1, v1[1] / l1)
        u2 = (v2[0] / l2, v2[1] / l2)
        t1 = (p1[0] + u1[0] * r, p1[1] + u1[1] * r)
        t2 = (p1[0] + u2[0] * r, p1[1] + u2[1] * r)
        fillets.append((t1, p1, t2))

    threshold = corners[0]
    pts: list[tuple[float, float]] = [threshold]

    def edge(a: tuple[float, float], b: tuple[float, float]) -> None:
        for k in range(1, n_edge + 1):
            pts.append(_lerp2(a, b, k / n_edge))

    def fillet(seg: tuple[tuple[float, float], tuple[float, float], tuple[float, float]]) -> None:
        t1, ctrl, t2 = seg
        for k in range(1, n_fillet + 1):
            t = k / n_fillet
            u = 1.0 - t
            x = u * u * t1[0] + 2 * u * t * ctrl[0] + t * t * t2[0]
            y = u * u * t1[1] + 2 * u * t * ctrl[1] + t * t * t2[1]
            pts.append((x, y))

    edge(threshold, fillets[1][0])  # type: ignore[index]
    for i in range(1, n):
        fillet(fillets[i])  # type: ignore[arg-type]
        if i < n - 1:
            edge(fillets[i][2], fillets[i + 1][0])  # type: ignore[index]
        else:
            edge(fillets[i][2], threshold)  # type: ignore[index]

    pts.append(threshold)
    return pts


def _circuit_corners(up_a: float, off: float, fin: float) -> list[tuple[float, float]]:
    return [
        (0.0, 0.0),
        (up_a, 0.0),
        (up_a, off),
        (-fin, off),
        (-fin, 0.0),
    ]


def _generate_track(rw: dict, pat: dict, style: str) -> list[list[float]]:
    up_a = rw["length_m"] + 420.0
    off = pat.get("downwind_offset_nm", 0.8) * 1852.0
    fin = float(pat.get("base_turn_past_abeam_m", 500))

    if style == "maverick_wide":
        corners = _circuit_corners(up_a + 70.0, off * 1.12, fin * 0.96)
        radius = 300.0
    elif style == "maverick_smooth":
        corners = _circuit_corners(up_a + 45.0, off * 1.06, fin * 0.98)
        radius = 270.0
    elif style == "iceman":
        corners = _circuit_corners(up_a + 35.0, off * 0.93, fin)
        radius = 210.0
    elif style == "iceman_tight":
        corners = _circuit_corners(up_a + 25.0, off * 0.88, fin * 1.02)
        radius = 195.0
    else:
        corners = _circuit_corners(up_a, off, fin)
        radius = 220.0

    ac_pts = _sample_traffic_pattern(corners, radius)
    out: list[list[float]] = []
    for a, c in ac_pts:
        lat, lon = _pattern_to_latlon(rw, pat, a, c)
        out.append([round(lat, 6), round(lon, 6)])
    if out:
        out.append(out[0][:])
    return out


@dataclass
class ReferenceProfile:
    id: str
    display_name: str
    lesson: str
    track: list[list[float]] | None = None
    callsign: str = ""
    run_no: int = 1
    recorded_at: str = ""
    aircraft: str = "c172sp_g1000"
    demo: bool = False
    targets: dict[str, dict] = field(default_factory=dict)
    turn_points: dict[str, dict] = field(default_factory=dict)
    path: Path | None = field(default=None, repr=False)

    def target_ias(self, phase: str, default: float | None = None) -> float | None:
        td = (self.targets or {}).get(phase) or {}
        if "ias" in td:
            return float(td["ias"])
        return default

    def circuit_alt(self, default: float) -> float:
        dw = (self.targets or {}).get("DOWNWIND") or {}
        if "alt" in dw:
            return float(dw["alt"])
        return default

    def to_menu(self) -> dict:
        return {
            "id": self.id,
            "name": self.display_name,
            "callsign": self.callsign,
            "run_no": self.run_no,
            "recorded_at": self.recorded_at,
            "lesson": self.lesson,
        }

    def to_admin(self) -> dict:
        return {
            **self.to_menu(),
            "lesson": self.lesson,
            "aircraft": self.aircraft,
            "demo": self.demo,
        }


PLANNED = ReferenceProfile("planned", "Planned circuit", "lesson01_pattern", track=None)


class ReferenceRegistry:
    def __init__(self, lesson: dict, lesson_id: str):
        self.lesson_id = lesson_id
        # A lesson flown over the same circuit (e.g. the night circuit) can share
        # another lesson's expert runs: references_from: lesson01_pattern
        self.shared_from = lesson.get("references_from") or ""
        self.rw = lesson["runway"]
        self.pat = lesson["pattern"]
        self._profiles: dict[str, ReferenceProfile] = {"planned": PLANNED}
        EXPERTS_DIR.mkdir(parents=True, exist_ok=True)
        self._seed_demo_runs()
        self.reload()

    def _profile_from_file(self, path: Path, data: dict) -> ReferenceProfile | None:
        if data.get("lesson") and data["lesson"] not in (self.lesson_id, self.shared_from):
            return None
        pid = data.get("id") or path.stem
        track = data.get("track")
        if not track:
            return None
        style = DEMO_STYLE_BY_ID.get(pid, "")
        targets = data.get("targets") or _DEMO_TARGETS.get(style, {})
        turn_points = data.get("turn_points") or _DEMO_TURN_POINTS.get(style, {})
        return ReferenceProfile(
            pid,
            data.get("display_name") or data.get("callsign") or pid,
            self.lesson_id,     # shared runs list under the lesson being flown
            track=track,
            callsign=data.get("callsign") or pid.split("_")[0].title(),
            run_no=int(data.get("run_no") or 1),
            recorded_at=str(data.get("recorded_at") or ""),
            aircraft=data.get("aircraft") or "c172sp_g1000",
            demo=bool(data.get("demo")),
            targets=dict(targets),
            turn_points=dict(turn_points),
            path=path,
        )

    def reload(self) -> None:
        self._profiles = {"planned": PLANNED}
        if not EXPERTS_DIR.is_dir():
            return
        for path in sorted(EXPERTS_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                prof = self._profile_from_file(path, data)
                if prof:
                    self._profiles[prof.id] = prof
            except Exception:
                continue

    def _seed_demo_runs(self) -> None:
        """Create showcase runs if missing. Never overwrite existing files."""
        for legacy in list(EXPERTS_DIR.glob("*.json")):
            try:
                data = json.loads(legacy.read_text(encoding="utf-8"))
                if data.get("id") in ("maverick", "iceman") and "Auto-generated" in data.get("note", ""):
                    legacy.unlink()
            except Exception:
                continue
        for rid, callsign, label, style, run_no, recorded_at in DEMO_RUNS:
            path = EXPERTS_DIR / f"{rid}.json"
            if path.exists():
                continue
            track = _generate_track(self.rw, self.pat, style)
            targets, turn_points = demo_coaching_for(style)
            path.write_text(json.dumps({
                "id": rid,
                "callsign": callsign,
                "display_name": label,
                "lesson": self.shared_from or self.lesson_id,
                "aircraft": "c172sp_g1000",
                "run_no": run_no,
                "recorded_at": recorded_at,
                "demo": True,
                "note": "Demo showcase run; replace with a real instructor recording.",
                "track": track,
                "targets": targets,
                "turn_points": turn_points,
            }, indent=2), encoding="utf-8")

    def menu(self) -> list[dict]:
        """Flat list for the trainee picker (planned handled separately on client)."""
        runs = [p for pid, p in self._profiles.items() if pid != "planned"]
        runs.sort(key=lambda p: (p.callsign.lower(), p.run_no, p.recorded_at))
        return [p.to_menu() for p in runs]

    def groups(self) -> list[dict]:
        """Grouped by callsign for optgroup UI."""
        by_cs: dict[str, list[ReferenceProfile]] = {}
        for pid, p in self._profiles.items():
            if pid == "planned":
                continue
            by_cs.setdefault(p.callsign, []).append(p)
        out = []
        for callsign in sorted(by_cs, key=str.lower):
            runs = sorted(by_cs[callsign], key=lambda p: (p.run_no, p.recorded_at))
            out.append({
                "callsign": callsign,
                "runs": [r.to_menu() for r in runs],
            })
        return out

    def admin_list(self) -> list[dict]:
        runs = [p for pid, p in self._profiles.items() if pid != "planned"]
        runs.sort(key=lambda p: (p.lesson, p.callsign.lower(), p.run_no))
        return [p.to_admin() for p in runs]

    def get(self, ref_id: str) -> ReferenceProfile:
        return self._profiles.get(ref_id) or PLANNED

    def tracks(self) -> dict[str, list[list[float]] | None]:
        return {pid: p.track for pid, p in self._profiles.items()}

    def rename(self, ref_id: str, display_name: str) -> bool:
        prof = self._profiles.get(ref_id)
        if not prof or prof.id == "planned" or not prof.path:
            return False
        name = display_name.strip()[:64]
        if not name:
            return False
        prof.display_name = name
        data = json.loads(prof.path.read_text(encoding="utf-8"))
        data["display_name"] = name
        prof.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return True

    def delete(self, ref_id: str) -> bool:
        prof = self._profiles.get(ref_id)
        if not prof or prof.id == "planned" or not prof.path:
            return False
        try:
            prof.path.unlink()
        except OSError:
            return False
        del self._profiles[ref_id]
        return True

    def payload_for_state(self) -> dict:
        return {
            "references": self.menu(),
            "reference_groups": self.groups(),
            "reference_tracks": self.tracks(),
            "reference_runs": self.admin_list(),
        }
