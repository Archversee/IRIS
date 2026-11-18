// The app's data palette, for charts, AOI regions, events and badges.
// Each entry is a CSS variable defined in styles.css (--data-*), with a
// darker version for the light theme, so the same colour always means the
// same thing and stays visible on either background.
// Works anywhere CSS colours do (style props, SVG/recharts fill & stroke).
// Not for <canvas>: canvas can't read CSS variables -- use cssColor().
export const C = {
  cyan: "var(--data-cyan)",
  green: "var(--data-green)",
  amber: "var(--data-amber)",
  violet: "var(--data-violet)",
  teal: "var(--data-teal)",
  rose: "var(--data-rose)",
  yellow: "var(--data-yellow)",
  blue: "var(--data-blue)",
  pink: "var(--data-pink)",
  red: "var(--data-red)",
  orange: "var(--data-orange)",
  lime: "var(--data-lime)",
  slate: "var(--data-slate)",
  slateDark: "var(--data-slate-dark)",
  gray: "var(--data-gray)",
  grayLight: "var(--data-gray-light)",
  neutral: "var(--data-neutral)",
};

// Theme colours for chart chrome (axes, grid, tooltips).
export const UI = {
  text: "var(--text)",
  muted: "var(--muted)",
  faint: "var(--faint)",
  border: "var(--border)",
  borderStrong: "var(--border-strong)",
  panel: "var(--panel)",
  accent: "var(--accent)",
  danger: "var(--danger)",
  warn: "var(--warn)",
  good: "var(--good)",
};

// Recharts axis/grid props in one place so every chart looks the same.
export const AXIS = { stroke: UI.borderStrong, tick: { fill: UI.muted, fontSize: 12 } };
export const GRID = { stroke: UI.border, strokeDasharray: "3 3" };
export const TOOLTIP = {
  contentStyle: {
    background: "var(--tooltip-bg)", border: "1px solid var(--border-strong)", borderRadius: 6,
    color: "var(--text)", fontSize: 13,
  },
  labelStyle: { color: "var(--muted)" },
  itemStyle: { color: "var(--text)" },
};

// Resolve a palette entry to a real colour (for <canvas>).
export function cssColor(value, el = document.documentElement) {
  const m = /^var\((--[\w-]+)\)$/.exec(value);
  return m ? getComputedStyle(el).getPropertyValue(m[1]).trim() : value;
}
