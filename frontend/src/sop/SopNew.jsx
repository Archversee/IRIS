import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { sopApi, ApiError } from "./sopApi.js";
import { editable, bookShort } from "./ruleText.js";
import RuleEditor from "./RuleEditor.jsx";
import SourceViewer from "./SourceViewer.jsx";
import PagePicker, { compressPages } from "./PagePicker.jsx";
import { RuleRow, EditorName, useKeyNav, TagFilter, tagMatch, LlmNote, ExportMenu, LessonFilter, lessonMatch } from "./parts.jsx";
import { draftAsSet } from "./exportRules.js";
import ConfirmDialog from "../live/components/ConfirmDialog.jsx";
import "../live/live.css";
import "./sop.css";

// /sop/new -- upload an aircraft POH/SOP or a training handbook (FAA or the
// school's own), let the local LLM draft rules, review and edit them, then
// Save. A rules file made with Export > JSON can be dropped here instead of a
// PDF: it becomes a draft straight away (no LLM run), to review and Save. A POH's rules are bound to one aircraft; a handbook's apply to every
// aircraft. Until Save, the draft only exists as a local file on the MAVIS
// machine (resumable via ?draft=<id>); nothing is in the database.
export default function SopNew() {
  const [params, setParams] = useSearchParams();
  const draftId = params.get("draft");
  const [meta, setMeta] = useState(null);
  const [err, setErr] = useState(null);
  const leaveDraft = useCallback(() => setParams({}), [setParams]);

  useEffect(() => {
    sopApi.meta().then(setMeta).catch((e) => setErr(e.message));
  }, []);

  return (
    <div className="sop">
      <header className="sop-head">
        <div>
          <div className="sop-crumb">MAVIS · Preflight &amp; Cockpit</div>
          <h1>Upload SOP or handbook</h1>
        </div>
        <div className="spacer" />
        <EditorName />
        <Link to="/sop/rules" className="sop-link-btn">Saved rules →</Link>
      </header>
      {err && <div className="sop-error">SOP service unavailable: {err}</div>}
      {meta && (draftId
        ? <DraftReview key={draftId} id={draftId} meta={meta} onGone={leaveDraft} />
        : <UploadStart meta={meta} onCreated={(id) => setParams({ draft: id })} />)}
    </div>
  );
}

/* ------------------------------------------------------------------ upload */
function Health({ status, meta }) {
  if (!status) return <div className="sop-health"><span className="sop-chip">Checking local services…</span></div>;
  const items = [
    [status.llm, `Local LLM · ${status.model}`, status.llm ? "" : "Not running in Ollama — rules can still be added by hand"],
    [status.ocr, "OCR (Tesseract)", status.ocr_detail || (status.ocr ? "" : "Not installed — scanned pages will be skipped")],
    [status.db, "Rules database", status.db ? "" : `${status.db_detail || "Postgres not reachable"} — you can review, but Save will fail`],
    [!status.flying, "No lesson in progress", status.flying ? "Uploading is paused until the lesson ends" : ""],
  ];
  return (
    <div className="sop-health">
      {items.map(([ok, t, why]) => (
        <span key={t} className={"sop-chip " + (ok ? "sop-chip--ok" : "sop-chip--bad")} title={why}>{t}</span>
      ))}
      {status.busy && <span className="sop-chip sop-chip--warn">Another SOP is being processed</span>}
      {!status.ocr && status.ocr_detail && <div className="sop-warn sop-health__why">OCR unavailable: {status.ocr_detail}</div>}
      {!status.db && status.db_detail && <div className="sop-warn sop-health__why">Rules database: {status.db_detail}</div>}
      {(meta?.aircraft || []).filter((a) => a.missing_speeds?.length).map((a) => (
        <div key={a.id} className="sop-warn sop-health__why">
          Aircraft profile {a.name} has no {a.missing_speeds.join(", ")}: a rule measured from {a.missing_speeds.length === 1 ? "that speed" : "one of those speeds"} can't
          be checked in it. Add {a.missing_speeds.length === 1 ? "it" : "them"} under v_speeds in profiles/aircraft/{a.id}.yaml.
        </div>
      ))}
    </div>
  );
}

const DOC_HELP = {
  poh: "The aircraft's Pilot's Operating Handbook, flight manual or type SOP. Its rules (limits, speeds, flap settings) apply to that aircraft only.",
  handbook: "A training handbook — the FAA handbooks or your school's own. Its rules (circuit procedure, scan path) apply to every aircraft. Saved rules from it are used together with each aircraft's POH rules.",
};

const dur = (s) => (s == null ? "" : s < 90 ? `${s} s` : `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")} s`);

/** Which local model drafts the rules. One click switches it; the choice is kept on the MAVIS machine. */
function ModelSwitch({ status, onChanged }) {
  const [busy, setBusy] = useState(null);
  const [err, setErr] = useState(null);
  if (!status) return null;
  const models = [...new Set([status.configured_model, ...(status.models || [])].filter(Boolean))];
  const installed = (m) => (status.models || []).includes(m) || (status.models || []).includes(`${m}:latest`);
  async function pick(m) {
    if (m === status.model || busy) return;
    setBusy(m); setErr(null);
    try { await sopApi.setModel(m === status.configured_model ? "" : m); await onChanged(); } catch (e) { setErr(e.message); }
    setBusy(null);
  }
  return (
    <div className="card sop-models">
      <div className="sop-models__head">
        <b>LLM that drafts the rules</b>
        <span className="muted sop-small">Used for every run from now on (also "Run LLM again"). One model at a time fits on the GPU: the coach's model is put aside during a run and comes back by itself.</span>
      </div>
      <div className="sop-seg sop-models__list" role="radiogroup" aria-label="LLM model">
        {models.map((m) => (
          <button type="button" key={m} role="radio" aria-checked={m === status.model} disabled={!!busy || status.busy || !installed(m)}
            className={m === status.model ? "is-on" : ""} title={installed(m) ? "" : "Not installed in Ollama"} onClick={() => pick(m)}>
            {busy === m ? "Switching…" : m}{m === status.coach_model ? " · coach" : ""}{installed(m) ? "" : " (not installed)"}
          </button>
        ))}
        <button type="button" className="sop-models__refresh" title="Look again for installed models" onClick={() => onChanged()}>↻</button>
      </div>
      <div className="muted sop-small">
        Not listed? Install it on this machine in a terminal, e.g. <code>ollama pull qwen3.6:27b</code>, then press ↻. Each draft and export records the model and how long it took.
      </div>
      {err && <div className="sop-error">{err}</div>}
    </div>
  );
}

function DocTypePicker({ meta, docType, setDocType, book, setBook, disabled }) {
  return (
    <div className="card sop-doctype">
      <div className="sop-doctype__opts" role="radiogroup" aria-label="What are you uploading?">
        {(meta.doc_types || []).map((t) => (
          <label key={t.id} className={"sop-doctype__opt" + (docType === t.id ? " is-on" : "")}>
            <input type="radio" name="sop-doctype" value={t.id} checked={docType === t.id} disabled={disabled}
              onChange={() => setDocType(t.id)} />
            <span><b>{t.label}</b><span className="muted sop-small">{DOC_HELP[t.id]}</span></span>
          </label>
        ))}
      </div>
      {docType === "handbook" && (
        <div className="sop-field sop-doctype__book">
          <label htmlFor="sop-book">Which handbook</label>
          <select id="sop-book" value={book} disabled={disabled} onChange={(e) => setBook(e.target.value)}>
            <option value="">Detect from the PDF</option>
            {(meta.books || []).map((b) => <option key={b.id} value={b.id}>{b.label}</option>)}
          </select>
          <span className="muted sop-small">Tells the LLM what to look for (e.g. scan errors in the Instrument Flying Handbook). Upload one handbook at a time; you choose its chapters next.</span>
        </div>
      )}
    </div>
  );
}

function UploadStart({ meta, onCreated }) {
  const [docType, setDocType] = useState("poh");
  const [book, setBook] = useState("");
  const [status, setStatus] = useState(null);
  const [drafts, setDrafts] = useState(null);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [discard, setDiscard] = useState(null);
  const [big, setBig] = useState(false);
  const input = useRef(null);
  const maxMb = status?.max_pdf_mb || 500;

  const refresh = useCallback(() => {
    sopApi.status().then(setStatus).catch(() => setStatus(null));
    sopApi.listDrafts().then(setDrafts).catch((e) => setErr(e.message));
  }, []);
  useEffect(refresh, [refresh]);

  async function upload(file) {
    if (!file) return;
    if (/\.json$/i.test(file.name) || file.type === "application/json") {
      // rules exported from these pages: no PDF to read and no LLM run
      setBig(false); setBusy(true); setErr(null);
      try {
        const d = await sopApi.importRules(file);
        onCreated(d.id);
      } catch (e) {
        setErr(e.message); setBusy(false);
      }
      return;
    }
    if (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf") {
      setErr("Choose the document as a PDF file, or a rules file (.json) exported from the SOP pages."); return;
    }
    if (file.size > maxMb * 1024 * 1024) {
      setErr(`${file.name} is ${Math.round(file.size / 1048576)} MB; the limit is ${maxMb} MB (sop.max_pdf_mb in MAVIS's config.yaml).`);
      return;
    }
    setBig(file.size > 30 * 1024 * 1024);
    setBusy(true); setErr(null);
    try {
      const d = await sopApi.upload(file, docType, docType === "handbook" ? book : "");
      onCreated(d.id);
    } catch (e) {
      setErr(e.message); setBusy(false);
    }
  }

  return (
    <div className="sop-start">
      <Health status={status} meta={meta} />
      <ModelSwitch status={status} onChanged={() => sopApi.status().then(setStatus).catch(() => setStatus(null))} />
      <DocTypePicker meta={meta} docType={docType} setDocType={setDocType} book={book} setBook={setBook} disabled={busy} />
      <div
        className={"sop-drop" + (over ? " is-over" : "") + (busy ? " is-busy" : "")}
        role="button" tabIndex={0}
        onClick={() => !busy && input.current?.click()}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") input.current?.click(); }}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); upload(e.dataTransfer.files[0]); }}
      >
        <input ref={input} type="file" accept="application/pdf,.pdf,application/json,.json" hidden
          onChange={(e) => { upload(e.target.files[0]); e.target.value = ""; }} />
        <div className="sop-drop__icon" aria-hidden>⇪</div>
        <h2>{busy ? (big ? "Uploading a large file — this can take a minute…" : "Uploading…")
          : docType === "handbook" ? "Drop the training handbook here" : "Drop the aircraft SOP here"}</h2>
        <p className="muted">PDF up to {maxMb} MB · typed or scanned pages · processed on this machine only</p>
        <button type="button" disabled={busy}>Choose PDF or rules file</button>
        <p className="muted sop-small sop-drop__alt">
          Already have rules? Drop a <b>rules file (.json)</b> made with <b>Export → JSON</b> here instead — it opens as a draft to review, with no LLM run.
        </p>
      </div>
      {err && <div className="sop-error">{err}</div>}
      <ol className="sop-steps">
        <li><b>Read</b> every page (OCR for scanned ones)</li>
        <li><b>Draft</b> rules with the local LLM</li>
        <li><b>Review &amp; edit</b> — nothing is stored yet</li>
        <li><b>Save</b> to the database{docType === "handbook" ? " for every aircraft" : " for an aircraft"}</li>
      </ol>

      <section className="card">
        <h3>Unsaved drafts</h3>
        {drafts === null ? <div className="muted sop-small">Loading…</div> :
          drafts.length === 0 ? <div className="muted sop-small">None — every uploaded SOP has been saved or discarded.</div> : (
            <table className="sop-table">
              <thead><tr><th>Document</th><th>Status</th><th>Rules</th><th>Last change</th><th /></tr></thead>
              <tbody>
                {drafts.map((d) => (
                  <tr key={d.id}>
                    <td><b>{d.name}</b><div className="muted sop-small">
                      {d.doc_type === "handbook" ? `Handbook${d.handbook ? ` · ${bookShort(meta, d.handbook)}` : ""}` : "Aircraft POH"} · {d.filename}{d.imported_from ? " · imported rules file" : ""}</div></td>
                    <td>{d.status === "processing" ? `Processing… ${d.detail || ""}` : d.status === "failed" ? <span className="err">Failed</span>
                      : d.status === "choose_pages" ? "Choose pages" : "Ready to review"}</td>
                    <td>{d.n_rules}{d.n_blocked ? <span className="err"> · {d.n_blocked} need fixing</span> : ""}</td>
                    <td className="muted sop-small">{new Date(d.updated_at).toLocaleString()}{d.created_by ? ` · ${d.created_by}` : ""}</td>
                    <td className="sop-actions-cell">
                      <button type="button" onClick={() => onCreated(d.id)}>Open</button>
                      <button type="button" className="secondary" onClick={() => setDiscard(d)}>Discard</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
      </section>
      {discard && (
        <ConfirmDialog title="Discard this draft?" confirmLabel="Discard"
          message={`"${discard.name}" and every edit made to its rules will be deleted. Nothing in the database changes.`}
          onCancel={() => setDiscard(null)}
          onConfirm={async () => {
            try { await sopApi.discardDraft(discard.id); } catch (e) { setErr(e.message); }
            setDiscard(null); refresh();
          }} />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ review */
const STAGES = [["extracting", "Read pages"], ["ocr", "OCR scanned pages"], ["parsing", "Draft rules"], ["validating", "Check rules"], ["done", "Ready"]];
const FILTERS = [["all", "All"], ["blocked", "Needs fixing"], ["warn", "Warnings"], ["aside", "Set aside"]];
const newCid = () => Math.random().toString(16).slice(2, 14);

function Progress({ d }) {
  const stage = d.stage === "paused" ? "parsing" : d.stage;
  const idx = STAGES.findIndex((s) => s[0] === stage);
  const pct = d.total ? Math.round((100 * d.done) / d.total) : 4;
  return (
    <div className="card sop-progress">
      <div className="sop-progress__top"><b>{d.filename}</b><span className="muted">{d.detail || "Starting…"}</span></div>
      <div className="sop-bar"><i style={{ width: `${pct}%` }} /></div>
      <div className="sop-stages">
        {STAGES.map(([k, t], i) => (
          <span key={k} className={i < idx ? "is-done" : i === idx ? "is-on" : ""}>{i < idx ? "✓ " : ""}{t}</span>
        ))}
      </div>
      {d.stage === "paused" && <div className="sop-warn">Paused while a lesson is being flown — it continues by itself when the lesson ends.</div>}
      <p className="muted sop-small">You can leave this page; processing continues on the MAVIS machine and the draft will be under "Unsaved drafts".</p>
    </div>
  );
}

function DraftReview({ id, meta, onGone }) {
  const nav = useNavigate();
  const [d, setD] = useState(null);           // server draft (status, warnings, ...)
  const [rules, setRules] = useState([]);      // local, edited rules
  const [name, setName] = useState("");
  const [aircraft, setAircraft] = useState("");
  const [book, setBook] = useState("");
  const [sel, setSel] = useState(null);
  const [filter, setFilter] = useState("all");
  const [saveState, setSaveState] = useState("saved"); // saved | pending | saving | error
  const [err, setErr] = useState(null);
  const [confirm, setConfirm] = useState(null);
  const [saving, setSaving] = useState(false);
  const [acWarn, setAcWarn] = useState([]);
  const [picking, setPicking] = useState(false);
  const [starting, setStarting] = useState(false);
  const [tagSel, setTagSel] = useState([]);
  const [lessonSel, setLessonSel] = useState(null);
  const [llm, setLlm] = useState(null);          // the model the next run uses
  const loaded = useRef(false);
  useEffect(() => { sopApi.status().then((s) => setLlm(s.model)).catch(() => {}); }, []);
  const version = useRef(0);

  // ---- load + poll while processing
  useEffect(() => {
    let off = false, timer = null;
    async function tick() {
      try {
        const x = await sopApi.getDraft(id);
        if (off) return;
        setD(x);
        if (x.status === "processing") { timer = setTimeout(tick, 1000); return; }
        if (!loaded.current || x._reloaded) {
          loaded.current = true;
          setRules(x.rules || []);
          setName(x.name || "");
          setAircraft(x.aircraft_profile || "");
          setBook(x.handbook || "");
          setAcWarn(x.aircraft_warnings || []);
          setSel((x.rules || []).find((r) => r.include)?.cid ?? x.rules?.[0]?.cid ?? null);
        }
      } catch (e) {
        if (!off) { if (e.status === 404) onGone(); else setErr(e.message); }
      }
    }
    tick();
    return () => { off = true; clearTimeout(timer); };
  }, [id, onGone, d?._restart]);

  // ---- autosave edits to the local draft file (debounced). Keyed on the
  // editable content only, so applying the server's re-check results
  // (issues/blocked) doesn't trigger another save.
  const isHB = d?.doc_type === "handbook";
  const draftBody = () => ({ name, aircraft_profile: aircraft, ...(isHB && book ? { handbook: book } : {}),
    rules: rules.map((r) => ({ cid: r.cid, include: r.include, ...editable(r) })) });
  const editKey = useMemo(() => JSON.stringify([name, aircraft, book, rules.map((r) => [r.cid, r.include, editable(r)])]),
    [name, aircraft, book, rules]);
  useEffect(() => {
    if (!loaded.current || d?.status !== "ready") return;
    const v = ++version.current;
    setSaveState("pending");
    const t = setTimeout(async () => {
      setSaveState("saving");
      try {
        const x = await sopApi.putDraft(id, draftBody());
        if (v !== version.current) return;            // newer edits are on their way
        const byCid = Object.fromEntries(x.rules.map((r) => [r.cid, r]));
        // take only what the server computes; never overwrite what's being typed
        setRules((cur) => cur.map((r) => byCid[r.cid] ? { ...r, issues: byCid[r.cid].issues, blocked: byCid[r.cid].blocked, quote_score: byCid[r.cid].quote_score,
          suggestion: byCid[r.cid].suggestion, suggested: byCid[r.cid].suggested } : r));
        setAcWarn(x.aircraft_warnings || []);
        setSaveState("saved");
      } catch (e) {
        if (v === version.current) { setSaveState("error"); setErr(e.message); }
      }
    }, 700);
    return () => clearTimeout(t);
  }, [editKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const counts = useMemo(() => ({
    all: rules.length,
    blocked: rules.filter((r) => r.include && r.blocked).length,
    warn: rules.filter((r) => r.include && !r.blocked && (r.issues || []).length).length,
    aside: rules.filter((r) => !r.include).length,
    included: rules.filter((r) => r.include).length,
  }), [rules]);
  const visible = rules.filter((r) =>
    (filter === "all" ? true : filter === "blocked" ? r.include && r.blocked :
      filter === "warn" ? r.include && !r.blocked && (r.issues || []).length : !r.include) && tagMatch(r, tagSel)
    && lessonMatch(r, lessonSel));
  const cur = rules.find((r) => r.cid === sel) || null;

  const update = (cid, patch) => setRules((rs) => rs.map((r) => (r.cid === cid ? { ...r, ...patch } : r)));
  const onKey = useKeyNav(visible, sel, (r) => r.cid, setSel, {
    x: () => cur && update(cur.cid, { include: !cur.include }),
  });
  useEffect(() => {
    if (d?.status !== "ready") return undefined;
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (err && !d) return <div className="sop-error">{err}</div>;
  if (!d) return <div className="muted">Loading draft…</div>;
  if (d.status === "processing") return <Progress d={d} />;

  async function runPages(body) {
    setStarting(true); setErr(null);
    try {
      await sopApi.run(id, body);
      loaded.current = false; setPicking(false);
      setD({ ...d, status: "processing", stage: "queued", done: 0, total: 0, detail: "", _restart: Date.now() });
    } catch (e) { setErr(e.message); }
    setStarting(false);
  }
  if (d.status === "choose_pages" || picking) {
    return (
      <>
        <PagePicker d={d} meta={meta} busy={starting} rerun={picking} onRun={runPages}
          onCancel={picking ? () => setPicking(false) : null} />
        {err && <div className="sop-error">{err}</div>}
      </>
    );
  }
  if (d.status === "failed") {
    return (
      <div className="card">
        <h3>Processing failed</h3>
        <div className="sop-error">{d.error}</div>
        <div className="sop-btnrow">
          <button type="button" onClick={async () => {
            try { await sopApi.reparse(id); loaded.current = false; setErr(null); setD({ ...d, status: "processing", _restart: Date.now() }); }
            catch (e) { setErr(e.message); }
          }}>Run again</button>
          <button type="button" className="secondary" onClick={() => setConfirm("discard")}>Discard draft</button>
          <Link to="/sop/new" className="sop-link-btn">Upload another file</Link>
        </div>
        {err && <div className="sop-error">{err}</div>}
        {confirm === "discard" && <ConfirmDialog title="Discard this draft?" confirmLabel="Discard" message="The draft is deleted. Nothing in the database changes."
          onCancel={() => setConfirm(null)} onConfirm={async () => { await sopApi.discardDraft(id).catch(() => {}); onGone(); }} />}
      </div>
    );
  }

  const acName = (a) => meta.aircraft.find((x) => x.id === a)?.name || a;
  const canSave = (isHB ? !!book : !!aircraft) && rules.length > 0 && counts.blocked === 0 && !saving;
  const canRerun = d.can_rerun !== false;      // false: imported rules file with no PDF on this machine
  // set aside only because the document gave no number, with the FAA's pre-filled: ready to accept
  const faaReady = rules.filter((r) => !r.include && r.suggested && r.suggested.ready !== false && !r.blocked);
  const nextModel = d.llm_choice || llm || d.llm_model;
  const hasPages = d.page_count > 0;           // page text is available (quotes were checked against it)
  const target = isHB ? `every aircraft (${bookShort(meta, book)})` : acName(aircraft);

  async function doSave() {
    setSaving(true); setErr(null);
    try {
      // flush the latest edits first, then save exactly what's on screen
      await sopApi.putDraft(id, draftBody());
      const res = await sopApi.saveDraft(id, isHB ? { handbook: book, name: name || d.name }
        : { aircraft_profile: aircraft, name: name || d.name });
      nav(`/sop/rules?aircraft=${encodeURIComponent(isHB ? "_general" : aircraft)}&set=${res.rule_set_id}&saved=1`);
    } catch (e) {
      setSaving(false);
      if (e instanceof ApiError && e.status === 422 && e.detail?.blocked?.length) {
        const blocked = Object.fromEntries(e.detail.blocked.map((b) => [b.cid, b.reason]));
        setRules((rs) => rs.map((r) => (blocked[r.cid] ? { ...r, blocked: blocked[r.cid] } : r)));
        setFilter("blocked");
      }
      setErr(e.message);
    }
  }

  function addRule() {
    const r = { cid: newCid(), origin: "manual", include: true, enabled: true, kind: "limit", title: "New rule",
      metric: "ias", severity: "caution", phases: [], conditions: [], lesson_types: ["normal"], issues: [], blocked: "limit rule needs a min or a max" };
    setRules((rs) => [...rs, r]); setFilter("all"); setSel(r.cid);
  }

  return (
    <div className="sop-review">
      <div className="card sop-savebar">
        <div className="sop-field">
          <label htmlFor="sop-name">Rule set name</label>
          <input id="sop-name" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} />
        </div>
        {isHB ? (
          <div className="sop-field">
            <label htmlFor="sop-book2">Handbook · applies to every aircraft</label>
            <select id="sop-book2" value={book} onChange={(e) => setBook(e.target.value)}>
              {!book && <option value="">Choose the handbook…</option>}
              {(meta.books || []).map((b) => <option key={b.id} value={b.id}>{b.label}</option>)}
            </select>
          </div>
        ) : (
          <div className="sop-field">
            <label htmlFor="sop-ac">Aircraft</label>
            <select id="sop-ac" value={aircraft} onChange={(e) => setAircraft(e.target.value)}>
              <option value="">Choose the aircraft…</option>
              {meta.aircraft.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          </div>
        )}
        <div className="sop-savebar__status">
          <span className={"sop-autosave sop-autosave--" + saveState}>
            {saveState === "saved" ? "Draft saved on this machine" : saveState === "error" ? "Couldn't save draft" : "Saving draft…"}
          </span>
          <span className="muted sop-small">{counts.included} rule{counts.included === 1 ? "" : "s"} will be used{counts.aside ? ` · ${counts.aside} set aside (saved too, not used)` : ""}{counts.blocked ? ` · ${counts.blocked} need fixing` : ""}</span>
          {d.imported_from && (
            <span className="muted sop-small">Imported from {d.imported_from}{d.has_pdf ? "" : " · no PDF on this machine"}</span>
          )}
          {(d.selected_pages?.length > 0 || d.llm_model) && (
            <span className="muted sop-small">
              {d.selected_pages?.length > 0 && <>{d.imported_from ? "Drafted from pages" : "Read pages"} {compressPages(d.selected_pages)}{d.page_count ? ` of ${d.page_count}` : ""}</>}
              {d.llm_model ? `${d.selected_pages?.length ? " · " : ""}${d.llm_model}` : ""}{d.prompt_version ? ` · prompt v${d.prompt_version}` : ""}
              {d.llm_seconds != null ? ` · drafted in ${dur(d.llm_seconds)}` : ""}
              {canRerun && d.page_count > 0 && <> · <button type="button" className="sop-linkish" onClick={() => setPicking(true)}>Change pages or model</button></>}
            </span>
          )}
        </div>
        <div className="sop-btnrow">
          {canRerun && <button type="button" className="secondary" onClick={() => setConfirm("reparse")}>Run LLM again</button>}
          <button type="button" className="secondary" onClick={() => setConfirm("discard")}>Discard</button>
          <ExportMenu meta={meta} setErr={setErr} load={async () => draftAsSet(d, { rules, name, aircraft, book })}
            note="Exports this draft as it is now, including set-aside rules." />
          <button type="button" disabled={!canSave} onClick={() => setConfirm("save")}
            title={!isHB && !aircraft ? "Choose the aircraft first" : counts.blocked ? "Fix or set aside the rules that need fixing" : ""}>
            {saving ? "Saving…" : counts.included ? `Save ${counts.included} rules` : "Save (all set aside)"}
          </button>
        </div>
      </div>

      {(d.warnings || []).map((w, i) => <div key={i} className="sop-warn">{w}</div>)}
      {(isHB || aircraft) && acWarn.map((w, i) => <div key={"ac" + i} className="sop-warn">{isHB || w.startsWith("The ") ? w : `Check against ${acName(aircraft)}: ${w}`}</div>)}
      {err && <div className="sop-error">{err}</div>}
      {faaReady.length > 0 && (
        <div className="sop-suggest sop-suggest--bar">
          <div>
            <b>{faaReady.length} set-aside rule{faaReady.length === 1 ? " has" : "s have"} an FAA suggestion pre-filled</b>
            <div className="muted sop-small">The document gave no tolerance or scan threshold for {faaReady.length === 1 ? "it" : "them"}, so the standard FAA value was filled in
              (FAA private-pilot checkride standards / Aeronautical Information Manual). Each rule says which. Check them under "Set aside", or accept them all.</div>
          </div>
          <button type="button" id="sop-accept-faa" onClick={() => {
            const ids = new Set(faaReady.map((r) => r.cid));
            setRules((rs) => rs.map((r) => (ids.has(r.cid) ? { ...r, include: true } : r)));
          }}>Accept all {faaReady.length} FAA suggestions</button>
        </div>
      )}

      <div className="sop-split">
        <div className="sop-list">
          <div className="sop-filters">
            {FILTERS.map(([k, t]) => (
              <button type="button" key={k} className={filter === k ? "is-on" : ""} onClick={() => setFilter(k)}>
                {t} <span>{counts[k]}</span>
              </button>
            ))}
          </div>
          <LessonFilter meta={meta} rules={rules} value={lessonSel} onChange={setLessonSel} />
          <TagFilter meta={meta} rules={rules} value={tagSel} onChange={setTagSel} />
          <div className="sop-list__rows">
            {visible.length === 0 && <div className="muted sop-empty">{filter === "blocked" ? "Nothing needs fixing." : "No rules here."}</div>}
            {groupByPage(visible).map(([page, rs]) => (
              <div key={page}>
                <div className="sop-pagehdr">{page === "manual" ? (d.imported_from ? "No page" : "Added by you") : `Page ${page}`}</div>
                {rs.map((r) => (
                  <RuleRow key={r.cid} meta={meta} rule={r} selected={r.cid === sel} onSelect={() => setSel(r.cid)} dim={!r.include}
                    status={!r.include ? "Set aside" : r.blocked ? "Needs fixing" : null}
                    statusClass={!r.include ? "" : "is-bad"}
                    leading={<input type="checkbox" className="sop-include" checked={!!r.include} title="Use this rule (untick to set it aside — it is still saved)"
                      aria-label={`Include ${r.title}`} onChange={(e) => update(r.cid, { include: e.target.checked })} />} />
                ))}
              </div>
            ))}
          </div>
          <div className="sop-list__foot">
            <button type="button" className="secondary" onClick={addRule}>+ Add rule</button>
            <span className="muted sop-small"><kbd>J</kbd>/<kbd>K</kbd> move · <kbd>X</kbd> use / set aside</span>
          </div>
        </div>

        <div className="sop-detail">
          {!cur ? <div className="card muted">Select a rule to review it.</div> : (
            <>
              <div className="card">
                <div className="sop-detail__head">
                  <h2>{cur.title || "Untitled rule"}</h2>
                  <span className="sop-chip">{cur.origin === "manual" ? (cur.imported ? "Written by hand" : "Added by you") : "Drafted by LLM"}</span>
                  {cur.imported && <span className="sop-chip">Imported</span>}
                  {cur.suggested && <span className="sop-chip sop-chip--faa" title={cur.suggested.source}>FAA suggestion: {cur.suggested.shown}</span>}
                  {cur.kind === "gaze" && <span className="sop-chip" title="Checked with the eye tracker against the AOI zone given this role">Scan path</span>}
                </div>
                {cur.blocked && <div className="sop-error">Needs fixing: {cur.blocked}</div>}
                <LlmNote note={cur.note} />
                {(cur.issues || []).map((i, n) => <div key={n} className="sop-warn">{i}</div>)}
                <RuleEditor meta={meta} rule={cur} onChange={(r) => update(cur.cid, r)}
                  quoteEditable={cur.origin === "manual" || !!cur.blocked || !!cur.imported} />
                <div className="sop-btnrow">
                  {cur.include
                    ? <button type="button" className="secondary" title="Saved with the rule set but not used in lessons; you can edit it and add it back later"
                        onClick={() => update(cur.cid, { include: false })}>Set aside</button>
                    : <button type="button" onClick={() => update(cur.cid, { include: true })}>Use this rule</button>}
                  {cur.origin === "manual" && (
                    <button type="button" className="secondary sop-danger" onClick={() => { setRules((rs) => rs.filter((r) => r.cid !== cur.cid)); setSel(null); }}>Delete</button>
                  )}
                </div>
              </div>
              {(d.has_pdf !== false || hasPages) && (
                <SourceViewer page={cur.source_page} quote={cur.source_quote} noImage={d.has_pdf === false}
                  imageUrl={(n, q) => sopApi.draftPageImage(id, n, q)} loadText={(n) => sopApi.draftPage(id, n)} />
              )}
            </>
          )}
        </div>
      </div>

      {confirm === "save" && (
        <ConfirmDialog title={`Save for ${target}?`} confirmLabel={counts.included ? `Save ${counts.included} rules` : "Save"}
          message={(isHB
            ? `${counts.included} rules are written to the database as "${name || d.name}" and become the active ${bookShort(meta, book)} rules, used in every lesson on top of each aircraft's own POH rules. A ${bookShort(meta, book)} rule set saved before is kept, but inactive.`
            : `${counts.included} rules are written to the database as "${name || d.name}" and become the active SOP rules for ${acName(aircraft)}. The rules this aircraft used before are kept, but inactive.`)
            + (counts.included ? "" : " No rule is in use yet, so lessons are not affected until you add rules back.")
            + (counts.aside ? ` ${counts.aside} set-aside rule${counts.aside === 1 ? " is" : "s are"} saved with it but not used in lessons: you can edit them and add them back later on the SOP Rules page.` : "")}
          onCancel={() => setConfirm(null)} onConfirm={() => { setConfirm(null); doSave(); }} />
      )}
      {confirm === "discard" && (
        <ConfirmDialog title="Discard this draft?" confirmLabel="Discard"
          message="The draft and all your edits are deleted. Nothing in the database changes."
          onCancel={() => setConfirm(null)} onConfirm={async () => { setConfirm(null); await sopApi.discardDraft(id).catch(() => {}); onGone(); }} />
      )}
      {confirm === "reparse" && (
        <ConfirmDialog title="Run the LLM again?" confirmLabel="Run again"
          message={`The LLM${nextModel ? ` (${nextModel})` : ""} re-drafts every rule from the same pages (${compressPages(d.selected_pages || [])}). Your edits to LLM-drafted rules are replaced; rules you added by hand are kept. To read different pages, use "Change pages or model" instead; the model is switched on the Upload page.`}
          onCancel={() => setConfirm(null)}
          onConfirm={async () => {
            setConfirm(null);
            try {
              await sopApi.reparse(id);
              loaded.current = false;
              setD({ ...d, status: "processing", stage: "queued", _restart: Date.now() });
            } catch (e) { setErr(e.message); }
          }} />
      )}
    </div>
  );
}

function groupByPage(rules) {
  const groups = new Map();
  for (const r of rules) {
    const k = r.origin === "manual" && !r.source_page ? "manual" : r.source_page ?? "manual";
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(r);
  }
  return [...groups.entries()].sort((a, b) => (a[0] === "manual" ? 1e9 : a[0]) - (b[0] === "manual" ? 1e9 : b[0]));
}
