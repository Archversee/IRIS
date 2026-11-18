import { C } from "../../palette.js";
export const AOI_COLORS = {
  OTW: C.teal, Instruments: C.cyan,
  airspeed: C.green, altimeter: C.amber, attitude: C.violet,
  heading: C.rose, throttle: C.yellow,
  unlabelled: C.slate,
};
export const PALETTE = [C.cyan, C.green, C.amber, C.violet, C.teal, C.rose, C.yellow, C.blue];
export function aoiColor(name, i = 0) {
  return AOI_COLORS[name] || PALETTE[i % PALETTE.length];
}

export const METRICS = [
  { key: "altitude", label: "Altitude", unit: "ft", color: C.cyan, src: "altitude_ft" },
  { key: "airspeed", label: "Airspeed", unit: "kt", color: C.green, src: "airspeed_kt" },
  { key: "vspeed", label: "Vert speed", unit: "fpm", color: C.violet, src: "vertical_speed_fpm" },
  // `needs: "index"` = drawn from the workload index (Review only, routers/workload.py);
  // the Live Monitor leaves these out until there's a live version of it
  { key: "workload", label: "Workload", unit: "", color: C.amber, src: "workload", needs: "index" },
];
// The workload index is drawn on a fixed scale so its bands sit at the same
// height on every flight: WL_MIN..WL_MAX fills the chart.
export const WL_MIN = -1;
export const WL_MAX = 3;
export const WL_BAND_LABEL = { low: "Low", medium: "Medium", high: "High" };
export const WL_BAND_COLOR = { low: C.green, medium: C.amber, high: C.red };

// Flight events (MAVIS to-do steps / circuit phases). Coloured by SOP grade once
// scored; until then each step gets a neutral colour of its own, a timed-out
// step is red and an abandoned (retried) attempt is grey.
export const GRADE_COLORS = { S: C.green, A: C.green, B: C.lime, C: C.amber, D: C.orange, F: C.red };
export const EVENT_COLORS = [C.cyan, C.violet, C.teal, C.amber, C.rose, C.blue, C.yellow, C.green];
export function eventColor(e, i) {
  if (e.kind === "demo") return C.slateDark;   // MAVIS flying the demonstration: not graded
  if (e.grade && GRADE_COLORS[e.grade]) return GRADE_COLORS[e.grade];
  if (e.kind === "stall") return C.red;   // a stall interrupts the flight: always stands out
  if (e.outcome === "skipped") return C.red;
  if (e.outcome === "retried" || e.outcome === "abandoned") return C.slate;
  return EVENT_COLORS[i % EVENT_COLORS.length];
}

export const VIDEO_SYNC_TOLERANCE = 0.15;
export const VIDEO_SYNC_TOLERANCE_PLAYING = 0.75;
// glances shorter than this are folded into the previous look (0 = every glance counts);
// the scan path's "min glance" box starts here, the Analytics cross-check uses it as is
export const MIN_AOI_DWELL_SEC_DEFAULT = 0;
export const CHART_MAX_POINTS = 2000;
