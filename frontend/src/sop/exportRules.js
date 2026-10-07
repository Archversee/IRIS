// Download a rule set as a file (built in the browser from the same data the
// pages show -- no extra backend endpoint). Works for a saved rule set and for
// a draft that is still being reviewed. Set-aside rules are included, marked
// in the "status" column with the reason they were set aside.
import { describe, phaseTxt, metricInfo, aoiLabel, bookShort, gazeIsShare, lessonTypes, refTxt, usedInText,
  notInNormal } from "./ruleText.js";

// the plain-English line of a rule, saying first when it is NOT for normal circuit lessons - a reader
// (or a model) handed one exported rule must not mistake an instrument-lesson rule for a general one
const summaryOf = (meta, r) => (notInNormal(r) ? `[${usedInText(r)}] ` : "") + describe(meta, r);
import { compressPages } from "./PagePicker.jsx";

const RULE_COLS = ["id", "enabled", "kind", "title", "tags", "phases", "metric", "target", "tol_hi", "tol_lo",
  "min_value", "max_value", "op", "value", "gate_phase", "capture_at", "conditions", "aoi", "gaze_measure",
  "lesson_types", "ref_speed", "ref_factor", "severity",
  "source_page", "source_quote", "note", "origin", "set_aside", "set_aside_reason",
  "created_by", "created_at", "updated_by", "updated_at"];

export const BY_REVIEWER = "Set aside by the reviewer";
/** In use / Switched off / Set aside -- what a lesson does with the rule. A
 *  draft can also hold rules that are ticked "use" but don't pass the checks
 *  yet (Needs fixing): they can't be saved until fixed or set aside. */
export const ruleStatus = (r) => (r.set_aside ? "Set aside" : r.needs_fixing ? "Needs fixing"
  : r.enabled === false ? "Switched off" : "In use");
/** Why a DRAFT rule is set aside (the server works this out again at Save). */
export function draftAsideReason(r) {
  if (r.include) return null;
  if (r.blocked && r.auto_aside) return r.auto_aside.includes(r.blocked) ? r.auto_aside : `${r.auto_aside} (also: ${r.blocked})`;
  if (r.blocked) return r.blocked;
  if (r.auto_aside) return r.auto_aside;
  // same order as the server (sop/validate.py aside_reason): the automatic checks, else the reviewer
  for (const i of r.issues || []) {
    if (i.startsWith("Set aside: ")) return i.slice("Set aside: ".length);
    if (i.startsWith("A correctly flown circuit")) return i;
  }
  return BY_REVIEWER;
}
/** A draft under review in the shape of a saved rule set, so it can be exported before saving. */
export function draftAsSet(d, { rules, name, aircraft, book }) {
  const hb = d.doc_type === "handbook";
  return {
    name: name || d.name, draft: true, active: false,
    scope: hb ? "general" : "aircraft", handbook: hb ? book || d.handbook : null,
    aircraft_profile: hb ? null : aircraft || d.aircraft_profile || null,
    filename: d.imported_from && !d.sha256 && d.filename === d.imported_from ? null : d.filename,
    document_sha256: d.sha256 || null, llm_model: d.llm_model, prompt_version: d.prompt_version, llm_seconds: d.llm_seconds ?? null,
    page_selection: (d.selected_pages || []).length ? compressPages(d.selected_pages) : null,
    created_by: d.created_by, created_at: d.created_at, updated_by: null, updated_at: d.updated_at,
    rules: rules.map((r) => ({ ...r, id: r.cid, enabled: r.enabled !== false, set_aside: !r.include,
      set_aside_reason: draftAsideReason(r), needs_fixing: r.include && r.blocked ? r.blocked : null })),
  };
}
// rules in use first, set-aside rules after them (each group in its saved order)
const ordered = (rules) => [...rules.filter((r) => !r.set_aside), ...rules.filter((r) => r.set_aside)];

function safeName(s) {
  return (s || "rules").replace(/[\\/:*?"<>|]+/g, "_").replace(/\s+/g, " ").trim().slice(0, 80);
}
function stamp() {
  const d = new Date(), p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`;
}
function download(text, filename, type) {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function header(set, meta) {
  const ac = meta.aircraft.find((a) => a.id === set.aircraft_profile);
  const general = set.scope === "general";
  return {
    name: set.name,
    saved: !set.draft,                       // false = exported from a draft still under review
    rules_in_use: set.rules.filter((r) => !r.set_aside && !r.needs_fixing && r.enabled !== false).length,
    rules_need_fixing: set.rules.filter((r) => !r.set_aside && r.needs_fixing).length,
    rules_switched_off: set.rules.filter((r) => !r.set_aside && r.enabled === false).length,
    rules_set_aside: set.rules.filter((r) => r.set_aside).length,
    scope: set.scope || "aircraft",
    handbook: set.handbook || null,
    aircraft_profile: set.aircraft_profile,
    aircraft_name: general ? `All aircraft (${bookShort(meta, set.handbook)})` : ac?.name || set.aircraft_profile,
    active: set.active,
    source_pdf: set.filename || null,
    source_sha256: set.document_sha256 || null,   // identifies the PDF, so an import can find it again on a machine that has it
    llm_model: set.llm_model || null,
    prompt_version: set.prompt_version || null,
    ...(set.llm_seconds != null ? { llm_seconds: set.llm_seconds } : {}),   // how long the LLM took (drafts only): for comparing models
    pages_read: set.page_selection || null,
    created_by: set.created_by, created_at: set.created_at,
    updated_by: set.updated_by, updated_at: set.updated_at,
  };
}

/** Full-fidelity JSON: every stored field, plus units and a readable summary. */
export function toJson(set, meta) {
  const units = Object.fromEntries(meta.metrics.map((m) => [m.id, m.unit]));
  return JSON.stringify({
    format: "mavis-sop-rules/1",
    exported_at: new Date().toISOString(),
    rule_set: header(set, meta),
    units,
    rules: ordered(set.rules).map((r) => ({
      ...Object.fromEntries(RULE_COLS.map((k) => [k, r[k] ?? null])),
      lesson_types: lessonTypes(r),
      used_in: usedInText(r),
      status: ruleStatus(r),
      needs_fixing: r.needs_fixing || null,
      unit: r.metric ? units[r.metric] : r.kind === "gaze" ? (gazeIsShare(r.gaze_measure) ? "fraction" : "s") : null,
      summary: summaryOf(meta, r),
    })),
  }, null, 2);
}

function csvCell(v) {
  if (v == null) return "";
  if (Array.isArray(v)) v = v.length ? (typeof v[0] === "object" ? v.map((c) => `${c.metric} ${c.op} ${c.value}`).join("; ") : v.join("; ")) : "";
  else if (typeof v === "boolean") v = v ? "yes" : "no";
  const s = String(v);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** Spreadsheet-friendly CSV (opens in Excel; UTF-8 BOM so ± and ° survive). */
export function toCsv(set, meta) {
  const cols = ["summary", "title", "status", "used_in", "set_aside_reason", "needs_fixing", "tags", "enabled", "severity", "kind", "metric", "unit", "phases", "target", "tol_hi", "tol_lo",
    "min_value", "max_value", "measured_from", "op", "value", "gate_phase", "capture_at", "conditions", "look_at", "gaze_check", "source_page", "source_quote",
    "note", "origin", "updated_by", "updated_at", "id"];
  const rows = ordered(set.rules).map((r) => ({
    ...r,
    status: ruleStatus(r),
    used_in: usedInText(r),
    measured_from: r.ref_speed ? refTxt(meta, r) : "",
    summary: summaryOf(meta, r),
    unit: r.metric ? metricInfo(meta, r.metric).unit : r.kind === "gaze" ? (gazeIsShare(r.gaze_measure) ? "share 0-1" : "s") : "",
    look_at: r.kind === "gaze" ? aoiLabel(meta, r.aoi) : "",
    gaze_check: r.kind === "gaze" ? (meta.gaze_measures?.find((g) => g.id === r.gaze_measure)?.label || r.gaze_measure) : "",
    phases: (r.phases || []).map(phaseTxt),
    tags: (r.tags || []).map((t) => meta.tags?.find((x) => x.id === t)?.label || t),
    gate_phase: r.gate_phase ? phaseTxt(r.gate_phase) : "",
  }));
  const h = header(set, meta);
  const lines = [
    `# ${h.name} — ${h.aircraft_name}${set.draft ? " (DRAFT, not saved yet)" : h.active ? " (active)" : ""}; ${h.rules_in_use} in use, ${h.rules_need_fixing ? `${h.rules_need_fixing} need fixing, ` : ""}${h.rules_switched_off} switched off, ${h.rules_set_aside} set aside; exported ${new Date().toLocaleString()}`,
    cols.join(","),
    ...rows.map((r) => cols.map((c) => csvCell(r[c])).join(",")),
  ];
  return "﻿" + lines.join("\r\n");
}

export function exportRuleSet(set, meta, format) {
  const base = `${safeName(set.name)}_${set.scope === "general" ? `all-aircraft-${set.handbook}` : set.aircraft_profile || "no-aircraft"}${set.draft ? "_DRAFT" : ""}_${stamp()}`;
  if (format === "csv") download(toCsv(set, meta), `${base}.csv`, "text/csv;charset=utf-8");
  else download(toJson(set, meta), `${base}.json`, "application/json");
}
