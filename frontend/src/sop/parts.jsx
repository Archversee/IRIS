import { useEffect, useState } from "react";
import { exportRuleSet } from "./exportRules.js";
import { describe, lessonTypes, lessonLabel, usedInText } from "./ruleText.js";
import { getEditor, setEditor } from "./sopApi.js";

const SEV_TITLE = { warning: "Warning", caution: "Caution", advisory: "Advisory" };

export const tagLabel = (meta, id) => meta?.tags?.find((t) => t.id === id)?.label || id;
export const tagMatch = (rule, sel) => !sel.length || (rule.tags || []).some((t) => sel.includes(t));

/** Toggle chips for the tags present in `rules` (shows rules with ANY chosen tag). */
export function TagFilter({ meta, rules, value, onChange }) {
  const counts = {};
  for (const r of rules) for (const t of r.tags || []) counts[t] = (counts[t] || 0) + 1;
  const ids = (meta.tags || []).map((t) => t.id).filter((id) => counts[id]);
  if (!ids.length) return null;
  return (
    <div className="sop-tagfilter" role="group" aria-label="Filter by tag">
      {ids.map((id) => {
        const on = value.includes(id);
        return (
          <button type="button" key={id} className={"sop-tag" + (on ? " is-on" : "")} aria-pressed={on}
            title={meta.tags.find((t) => t.id === id)?.help}
            onClick={() => onChange(on ? value.filter((x) => x !== id) : [...value, id])}>
            {tagLabel(meta, id)} <span>{counts[id]}</span>
          </button>
        );
      })}
      {value.length > 0 && <button type="button" className="sop-tag sop-tag--clear" onClick={() => onChange([])}>Clear</button>}
    </div>
  );
}

export const lessonMatch = (rule, sel) => !sel || lessonTypes(rule).includes(sel);

/** "Used in" filter: Normal circuit / Emergency / Instrument flying. Hidden while every rule is a normal-circuit rule. */
export function LessonFilter({ meta, rules, value, onChange }) {
  const counts = {};
  for (const r of rules) for (const t of lessonTypes(r)) counts[t] = (counts[t] || 0) + 1;
  const ids = (meta.lesson_types || []).map((t) => t.id).filter((id) => counts[id]);
  if (!ids.some((id) => id !== "normal")) return null;
  return (
    <div className="sop-tagfilter sop-lessonfilter" role="group" aria-label="Filter by lesson type">
      <span className="sop-tagfilter__label">Used in</span>
      {ids.map((id) => (
        <button type="button" key={id} className={"sop-tag sop-tag--lesson" + (value === id ? " is-on" : "")} aria-pressed={value === id}
          title={meta.lesson_types.find((t) => t.id === id)?.help}
          onClick={() => onChange(value === id ? null : id)}>
          {lessonLabel(meta, id)} <span>{counts[id]}</span>
        </button>
      ))}
    </div>
  );
}

/** The handbook prompt's summary of what the passage says (helps set a threshold). Unverified LLM text. */
export function LlmNote({ note }) {
  // the note may also carry a line saying a number was suggested from the FAA: that is not what the book says
  const lines = (note || "").split("\n");
  const faa = lines.filter((l) => l.startsWith("FAA suggestion: "));
  const m = /^LLM summary of the passage:\s*(.+)$/s.exec(lines.filter((l) => !l.startsWith("FAA suggestion: ")).join("\n"));
  if (!m && !faa.length) return null;
  return (
    <>
      {m && <div className="sop-llmnote"><b>What the book says here</b> <span className="muted">(LLM summary — check it against the page)</span><br />{m[1]}</div>}
      {faa.map((l, i) => <div key={i} className="sop-llmnote sop-llmnote--faa"><b>Not from this document</b> — {l}</div>)}
    </>
  );
}

/** One rule in a list: severity dot, title, plain-English summary, tags, flags. */
export function RuleRow({ meta, rule, selected, onSelect, status, statusClass, leading, dim }) {
  const flags = (rule.issues || []).slice(0, 3);
  const lt = lessonTypes(rule);
  const special = lt.some((t) => t !== "normal") ? lt : [];   // plain normal-circuit rules carry no chip
  return (
    <div className={"sop-row" + (selected ? " is-sel" : "") + (dim ? " is-dim" : "")}>
      {leading}
      <button type="button" className="sop-row__main" onClick={onSelect}>
        <span className={"sop-dot sop-dot--" + (rule.severity || "caution")} title={SEV_TITLE[rule.severity] || ""} />
        <span className="sop-row__text">
          <span className="sop-row__title">
            <b>{rule.title || "Untitled rule"}</b>
            {status && <span className={"sop-status " + (statusClass || "")}>{status}</span>}
          </span>
          <span className="sop-row__sum">{describe(meta, rule)}</span>
          {((rule.tags || []).length > 0 || special.length > 0) && (
            <span className="sop-row__tags">
              {special.length === 1
                ? <span className={"sop-tag sop-tag--mini sop-tag--lesson sop-tag--" + special[0]}
                    title="Checked only in this kind of lesson - never in a normal circuit">{usedInText(rule)}</span>
                : special.map((t) => <span key={"lt" + t} className={"sop-tag sop-tag--mini sop-tag--lesson sop-tag--" + t} title="Used in this kind of lesson">{lessonLabel(meta, t)} lessons</span>)}
              {(rule.tags || []).map((t) => <span key={t} className="sop-tag sop-tag--mini">{tagLabel(meta, t)}</span>)}
            </span>
          )}
          {rule.blocked && <span className="sop-flag sop-flag--bad">{rule.blocked}</span>}
          {flags.length > 0 && (
            <span className="sop-flags">
              {flags.map((f, i) => <span key={i} className="sop-flag" title={f}>{f.length > 48 ? f.slice(0, 46) + "…" : f}</span>)}
            </span>
          )}
        </span>
      </button>
    </div>
  );
}

/** "Export ▾" menu (CSV / JSON). `load()` returns the rule set to export: the
 *  latest saved rules, or a draft under review in the same shape. Set-aside
 *  rules are included, with their status and reason. */
export function ExportMenu({ meta, setErr, load, note }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!open) return undefined;
    const close = (e) => { if (!e.target.closest?.(".sop-export")) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  async function go(format) {
    setOpen(false); setBusy(true);
    try {
      exportRuleSet(await load(), meta, format);
    } catch (e) { setErr(`Export failed: ${e.message}`); }
    setBusy(false);
  }
  return (
    <div className="sop-export">
      <button type="button" className="secondary" disabled={busy} aria-haspopup="menu" aria-expanded={open}
        onClick={() => setOpen((v) => !v)}>{busy ? "Exporting…" : "Export ▾"}</button>
      {open && (
        <div className="sop-export__menu" role="menu">
          <button type="button" role="menuitem" onClick={() => go("csv")}>
            <b>CSV</b><span>Opens in Excel · one row per rule</span>
          </button>
          <button type="button" role="menuitem" onClick={() => go("json")}>
            <b>JSON</b><span>Every field, for backup or other tools</span>
          </button>
          <div className="sop-export__note">{note || "Includes set-aside rules, marked in the status column."}</div>
        </div>
      )}
    </div>
  );
}

/** "Editing as" name, sent with every change so history records who did it. */
export function EditorName() {
  const [name, setName] = useState(getEditor());
  return (
    <label className="sop-editor">
      <span>Editing as</span>
      <input value={name} placeholder="Your name / callsign" maxLength={40}
        onChange={(e) => { setName(e.target.value); setEditor(e.target.value.trim()); }} />
    </label>
  );
}

export function useKeyNav(list, selKey, keyOf, select, extra = {}) {
  // J/K or arrows to move; extra: {a: fn, r: fn}. Ignored while typing.
  return (e) => {
    if (/INPUT|TEXTAREA|SELECT/.test(e.target?.tagName || "")) return;
    const i = list.findIndex((x) => keyOf(x) === selKey);
    const k = e.key.toLowerCase();
    if ((k === "j" || e.key === "ArrowDown") && list[i + 1]) { e.preventDefault(); select(keyOf(list[i + 1])); }
    else if ((k === "k" || e.key === "ArrowUp") && list[i - 1]) { e.preventDefault(); select(keyOf(list[i - 1])); }
    else if (extra[k]) { e.preventDefault(); extra[k](); }
  };
}
