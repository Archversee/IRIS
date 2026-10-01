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

function pagesOf(outline, pred) {
  const out = [];
  for (const o of outline) if (pred(o)) for (let p = o.first; p <= o.last; p++) out.push(p);
  return out;
}

/** Step between reading the PDF and running the LLM: choose which pages the
 *  LLM reads -- POH sections (from its running headers/footers) or handbook
 *  chapters (from chapter headings and page labels like "8-3"). The document
 *  type can still be switched here, before any LLM time is spent. */
export default function PagePicker({ d, meta, busy, rerun, onRun, onCancel }) {
  const n = d.page_count || 0;
  const [docType, setDocType] = useState(d.doc_type || "poh");
  const [book, setBook] = useState(d.handbook || d.handbook_detected || "");
  const isHB = docType === "handbook";
  const outline = (isHB ? d.chapters : d.outline) || [];
  const suggestion = useMemo(() => {
    const s = pagesOf(outline, (o) => o.suggested);
    return s.length ? s : Array.from({ length: n }, (_, k) => k + 1);
  }, [outline, n]);
  const [sel, setSel] = useState(() => new Set(d.selected_pages?.length ? d.selected_pages : d.suggested_pages || []));
  const [text, setText] = useState(() => compressPages([...sel]));
  const [textErr, setTextErr] = useState(null);
  const [aircraft, setAircraft] = useState(d.aircraft_profile || "");
  const scanned = useMemo(() => new Set(d.scanned_pages || []), [d.scanned_pages]);

  function apply(next) {
    setSel(next); setText(compressPages([...next])); setTextErr(null);
  }
  function switchType(t) {
    setDocType(t);
    const o = (t === "handbook" ? d.chapters : d.outline) || [];
    const s = pagesOf(o, (x) => x.suggested);
    apply(new Set(s.length ? s : Array.from({ length: n }, (_, k) => k + 1)));
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
  const hasSections = outline.some((o) => (isHB ? o.chapter : o.section));
  const rowTitle = (o) => (isHB
    ? `${o.chapter && /^\d+$/.test(o.chapter) && !o.title.startsWith("Chapter") ? `Chapter ${o.chapter} · ` : ""}${o.title}`
    : `${o.section ? `Section ${o.section} · ` : ""}${o.title}`);

  return (
    <div className="card sop-picker">
      <div className="sop-picker__head">
        <div>
          <h2>{rerun ? "Change the pages and run again" : "Choose the pages to draft rules from"}</h2>
          <p className="muted">
            <b>{d.filename}</b> has {n} pages. The LLM reads only the pages you choose.
            {isHB
              ? (hasSections
                ? " In the FAA handbooks the circuit and scan-path rules are in a few chapters: Basic Flight Maneuvers, Takeoffs and Departure Climbs, Airport Traffic Patterns, Approaches and Landings (Airplane Flying Handbook), Attitude Instrument Flying (Instrument Flying Handbook), Airport Operations and Aeromedical Factors (PHAK). Those are suggested; the rest of the book is theory."
                : " No chapters were found in this document; choose the pages about circuit procedure and scanning.")
              : (hasSections
                ? " For a POH, Limitations (Section 2) and Normal Procedures (Section 4) hold the rules the simulator can check. Supplements for optional equipment (autopilot, GPS, transponder…) only produce noise."
                : " No POH sections were found in this document; choose the pages that contain operating limits and normal procedures.")}
          </p>
        </div>
      </div>
      {(d.warnings || []).map((w, i) => <div key={i} className="sop-warn">{w}</div>)}

      <div className="sop-picker__type">
        <div className="sop-field">
          <label htmlFor="sop-pick-type">Document type</label>
          <select id="sop-pick-type" value={docType} onChange={(e) => switchType(e.target.value)}>
            {(meta.doc_types || []).map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
          </select>
        </div>
        {isHB ? (
          <div className="sop-field">
            <label htmlFor="sop-pick-book">Handbook</label>
            <select id="sop-pick-book" value={book} onChange={(e) => setBook(e.target.value)}>
              {!book && <option value="">Choose…</option>}
              {(meta.books || []).map((b) => <option key={b.id} value={b.id}>{b.label}</option>)}
            </select>
          </div>
        ) : (
          <div className="sop-field">
            <label htmlFor="sop-pick-ac">Aircraft</label>
            <select id="sop-pick-ac" value={aircraft} onChange={(e) => setAircraft(e.target.value)}>
              <option value="">Choose the aircraft…</option>
              {meta.aircraft.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          </div>
        )}
        <span className="muted sop-small sop-picker__typehint">
          {isHB
            ? "Rules from a training handbook apply to every aircraft, and are checked against a correctly flown circuit in each aircraft profile."
            : "Used to tell the LLM which aircraft this is, and to check every rule against a correctly flown circuit in it."}
        </span>
      </div>

      <table className="sop-table sop-picker__table">
        <thead><tr><th /><th>{isHB ? "Chapter" : "Section"}</th><th>Pages</th><th /></tr></thead>
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
                  <b>{rowTitle(o)}</b>
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
          {isHB && nSel > 60 ? " · a long selection can take an hour or more with a local LLM" : ""}
          {rerun ? " · LLM-drafted rules are replaced; rules you added by hand are kept" : ""}
        </span>
        <div className="sop-btnrow">
          <button type="button" className="secondary" onClick={() => apply(new Set(suggestion))}>Use suggestion</button>
          <button type="button" className="secondary" onClick={() => apply(new Set(Array.from({ length: n }, (_, k) => k + 1)))}>All pages</button>
          {onCancel && <button type="button" className="secondary" onClick={onCancel}>Cancel</button>}
          <button type="button" disabled={!nSel || !!textErr || busy || (isHB && !book)}
            onClick={() => onRun({ pages: compressPages([...sel]), doc_type: docType,
              ...(isHB ? { handbook: book } : { aircraft_profile: aircraft || null }) })}>
            {busy ? "Starting…" : `Draft rules from ${nSel} page${nSel === 1 ? "" : "s"}`}
          </button>
        </div>
      </div>
    </div>
  );
}
