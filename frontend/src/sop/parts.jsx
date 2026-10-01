import { useState } from "react";
import { describe } from "./ruleText.js";
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

/** One rule in a list: severity dot, title, plain-English summary, tags, flags. */
export function RuleRow({ meta, rule, selected, onSelect, status, statusClass, leading, dim }) {
  const flags = (rule.issues || []).slice(0, 3);
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
          {(rule.tags || []).length > 0 && (
            <span className="sop-row__tags">{rule.tags.map((t) => <span key={t} className="sop-tag sop-tag--mini">{tagLabel(meta, t)}</span>)}</span>
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
