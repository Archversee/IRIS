import { useState } from "react";
import { describe } from "./ruleText.js";
import { getEditor, setEditor } from "./sopApi.js";

const SEV_TITLE = { warning: "Warning", caution: "Caution", advisory: "Advisory" };

/** One rule in a list: severity dot, title, plain-English summary, flags. */
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
