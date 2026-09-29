import { useMemo, useState } from "react";
import { sopApi } from "./sopApi.js";

/** "1-5, 9" <-> [1,2,3,4,5,9] */
export function compressPages(nums) {
  const s = [...new Set(nums)].sort((a, b) => a - b);
  const out = [];
  for (let i = 0; i < s.length; i++) {
    let j = i;
    while (j + 1 < s.length && s[j + 1] === s[j] + 1) j++;
    out.push(i === j ? String(s[i]) : `${s[i]}-${s[j]}`);
    i = j;
  }
  return out.join(", ");
}
export function parsePages(text, n) {
  const out = new Set();
  for (const raw of String(text || "").split(/[,;]/)) {
    const part = raw.trim();
    if (!part) continue;
    const m = part.match(/^(\d+)\s*[-–]\s*(\d+)$/);
    if (m) {
      let [a, b] = [Number(m[1]), Number(m[2])];
      if (a > b) [a, b] = [b, a];
      for (let p = a; p <= b; p++) out.add(p);
    } else if (/^\d+$/.test(part)) out.add(Number(part));
    else throw new Error(`Can't read "${part}" — use e.g. 13-20, 34-63`);
  }
  return [...out].filter((p) => p >= 1 && p <= n).sort((a, b) => a - b);
}

/** Step between reading the PDF and running the LLM: choose which pages the
 *  LLM reads (sections detected from the POH's running headers/footers). */
export default function PagePicker({ d, meta, busy, rerun, onRun, onCancel }) {
  const n = d.page_count || 0;
  const [sel, setSel] = useState(() => new Set(d.selected_pages?.length ? d.selected_pages : d.suggested_pages || []));
  const [text, setText] = useState(() => compressPages([...sel]));
  const [textErr, setTextErr] = useState(null);
  const [aircraft, setAircraft] = useState(d.aircraft_profile || "");
  const scanned = useMemo(() => new Set(d.scanned_pages || []), [d.scanned_pages]);
  const outline = d.outline || [];

  function apply(next) {
    setSel(next); setText(compressPages([...next])); setTextErr(null);
  }
  function toggleSection(o, on) {
    const next = new Set(sel);
    for (let p = o.first; p <= o.last; p++) (on ? next.add(p) : next.delete(p));
    apply(next);
  }
  function onText(v) {
    setText(v);
    try { setSel(new Set(parsePages(v, n))); setTextErr(null); } catch (e) { setTextErr(e.message); }
  }
  const nSel = sel.size;
  const nScan = [...sel].filter((p) => scanned.has(p)).length;
  const hasSections = outline.some((o) => o.section);

  return (
    <div className="card sop-picker">
      <div className="sop-picker__head">
        <div>
          <h2>{rerun ? "Change the pages and run again" : "Choose the pages to draft rules from"}</h2>
          <p className="muted">
            <b>{d.filename}</b> has {n} pages. The LLM reads only the pages you choose.
            {hasSections
              ? " For a POH, Limitations (Section 2) and Normal Procedures (Section 4) hold the rules the simulator can check. Supplements for optional equipment (autopilot, GPS, transponder…) only produce noise."
              : " No POH sections were found in this document; choose the pages that contain operating limits and normal procedures."}
          </p>
        </div>
      </div>

      <div className="sop-field sop-picker__ac">
        <label htmlFor="sop-pick-ac">Aircraft</label>
        <select id="sop-pick-ac" value={aircraft} onChange={(e) => setAircraft(e.target.value)}>
          <option value="">Choose the aircraft…</option>
          {meta.aircraft.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
        <span className="muted sop-small">Used to tell the LLM which aircraft this is, and to check every rule against a correctly flown circuit in it.</span>
      </div>

      <table className="sop-table sop-picker__table">
        <thead><tr><th /><th>Section</th><th>Pages</th><th /></tr></thead>
        <tbody>
          {outline.map((o, i) => {
            const pages = Array.from({ length: o.last - o.first + 1 }, (_, k) => o.first + k);
            const k = pages.filter((p) => sel.has(p)).length;
            const state = k === 0 ? "none" : k === pages.length ? "all" : "some";
            return (
              <tr key={i} className={state !== "none" ? "is-on" : ""}>
                <td>
                  <input type="checkbox" aria-label={`Select ${o.title}`} checked={state === "all"}
                    ref={(el) => { if (el) el.indeterminate = state === "some"; }}
                    onChange={(e) => toggleSection(o, e.target.checked)} />
                </td>
                <td>
                  <b>{o.section ? `Section ${o.section} · ` : ""}{o.title}</b>
                  {o.suggested && <span className="sop-chip sop-chip--ok sop-picker__sug">Suggested</span>}
                  {o.scanned > 0 && <span className="sop-chip">{o.scanned} scanned</span>}
                </td>
                <td className="muted">{o.first === o.last ? `p. ${o.first}` : `p. ${o.first}–${o.last}`} · {o.count} page{o.count > 1 ? "s" : ""}{state === "some" ? ` (${k} chosen)` : ""}</td>
                <td className="sop-actions-cell">
                  <a href={sopApi.draftPageImage(d.id, o.first)} target="_blank" rel="noreferrer">Preview</a>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <div className="sop-field">
        <label htmlFor="sop-pick-pages">Pages to read</label>
        <input id="sop-pick-pages" value={text} onChange={(e) => onText(e.target.value)} placeholder="e.g. 13-20, 34-63" />
        {textErr && <div className="err sop-small">{textErr}</div>}
      </div>

      <div className="sop-picker__foot">
        <span className="muted sop-small">
          {nSel} of {n} pages chosen{nScan ? ` · ${nScan} scanned page${nScan > 1 ? "s" : ""} will be read with OCR` : ""}
          {rerun ? " · LLM-drafted rules are replaced; rules you added by hand are kept" : ""}
        </span>
        <div className="sop-btnrow">
          <button type="button" className="secondary" onClick={() => apply(new Set(d.suggested_pages || []))}>Use suggestion</button>
          <button type="button" className="secondary" onClick={() => apply(new Set(Array.from({ length: n }, (_, k) => k + 1)))}>All pages</button>
          {onCancel && <button type="button" className="secondary" onClick={onCancel}>Cancel</button>}
          <button type="button" disabled={!nSel || !!textErr || busy}
            onClick={() => onRun({ pages: compressPages([...sel]), aircraft_profile: aircraft || null })}>
            {busy ? "Starting…" : `Draft rules from ${nSel} page${nSel === 1 ? "" : "s"}`}
          </button>
        </div>
      </div>
    </div>
  );
}
