export const AOI_COLORS = {
  OTW: "#14b8a6", Instruments: "#38bdf8",
  airspeed: "#4ade80", altimeter: "#f5a623", attitude: "#a78bfa",
  heading: "#fb7185", throttle: "#facc15",
  unlabelled: "#64748b",
};
export const PALETTE = ["#38bdf8", "#4ade80", "#f5a623", "#a78bfa", "#14b8a6", "#fb7185", "#facc15", "#60a5fa"];
export function aoiColor(name, i = 0) {
  return AOI_COLORS[name] || PALETTE[i % PALETTE.length];
}

export const METRICS = [
  { key: "altitude", label: "Altitude", unit: "ft", color: "#38bdf8", src: "altitude_ft" },
  { key: "airspeed", label: "Airspeed", unit: "kt", color: "#4ade80", src: "airspeed_kt" },
  { key: "vspeed", label: "Vert speed", unit: "fpm", color: "#a78bfa", src: "vertical_speed_fpm" },
  // `needs`: "eye" = any eye data; "index" = the workload index (Review only, routers/workload.py)
  { key: "workload", label: "Workload", unit: "", color: "#f5a623", src: "workload", needs: "eye" },
  { key: "scanSpread", label: "Scan spread", unit: "bits", color: "#14b8a6", src: "h_s", needs: "index" },
  { key: "scanRandom", label: "Scan randomness", unit: "bits", color: "#fb7185", src: "h_t", needs: "index" },
];
// The workload index is drawn on a fixed scale so its bands sit at the same
// height on every flight: WL_MIN..WL_MAX fills the chart.
export const WL_MIN = -1;
export const WL_MAX = 3;
export const WL_BAND_LABEL = { low: "Low", medium: "Medium", high: "High" };

// Flight events (MAVIS to-do steps / circuit phases). Coloured by SOP grade once
// scored; until then each step gets a neutral colour of its own, a timed-out
// step is red and an abandoned (retried) attempt is grey.
export const GRADE_COLORS = { S: "#4ade80", A: "#4ade80", B: "#a3e635", C: "#f5a623", D: "#fb923c", F: "#ef4444" };
export const EVENT_COLORS = ["#38bdf8", "#a78bfa", "#14b8a6", "#f5a623", "#fb7185", "#60a5fa", "#facc15", "#4ade80"];
export function eventColor(e, i) {
  if (e.kind === "demo") return "#475569";   // MAVIS flying the demonstration: not graded
  if (e.grade && GRADE_COLORS[e.grade]) return GRADE_COLORS[e.grade];
  if (e.kind === "stall") return "#ef4444";   // a stall interrupts the flight: always stands out
  if (e.outcome === "skipped") return "#ef4444";
  if (e.outcome === "retried" || e.outcome === "abandoned") return "#64748b";
  return EVENT_COLORS[i % EVENT_COLORS.length];
}

export const VIDEO_SYNC_TOLERANCE = 0.15;
export const VIDEO_SYNC_TOLERANCE_PLAYING = 0.75;
export const MIN_AOI_DWELL_SEC_DEFAULT = 0.1;
export const CHART_MAX_POINTS = 2000;
