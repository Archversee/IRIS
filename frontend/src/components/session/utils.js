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
