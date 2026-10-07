// Vocabulary + plain-English rendering of a rule, shared by both SOP pages.
// Numbers are in MAVIS Telemetry units (kt, ft, ft/min, deg, rpm, throttle 0..1).

export const EDITABLE = ["kind", "title", "phases", "metric", "target", "tol_hi", "tol_lo",
  "min_value", "max_value", "op", "value", "gate_phase", "capture_at", "conditions",
  "severity", "source_quote", "source_page", "note", "enabled", "tags", "aoi", "gaze_measure",
  "lesson_types", "ref_speed", "ref_factor"];

/** The kinds of lesson that use a rule (never empty: a rule without any is a normal-circuit rule). */
export const lessonTypes = (r) => (r.lesson_types?.length ? r.lesson_types : ["normal"]);
export const lessonLabel = (meta, id) => meta?.lesson_types?.find((t) => t.id === id)?.label || id;
const LESSON_WORDS = { normal: "normal circuit", emergency: "emergency", instrument: "instrument-flying" };
/** In words, where a rule applies: "Instrument-flying lessons only" (same wording as the server's used_in). */
export function usedInText(r) {
  const lt = lessonTypes(r), w = lt.map((t) => LESSON_WORDS[t] || t);
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  if (w.length === 1) return `${cap(w[0])} lessons${lt[0] === "normal" ? "" : " only"}`;
  return `${cap(w.slice(0, -1).join(", "))} and ${w[w.length - 1]} lessons`;
}
/** True when a normal circuit lesson never checks this rule. */
export const notInNormal = (r) => !lessonTypes(r).includes("normal");
/** Rules for airspeed can be measured from one of the aircraft's own speeds ("1.3 × VSO"). */
export const canRef = (r) => r.metric === "ias" && ["band", "limit", "capture", "event"].includes(r.kind);
/** "1.3 × VSO" / "the approach speed" */
export function refTxt(meta, r) {
  const s = meta?.ref_speeds?.find((x) => x.id === r.ref_speed);
  if (!s) return null;
  const f = r.ref_factor ?? 1;
  return f === 1 ? s.short : `${fmt(f)} × ${s.short}`;
}
/** What factor × speed comes to in one aircraft (null: its profile doesn't give that speed). */
export function refValue(meta, r, ac) {
  const s = meta?.ref_speeds?.find((x) => x.id === r.ref_speed);
  if (!s || !ac) return null;
  const k = s.keys.find((key) => typeof ac.v_speeds?.[key] === "number");
  return k ? Math.round(ac.v_speeds[k] * (r.ref_factor ?? 1) * 10) / 10 : null;
}

export const KIND_LABEL = {
  band: "Hold target ± tolerance",
  limit: "Stay within limits",
  event: "Must happen by a phase",
  capture: "Check at a moment",
  gaze: "Where the pilot looks",
  unobservable: "Not observable in sim",
};
export const OP_TXT = { ">=": "at least", "<=": "at most", "==": "exactly", ">": "above", "<": "below" };
const UNIT_TXT = { kt: "kt", ft: "ft", "ft/min": "ft/min", deg: "°", rpm: "RPM", frac: "", bool: "" };

export const phaseTxt = (p) => p.toLowerCase().replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
export const fmt = (n) => (n == null || Number.isNaN(n) ? "" : String(Math.round(n * 100) / 100));

export const aoiLabel = (meta, id) => meta?.aoi_roles?.find((a) => a.id === id)?.label || id || "?";
export const bookShort = (meta, id) => meta?.books?.find((b) => b.id === id)?.short || id || "";
/** gaze value as shown and typed: seconds, or a share 0..1 shown as percent */
export const gazeIsShare = (measure) => measure === "min_share" || measure === "max_share";
export const gazeShow = (measure, v) => (v == null ? null : gazeIsShare(measure) ? Math.round(v * 1000) / 10 : v);

function gazeTxt(meta, r) {
  // "look outside" / "look at the instruments" / "look at the airspeed indicator"
  const at = r.aoi === "outside" ? "outside" : r.aoi === "instruments" ? "at the instruments"
    : `at the ${aoiLabel(meta, r.aoi).toLowerCase()}`;
  const v = gazeShow(r.gaze_measure, r.value);
  const n = v == null ? "(threshold not set)" : gazeIsShare(r.gaze_measure) ? `${fmt(v)}%` : `${fmt(v)} s`;
  const p = phasesTxt(r).replace(" at all times", " throughout the flight");
  switch (r.gaze_measure) {
    case "max_gap": return `Look ${at} at least every ${n}${p}.`;
    case "max_dwell": return r.aoi === "instruments"
      ? `Don't stare at any single instrument for longer than ${n}${p}.`
      : `Don't look ${at} for longer than ${n} at a time${p}.`;
    case "min_share": return `Spend at least ${n} of the time looking ${at}${p}.`;
    case "max_share": return `Spend no more than ${n} of the time looking ${at}${p}.`;
    default: return `Gaze rule: look ${at}${p}.`;
  }
}

export function metricInfo(meta, id) {
  return meta?.metrics?.find((m) => m.id === id) || { id, label: id || "", unit: "" };
}
export function unitLabel(meta, metric) {
  const u = metricInfo(meta, metric).unit;
  if (u === "frac") return "0–1";
  return UNIT_TXT[u] ?? u;
}
// a number of a rule that is measured from an aircraft speed: "1.3 × VSO", "the approach speed + 5 kt"
function relVal(meta, r, v) {
  const base = refTxt(meta, r);
  if (v == null) return "?";
  return v === 0 ? base : `${base} ${v > 0 ? "+" : "−"} ${fmt(Math.abs(v))} kt`;
}
function val(meta, metric, v, r) {
  if (r?.ref_speed && refTxt(meta, r)) return relVal(meta, r, v);
  if (v == null) return "?";
  const u = metricInfo(meta, metric).unit;
  if (u === "frac") return Math.round(v * 100) + "%";
  if (u === "bool") return v ? "yes" : "no";
  return fmt(v) + (u === "deg" ? "°" : " " + (UNIT_TXT[u] || u));
}
const suffix = (meta, metric) => {
  const u = metricInfo(meta, metric).unit;
  return u === "deg" ? "°" : u === "frac" || u === "bool" ? "" : " " + (UNIT_TXT[u] || u);
};

function whenTxt(meta, r) {
  const c = (r.conditions || []).map((x) =>
    `${metricInfo(meta, x.metric).label.toLowerCase()} ${OP_TXT[x.op] || x.op} ${val(meta, x.metric, x.value)}`);
  return c.length ? " while " + c.join(" and ") : "";
}
const phasesTxt = (r) => (r.phases?.length ? " during " + r.phases.map(phaseTxt).join(", ") : " at all times");

/** "Hold indicated airspeed at 74 kt +10 / −5 kt during Climb out." */
export function describe(meta, r) {
  const m = metricInfo(meta, r.metric).label.toLowerCase();
  const w = whenTxt(meta, r);
  switch (r.kind) {
    case "band": {
      const { tol_hi: hi, tol_lo: lo } = r;
      const u = suffix(meta, r.metric);
      const tol = hi == null && lo == null ? " (no tolerance set)" :
        hi === lo ? ` ± ${fmt(hi)}${u}` : ` +${fmt(hi ?? 0)} / −${fmt(lo ?? 0)}${u}`;
      return `Hold ${m} at ${val(meta, r.metric, r.target ?? (r.ref_speed ? 0 : null), r)}${tol}${phasesTxt(r)}${w}.`;
    }
    case "limit": {
      const a = r.min_value, b = r.max_value;
      if (r.metric === "vs" && a != null && a < 0 && b == null)
        return `Keep the descent rate at or below ${fmt(-a)} ft/min${phasesTxt(r)}${w}.`;
      if (!r.ref_speed && a != null && b != null && a === -b && b > 0)
        return `Keep ${m} within ±${val(meta, r.metric, b)}${phasesTxt(r)}${w}.`;
      const rng = a != null && b != null ? `between ${val(meta, r.metric, a, r)} and ${val(meta, r.metric, b, r)}` :
        b != null ? `at or below ${val(meta, r.metric, b, r)}` : `at or above ${val(meta, r.metric, a, r)}`;
      return `Keep ${m} ${rng}${phasesTxt(r)}${w}.`;
    }
    case "event":
      return `${metricInfo(meta, r.metric).label} must be ${OP_TXT[r.op] || r.op || "?"} ${val(meta, r.metric, r.value, r)}` +
        (r.gate_phase ? ` by the start of ${phaseTxt(r.gate_phase).toLowerCase()}` : "") + `${w}.`;
    case "capture": {
      const a = r.min_value, b = r.max_value;
      const rng = a != null && b != null ? `between ${val(meta, r.metric, a, r)} and ${val(meta, r.metric, b, r)}` :
        b != null ? `at most ${val(meta, r.metric, b, r)}` : `at least ${val(meta, r.metric, a, r)}`;
      return `At ${String(r.capture_at || "?").replace(/_/g, " ")}, ${m} should be ${rng}.`;
    }
    case "gaze":
      return gazeTxt(meta, r);
    default:
      return "A procedure the simulator can't observe — the instructor checks it by eye or asks about it.";
  }
}

/** Only the fields the API accepts for a rule. */
export function editable(rule) {
  const out = {};
  for (const k of EDITABLE) if (k in rule) out[k] = rule[k];
  return out;
}

export function sameRule(a, b) {
  return JSON.stringify(editable(a)) === JSON.stringify(editable(b));
}
