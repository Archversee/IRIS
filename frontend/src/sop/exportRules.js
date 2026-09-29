// Download a saved rule set as a file (built in the browser from the same
// data the SOP Rules page shows -- no extra backend endpoint).
import { describe, phaseTxt, metricInfo } from "./ruleText.js";

const RULE_COLS = ["id", "enabled", "kind", "title", "tags", "phases", "metric", "target", "tol_hi", "tol_lo",
  "min_value", "max_value", "op", "value", "gate_phase", "capture_at", "conditions", "severity",
  "source_page", "source_quote", "note", "origin", "created_by", "created_at", "updated_by", "updated_at"];

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
  return {
    name: set.name,
    aircraft_profile: set.aircraft_profile,
    aircraft_name: ac?.name || set.aircraft_profile,
    active: set.active,
    source_pdf: set.filename || null,
    llm_model: set.llm_model || null,
    prompt_version: set.prompt_version || null,
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
    rules: set.rules.map((r) => ({
      ...Object.fromEntries(RULE_COLS.map((k) => [k, r[k] ?? null])),
      unit: r.metric ? units[r.metric] : null,
      summary: describe(meta, r),
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
  const cols = ["summary", "title", "tags", "enabled", "severity", "kind", "metric", "unit", "phases", "target", "tol_hi", "tol_lo",
    "min_value", "max_value", "op", "value", "gate_phase", "capture_at", "conditions", "source_page", "source_quote",
    "note", "origin", "updated_by", "updated_at", "id"];
  const rows = set.rules.map((r) => ({
    ...r,
    summary: describe(meta, r),
    unit: r.metric ? metricInfo(meta, r.metric).unit : "",
    phases: (r.phases || []).map(phaseTxt),
    tags: (r.tags || []).map((t) => meta.tags?.find((x) => x.id === t)?.label || t),
    gate_phase: r.gate_phase ? phaseTxt(r.gate_phase) : "",
  }));
  const h = header(set, meta);
  const lines = [
    `# ${h.name} — ${h.aircraft_name}${h.active ? " (active)" : ""}; exported ${new Date().toLocaleString()}`,
    cols.join(","),
    ...rows.map((r) => cols.map((c) => csvCell(r[c])).join(",")),
  ];
  return "﻿" + lines.join("\r\n");
}

export function exportRuleSet(set, meta, format) {
  const base = `${safeName(set.name)}_${set.aircraft_profile}_${stamp()}`;
  if (format === "csv") download(toCsv(set, meta), `${base}.csv`, "text/csv;charset=utf-8");
  else download(toJson(set, meta), `${base}.json`, "application/json");
}
