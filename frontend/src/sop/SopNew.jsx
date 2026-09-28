import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { sopApi, ApiError } from "./sopApi.js";
import { editable } from "./ruleText.js";
import RuleEditor from "./RuleEditor.jsx";
import SourceViewer from "./SourceViewer.jsx";
import { RuleRow, EditorName, useKeyNav } from "./parts.jsx";
import ConfirmDialog from "../live/components/ConfirmDialog.jsx";
import "../live/live.css";
import "./sop.css";

// /sop/new -- upload an SOP, let the local LLM draft rules, review and edit
// them, then Save. Until Save, the draft only exists as a local file on the
// MAVIS machine (resumable via ?draft=<id>); nothing is in the database.
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
          <h1>Upload SOP</h1>
        </div>
        <div className="spacer" />
        <EditorName />
        <Link to="/sop/rules" className="sop-link-btn">Saved rules →</Link>
      </header>
      {err && <div className="sop-error">SOP service unavailable: {err}</div>}
      {meta && (draftId
        ? <DraftReview key={draftId} id={draftId} meta={meta} onGone={leaveDraft} />
        : <UploadStart onCreated={(id) => setParams({ draft: id })} />)}
    </div>
  );
}

/* ------------------------------------------------------------------ upload */
function Health({ status }) {
  if (!status) return <div className="sop-health"><span className="sop-chip">Checking local services…</span></div>;
  const items = [
    [status.llm, `Local LLM · ${status.model}`, status.llm ? "" : "Not running in Ollama — rules can still be added by hand"],
    [status.ocr, "OCR (Tesseract)", status.ocr_detail || (status.ocr ? "" : "Not installed — scanned pages will be skipped")],
    [status.db, "Rules database", status.db ? "" : "Postgres not reachable — you can review, but Save will fail"],
    [!status.flying, "No lesson in progress", status.flying ? "Uploading is paused until the lesson ends" : ""],
  ];
  return (
    <div className="sop-health">
      {items.map(([ok, t, why]) => (
        <span key={t} className={"sop-chip " + (ok ? "sop-chip--ok" : "sop-chip--bad")} title={why}>{t}</span>
      ))}
      {status.busy && <span className="sop-chip sop-chip--warn">Another SOP is being processed</span>}
      {!status.ocr && status.ocr_detail && <div className="sop-warn sop-health__why">OCR unavailable: {status.ocr_detail}</div>}
    </div>
  );
}

function UploadStart({ onCreated }) {
  const [status, setStatus] = useState(null);
  const [drafts, setDrafts] = useState(null);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [discard, setDiscard] = useState(null);
  const input = useRef(null);

  const refresh = useCallback(() => {
    sopApi.status().then(setStatus).catch(() => setStatus(null));
    sopApi.listDrafts().then(setDrafts).catch((e) => setErr(e.message));
  }, []);
  useEffect(refresh, [refresh]);

  async function upload(file) {
    if (!file) return;
    if (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf") { setErr("Choose the SOP as a PDF file."); return; }
    setBusy(true); setErr(null);
    try {
      const d = await sopApi.upload(file);
      onCreated(d.id);
    } catch (e) {
      setErr(e.message); setBusy(false);
    }
  }

  return (
    <div className="sop-start">
      <Health status={status} />
      <div
        className={"sop-drop" + (over ? " is-over" : "") + (busy ? " is-busy" : "")}
        role="button" tabIndex={0}
        onClick={() => !busy && input.current?.click()}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") input.current?.click(); }}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); upload(e.dataTransfer.files[0]); }}
      >
        <input ref={input} type="file" accept="application/pdf,.pdf" hidden onChange={(e) => upload(e.target.files[0])} />
        <div className="sop-drop__icon" aria-hidden>⇪</div>
        <h2>{busy ? "Uploading…" : "Drop the aircraft SOP here"}</h2>
        <p className="muted">PDF up to 50 MB · typed or scanned pages · processed on this machine only</p>
        <button type="button" disabled={busy}>Choose PDF</button>
      </div>
      {err && <div className="sop-error">{err}</div>}
      <ol className="sop-steps">
        <li><b>Read</b> every page (OCR for scanned ones)</li>
        <li><b>Draft</b> rules with the local LLM</li>
        <li><b>Review &amp; edit</b> — nothing is stored yet</li>
        <li><b>Save</b> to the database for an aircraft</li>
      </ol>

      <section className="card">
        <h3>Unsaved drafts</h3>
        {drafts === null ? <div className="muted sop-small">Loading…</div> :
          drafts.length === 0 ? <div className="muted sop-small">None — every uploaded SOP has been saved or discarded.</div> : (
            <table className="sop-table">
              <thead><tr><th>SOP</th><th>Status</th><th>Rules</th><th>Last change</th><th /></tr></thead>
              <tbody>
                {drafts.map((d) => (
                  <tr key={d.id}>
                    <td><b>{d.name}</b><div className="muted sop-small">{d.filename}</div></td>
                    <td>{d.status === "processing" ? `Processing… ${d.detail || ""}` : d.status === "failed" ? <span className="err">Failed</span> : "Ready to review"}</td>
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
const STAGES = [["extracting", "Read pages"], ["parsing", "Draft rules"], ["validating", "Check rules"], ["done", "Ready"]];
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
  const [sel, setSel] = useState(null);
  const [filter, setFilter] = useState("all");
  const [saveState, setSaveState] = useState("saved"); // saved | pending | saving | error
  const [err, setErr] = useState(null);
  const [confirm, setConfirm] = useState(null);
  const [saving, setSaving] = useState(false);
  const [acWarn, setAcWarn] = useState([]);
  const loaded = useRef(false);
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
  const editKey = useMemo(() => JSON.stringify([name, aircraft, rules.map((r) => [r.cid, r.include, editable(r)])]),
    [name, aircraft, rules]);
  useEffect(() => {
    if (!loaded.current || d?.status !== "ready") return;
    const v = ++version.current;
    setSaveState("pending");
    const t = setTimeout(async () => {
      setSaveState("saving");
      try {
        const x = await sopApi.putDraft(id, { name, aircraft_profile: aircraft, rules: rules.map((r) => ({ cid: r.cid, include: r.include, ...editable(r) })) });
        if (v !== version.current) return;            // newer edits are on their way
        const byCid = Object.fromEntries(x.rules.map((r) => [r.cid, r]));
        // take only what the server computes; never overwrite what's being typed
        setRules((cur) => cur.map((r) => byCid[r.cid] ? { ...r, issues: byCid[r.cid].issues, blocked: byCid[r.cid].blocked, quote_score: byCid[r.cid].quote_score } : r));
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
    filter === "all" ? true : filter === "blocked" ? r.include && r.blocked :
      filter === "warn" ? r.include && !r.blocked && (r.issues || []).length : !r.include);
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
  const canSave = aircraft && counts.included > 0 && counts.blocked === 0 && !saving;

  async function doSave() {
    setSaving(true); setErr(null);
    try {
      // flush the latest edits first, then save exactly what's on screen
      await sopApi.putDraft(id, { name, aircraft_profile: aircraft, rules: rules.map((r) => ({ cid: r.cid, include: r.include, ...editable(r) })) });
      const res = await sopApi.saveDraft(id, { aircraft_profile: aircraft, name: name || d.name });
      nav(`/sop/rules?aircraft=${encodeURIComponent(aircraft)}&set=${res.rule_set_id}&saved=1`);
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
      metric: "ias", severity: "caution", phases: [], conditions: [], issues: [], blocked: "limit rule needs a min or a max" };
    setRules((rs) => [...rs, r]); setFilter("all"); setSel(r.cid);
  }

  return (
    <div className="sop-review">
      <div className="card sop-savebar">
        <div className="sop-field">
          <label htmlFor="sop-name">Rule set name</label>
          <input id="sop-name" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="sop-field">
          <label htmlFor="sop-ac">Aircraft</label>
          <select id="sop-ac" value={aircraft} onChange={(e) => setAircraft(e.target.value)}>
            <option value="">Choose the aircraft…</option>
            {meta.aircraft.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </div>
        <div className="sop-savebar__status">
          <span className={"sop-autosave sop-autosave--" + saveState}>
            {saveState === "saved" ? "Draft saved on this machine" : saveState === "error" ? "Couldn't save draft" : "Saving draft…"}
          </span>
          <span className="muted sop-small">{counts.included} of {counts.all} rules will be saved{counts.blocked ? ` · ${counts.blocked} need fixing` : ""}</span>
        </div>
        <div className="sop-btnrow">
          <button type="button" className="secondary" onClick={() => setConfirm("reparse")}>Run LLM again</button>
          <button type="button" className="secondary" onClick={() => setConfirm("discard")}>Discard</button>
          <button type="button" disabled={!canSave} onClick={() => setConfirm("save")}
            title={!aircraft ? "Choose the aircraft first" : counts.blocked ? "Fix or set aside the rules that need fixing" : ""}>
            {saving ? "Saving…" : `Save ${counts.included} rules`}
          </button>
        </div>
      </div>

      {(d.warnings || []).map((w, i) => <div key={i} className="sop-warn">{w}</div>)}
      {aircraft && acWarn.map((w, i) => <div key={"ac" + i} className="sop-warn">Check against {acName(aircraft)}: {w}</div>)}
      {err && <div className="sop-error">{err}</div>}

      <div className="sop-split">
        <div className="sop-list">
          <div className="sop-filters">
            {FILTERS.map(([k, t]) => (
              <button type="button" key={k} className={filter === k ? "is-on" : ""} onClick={() => setFilter(k)}>
                {t} <span>{counts[k]}</span>
              </button>
            ))}
          </div>
          <div className="sop-list__rows">
            {visible.length === 0 && <div className="muted sop-empty">{filter === "blocked" ? "Nothing needs fixing." : "No rules here."}</div>}
            {groupByPage(visible).map(([page, rs]) => (
              <div key={page}>
                <div className="sop-pagehdr">{page === "manual" ? "Added by you" : `Page ${page}`}</div>
                {rs.map((r) => (
                  <RuleRow key={r.cid} meta={meta} rule={r} selected={r.cid === sel} onSelect={() => setSel(r.cid)} dim={!r.include}
                    status={!r.include ? "Set aside" : r.blocked ? "Needs fixing" : null}
                    statusClass={!r.include ? "" : "is-bad"}
                    leading={<input type="checkbox" className="sop-include" checked={!!r.include} title="Include when saving"
                      aria-label={`Include ${r.title}`} onChange={(e) => update(r.cid, { include: e.target.checked })} />} />
                ))}
              </div>
            ))}
          </div>
          <div className="sop-list__foot">
            <button type="button" className="secondary" onClick={addRule}>+ Add rule</button>
            <span className="muted sop-small"><kbd>J</kbd>/<kbd>K</kbd> move · <kbd>X</kbd> include / set aside</span>
          </div>
        </div>

        <div className="sop-detail">
          {!cur ? <div className="card muted">Select a rule to review it.</div> : (
            <>
              <div className="card">
                <div className="sop-detail__head">
                  <h2>{cur.title || "Untitled rule"}</h2>
                  <span className="sop-chip">{cur.origin === "manual" ? "Added by you" : "Drafted by LLM"}</span>
                </div>
                {cur.blocked && <div className="sop-error">Needs fixing: {cur.blocked}</div>}
                {(cur.issues || []).map((i, n) => <div key={n} className="sop-warn">{i}</div>)}
                <RuleEditor meta={meta} rule={cur} onChange={(r) => update(cur.cid, r)}
                  quoteEditable={cur.origin === "manual" || !!cur.blocked} />
                <div className="sop-btnrow">
                  {cur.include
                    ? <button type="button" className="secondary" onClick={() => update(cur.cid, { include: false })}>Set aside (don't save)</button>
                    : <button type="button" onClick={() => update(cur.cid, { include: true })}>Include when saving</button>}
                  {cur.origin === "manual" && (
                    <button type="button" className="secondary sop-danger" onClick={() => { setRules((rs) => rs.filter((r) => r.cid !== cur.cid)); setSel(null); }}>Delete</button>
                  )}
                </div>
              </div>
              <SourceViewer page={cur.source_page} quote={cur.source_quote}
                imageUrl={(n, q) => sopApi.draftPageImage(id, n, q)} loadText={(n) => sopApi.draftPage(id, n)} />
            </>
          )}
        </div>
      </div>

      {confirm === "save" && (
        <ConfirmDialog title={`Save to ${acName(aircraft)}?`} confirmLabel={`Save ${counts.included} rules`}
          message={`${counts.included} rules are written to the database as "${name || d.name}" and become the active SOP rules for ${acName(aircraft)}. ${counts.aside} set-aside rules are dropped. The rules this aircraft used before are kept, but inactive.`}
          onCancel={() => setConfirm(null)} onConfirm={() => { setConfirm(null); doSave(); }} />
      )}
      {confirm === "discard" && (
        <ConfirmDialog title="Discard this draft?" confirmLabel="Discard"
          message="The draft and all your edits are deleted. Nothing in the database changes."
          onCancel={() => setConfirm(null)} onConfirm={async () => { setConfirm(null); await sopApi.discardDraft(id).catch(() => {}); onGone(); }} />
      )}
      {confirm === "reparse" && (
        <ConfirmDialog title="Run the LLM again?" confirmLabel="Run again"
          message="The LLM re-drafts every rule from the SOP. Your edits to LLM-drafted rules are replaced; rules you added by hand are kept."
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
