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
  { key: "workload", label: "Workload", unit: "", color: "#f5a623", src: "workload" },
];

export const VIDEO_SYNC_TOLERANCE = 0.15;
export const VIDEO_SYNC_TOLERANCE_PLAYING = 0.75;
export const MIN_AOI_DWELL_SEC_DEFAULT = 0.1;
export const CHART_MAX_POINTS = 2000;
