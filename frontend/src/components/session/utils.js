export const fmt = (v, d = 1) => (v == null || Number.isNaN(v) ? "—" : Number(v).toFixed(d));

export function bounds(arr, key) {
  let mn = Infinity, mx = -Infinity;
  for (const r of arr) {
    const v = r[key];
    if (v == null || Number.isNaN(v)) continue;
    if (v < mn) mn = v;
    if (v > mx) mx = v;
  }
  return [mn, mx];
}
export const norm = (v, [mn, mx]) => (v == null || mx <= mn ? null : (v - mn) / (mx - mn));

export function nearestIndexForTime(arr, t) {
  let lo = 0, hi = arr.length - 1;
  if (hi < 0) return 0;
  if (t <= arr[0].t) return 0;
  if (t >= arr[hi].t) return hi;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (arr[mid].t < t) lo = mid + 1; else hi = mid;
  }
  if (lo > 0 && Math.abs(arr[lo - 1].t - t) <= Math.abs(arr[lo].t - t)) return lo - 1;
  return lo;
}

export function interpolatedGaze(eyeArr, timeline, t) {
  if (!eyeArr.length) return null;
  const i = nearestIndexForTime(timeline, t);
  let j = i;
  if (timeline[i].t < t && i + 1 < eyeArr.length) j = i + 1;
  else if (timeline[i].t > t && i - 1 >= 0) j = i - 1;

  const a = eyeArr[i], b = eyeArr[j];
  if (i === j || a.blink || b.blink || a.aoi !== b.aoi
      || a.gaze_point_x == null || b.gaze_point_x == null) {
    return { x: a.gaze_point_x, y: a.gaze_point_y };
  }
  const lo = timeline[i].t <= timeline[j].t ? a : b;
  const hi = timeline[i].t <= timeline[j].t ? b : a;
  const ta = Math.min(timeline[i].t, timeline[j].t);
  const tb = Math.max(timeline[i].t, timeline[j].t);
  const frac = tb > ta ? Math.min(1, Math.max(0, (t - ta) / (tb - ta))) : 0;
  return {
    x: lo.gaze_point_x + (hi.gaze_point_x - lo.gaze_point_x) * frac,
    y: lo.gaze_point_y + (hi.gaze_point_y - lo.gaze_point_y) * frac,
  };
}

export function zoneForPoint(zones, x, y) {
  for (const z of zones) {
    const xMin = Math.min(z.x1, z.x2), xMax = Math.max(z.x1, z.x2);
    const yMin = Math.min(z.y1, z.y2), yMax = Math.max(z.y1, z.y2);
    if (x >= xMin && x <= xMax && y >= yMin && y <= yMax) return z.name;
  }
  return null;
}

export function effectiveAoi(e, zones) {
  if (e.aoi === "Instruments" && e.gaze_point_x != null && e.gaze_point_y != null) {
    const zoneName = zoneForPoint(zones, e.gaze_point_x, e.gaze_point_y);
    if (zoneName) return zoneName;
  }
  return e.aoi || "unlabelled";
}

// Gaze samples -> "runs": [{aoi, t}] where each run starts when the gaze
// moves to a different area. Blinks are skipped (a run continues across
// one). Glances shorter than minDwellSec are folded into the previous run,
// repeatedly, so a quick flick doesn't break up a steady look.
// elapsedOf(ts) -> seconds on the session clock; endT = when gaze data ends.
export function buildScanRuns(eye, zones, elapsedOf, minDwellSec, endT) {
  const raw = [];
  let prev = null;
  for (const e of eye) {
    if (e.blink) continue;
    const a = effectiveAoi(e, zones);
    if (a !== prev) { raw.push({ aoi: a, t: elapsedOf(e.ts) }); prev = a; }
  }
  if (raw.length < 2) return raw;
  let merged = raw.map((r) => ({ ...r }));
  let changed = true;
  while (changed) {
    changed = false;
    for (let i = 1; i < merged.length; i++) {
      const end = i + 1 < merged.length ? merged[i + 1].t : endT;
      if (end - merged[i].t < minDwellSec) {
        merged[i].aoi = merged[i - 1].aoi;
        changed = true;
      }
    }
    if (changed) {
      const collapsed = [];
      for (const r of merged) {
        if (!collapsed.length || collapsed[collapsed.length - 1].aoi !== r.aoi) collapsed.push(r);
      }
      merged = collapsed;
    }
  }
  return merged;
}

// Cross-check counts: how often the gaze moved directly between each pair
// of areas (either direction), from consecutive runs.
export function scanTransitions(runs) {
  const counts = new Map();
  const visits = new Map();
  for (let i = 0; i < runs.length; i++) {
    visits.set(runs[i].aoi, (visits.get(runs[i].aoi) || 0) + 1);
    if (i === 0) continue;
    const a = runs[i - 1].aoi, b = runs[i].aoi;
    if (a === b) continue;
    const key = a < b ? `${a}\u0000${b}` : `${b}\u0000${a}`;
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  const pairs = [...counts.entries()]
    .map(([k, n]) => { const [a, b] = k.split("\u0000"); return { a, b, count: n }; })
    .sort((x, y) => y.count - x.count);
  const total = pairs.reduce((s, p) => s + p.count, 0);
  return { pairs, total, visits };
}
