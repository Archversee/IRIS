import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { sopApi } from "./sopApi.js";
import { editable, sameRule, describe, bookShort } from "./ruleText.js";
import RuleEditor from "./RuleEditor.jsx";
import SourceViewer from "./SourceViewer.jsx";
import { RuleRow, EditorName, useKeyNav, TagFilter, tagMatch, LlmNote, ExportMenu, LessonFilter, lessonMatch } from "./parts.jsx";
import ConfirmDialog from "../live/components/ConfirmDialog.jsx";
import LiveChecksToggle from "./LiveChecksToggle.jsx";
import "../live/live.css";
import "./sop.css";

// /sop/rules -- saved flight rules per aircraft, plus the training-handbook
// rules (tab "_general") that apply to every aircraft. Rules are edited in
// place; every change is recorded in flight_rule_history (History tab).
// Rules that were set aside (in the draft, or here) are kept with the set:
// never used in a lesson, editable even while incomplete, and can be added
// back once they pass the full check.
const GENERAL = "_general";
export default function SopRules() {
  const [params, setParams] = useSearchParams();
  const [meta, setMeta] = useState(null);
  const [sets, setSets] = useState(null);
  const [err, setErr] = useState(null);
  const aircraft = params.get("aircraft") || meta?.aircraft?.[0]?.id || "";
  const setId = params.get("set");
  const justSaved = params.get("saved") === "1";

  useEffect(() => { sopApi.meta().then(setMeta).catch((e) => setErr(e.message)); }, []);
  const loadSets = useCallback(() => {
    sopApi.listSets().then(setSets).catch((e) => setErr(e.message));
  }, []);
  useEffect(loadSets, [loadSets]);

  const go = (patch) => {
    const next = { aircraft, ...(setId ? { set: setId } : {}), ...patch };
    Object.keys(next).forEach((k) => next[k] == null && delete next[k]);
    setParams(next);
  };

  const isGen = aircraft === GENERAL;
  const forAc = (sets || []).filter((s) => (isGen ? s.scope === "general" : s.aircraft_profile === aircraft && s.scope !== "general"));
  const current = forAc.find((s) => s.id === setId) || forAc.find((s) => s.active) || forAc[0] || null;
  const acName = (a) => (a === GENERAL ? "every aircraft" : meta?.aircraft.find((x) => x.id === a)?.name || a);
  const genActive = (sets || []).filter((s) => s.scope === "general" && s.active);

  return (
    <div className="sop">
      <header className="sop-head">
        <div>
          <div className="sop-crumb">Settings · SOP</div>
          <h1>SOP rules</h1>
        </div>
        <div className="spacer" />
        <EditorName />
        <Link to="/sop/new" className="sop-link-btn sop-link-btn--primary">Upload SOP</Link>
      </header>
      <LiveChecksToggle />
      {err && <div className="sop-error">{err}</div>}
      {meta && sets && (
        <>
          <nav className="sop-actabs" aria-label="Aircraft">
            {meta.aircraft.map((a) => {
              const act = sets.find((s) => s.aircraft_profile === a.id && s.active);
              return (
                <button type="button" key={a.id} className={a.id === aircraft ? "is-on" : ""} onClick={() => setParams({ aircraft: a.id })}>
                  <b>{a.name}</b>
                  <span>{act ? `${act.name} · ${act.n_enabled} rules` : "No active rules"}</span>
                </button>
              );
            })}
            <button type="button" className={"sop-actabs__gen" + (isGen ? " is-on" : "")} onClick={() => setParams({ aircraft: GENERAL })}>
              <b>Training handbooks</b>
              <span>{genActive.length ? `${genActive.length} active · ${genActive.reduce((n, s) => n + s.n_enabled, 0)} rules · every aircraft` : "None active · would apply to every aircraft"}</span>
            </button>
          </nav>
          {justSaved && current && (
            <div className="sop-ok">Saved. <b>{current.name}</b> is now {isGen
              ? <>the active {bookShort(meta, current.handbook)} rule set — every lesson uses it from the next start, on top of the aircraft's own rules.</>
              : <>the active rule set for {acName(aircraft)} — lessons in this aircraft use it from the next start.</>}</div>
          )}
          {isGen && current && (
            <p className="muted sop-small sop-gen-note">Rules from training handbooks apply to every aircraft, together with that aircraft's own POH rules. One rule set per handbook is active at a time.</p>
          )}
          {!current ? (
            <div className="card sop-emptystate">
              <h3>{isGen ? "No training-handbook rules yet" : `No rules for ${acName(aircraft)} yet`}</h3>
              <p className="muted">{isGen
                ? "Upload a training handbook (the FAA Airplane Flying Handbook, Instrument Flying Handbook, PHAK, or your school's own) and the local LLM drafts circuit and scan-path rules that apply to every aircraft."
                : "Upload this aircraft's SOP and the local LLM drafts the rules for you to check."}</p>
              <Link to="/sop/new" className="sop-link-btn sop-link-btn--primary">{isGen ? "Upload a handbook" : "Upload SOP"}</Link>
            </div>
          ) : (
            <RuleSet key={current.id} meta={meta} setInfo={current} others={forAc}
              onPick={(id) => go({ set: id, saved: null })} onChanged={loadSets} acName={acName} />
          )}
        </>
      )}
    </div>
  );
}

function SetBar({ s, meta, others, onPick, onChanged, setErr }) {
  const [renaming, setRenaming] = useState(null);
  const [confirm, setConfirm] = useState(null);
  async function act(fn) {
    try { await fn(); onChanged(); } catch (e) { setErr(e.message); }
  }
  return (
    <div className="card sop-setbar">
      <div className="sop-field">
        <label htmlFor="sop-set">Rule set</label>
        <select id="sop-set" value={s.id} onChange={(e) => onPick(e.target.value)}>
          {others.map((o) => <option key={o.id} value={o.id}>{o.scope === "general" ? `${bookShort(meta, o.handbook)}: ` : ""}{o.name}{o.active ? " — active" : ""}</option>)}
        </select>
      </div>
      <div className="sop-setbar__meta">
        {s.active ? <span className="sop-chip sop-chip--ok">{s.scope === "general" ? "Active — used in every lesson" : "Active — used in lessons"}</span> : <span className="sop-chip">Inactive</span>}
        <span className="muted sop-small">
          {s.filename ? `From ${s.filename}` : s.llm_model ? "Imported from a rules file" : "Typed in by hand"} · {s.n_enabled}/{s.n_rules} rules on{s.n_set_aside ? ` · ${s.n_set_aside} set aside` : ""} ·
          last change {new Date(s.updated_at).toLocaleString()}{s.updated_by ? ` by ${s.updated_by}` : ""}
        </span>
      </div>
      <div className="sop-btnrow">
        {renaming !== null ? (
          <>
            <input aria-label="New name" value={renaming} onChange={(e) => setRenaming(e.target.value)} autoFocus />
            <button type="button" onClick={() => act(() => sopApi.renameSet(s.id, renaming)).then(() => setRenaming(null))}>Rename</button>
            <button type="button" className="secondary" onClick={() => setRenaming(null)}>Cancel</button>
          </>
        ) : <button type="button" className="secondary" onClick={() => setRenaming(s.name)}>Rename</button>}
        {s.active
          ? <button type="button" className="secondary" onClick={() => setConfirm("deactivate")}>Deactivate</button>
          : <button type="button" onClick={() => act(() => sopApi.activateSet(s.id))}>Make active</button>}
        <ExportMenu meta={meta} setErr={setErr} load={() => sopApi.getSet(s.id)} />
        {!s.active && <button type="button" className="secondary sop-danger" onClick={() => setConfirm("delete")}>Delete</button>}
      </div>
      {confirm === "deactivate" && (
        <ConfirmDialog title="Deactivate these rules?" confirmLabel="Deactivate"
          message={s.scope === "general"
            ? `Lessons will run without ${bookShort(meta, s.handbook)} rules until you make one of its rule sets active again.`
            : "Lessons in this aircraft will run without SOP rules until you make a rule set active again."}
          onCancel={() => setConfirm(null)} onConfirm={() => { setConfirm(null); act(() => sopApi.deactivateSet(s.id)); }} />
      )}
      {confirm === "delete" && (
        <ConfirmDialog title={`Delete "${s.name}"?`} confirmLabel="Delete"
          message="This inactive rule set, its rules and their history are deleted permanently."
          onCancel={() => setConfirm(null)}
          onConfirm={() => { setConfirm(null); act(async () => { await sopApi.deleteSet(s.id); onPick(null); }); }} />
      )}
    </div>
  );
}

function RuleSet({ meta, setInfo, others, onPick, onChanged }) {
  const [data, setData] = useState(null);
  const [sel, setSel] = useState(null);
  const [edit, setEdit] = useState(null);         // local copy of the selected rule
  const [tab, setTab] = useState("edit");
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [tagSel, setTagSel] = useState([]);
  const [lessonSel, setLessonSel] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(null);
  const [pendingSel, setPendingSel] = useState(null);
  const [warn, setWarn] = useState([]);           // warnings from the last save (not stored)
  const [blocked, setBlocked] = useState(null);   // set-aside rule: what stops it being added back (from the last save)

  const load = useCallback(async (keepSel) => {
    try {
      const x = await sopApi.getSet(setInfo.id);
      setData(x);
      const pick = x.rules.find((r) => r.id === keepSel) || x.rules.find((r) => !r.set_aside) || x.rules[0] || null;
      setSel(pick?.id ?? null);
      setEdit(pick ? { ...pick } : null);
    } catch (e) { setErr(e.message); }
  }, [setInfo.id]);
  useEffect(() => { load(null); }, [load]);

  const rules = data?.rules || [];
  const saved = rules.find((r) => r.id === sel) || null;
  const dirty = !!(edit && saved && !sameRule(edit, saved));
  const q = search.trim().toLowerCase();
  // "All" / "On" / "Switched off" are the rules that belong to the set; set-aside rules have their own list
  const visible = rules.filter((r) =>
    (filter === "aside" ? r.set_aside : !r.set_aside && (filter === "all" || (filter === "on" ? r.enabled : !r.enabled))) &&
    (!q || `${r.title} ${describe(meta, r)} ${r.source_quote || ""}`.toLowerCase().includes(q)) && tagMatch(r, tagSel) && lessonMatch(r, lessonSel));
  const counts = useMemo(() => {
    const used = rules.filter((r) => !r.set_aside);
    return { all: used.length, on: used.filter((r) => r.enabled).length, off: used.filter((r) => !r.enabled).length,
      aside: rules.length - used.length };
  }, [rules]);

  function select(id) {
    if (id === sel) return;
    if (dirty) { setPendingSel(id); return; }
    const r = rules.find((x) => x.id === id);
    setSel(id); setEdit(r ? { ...r } : null); setErr(null); setWarn([]); setBlocked(null);
  }
  const onKey = useKeyNav(visible, sel, (r) => r.id, select);
  useEffect(() => {
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  async function save(patch) {
    setBusy(true); setErr(null);
    try {
      const body = patch || editable(edit);
      const out = await sopApi.updateRule(sel, body);
      setData((d) => ({ ...d, rules: d.rules.map((r) => (r.id === out.id ? out : r)) }));
      setEdit({ ...out });
      setWarn(out.issues || []);
      setBlocked(out.set_aside ? out.blocked || null : null);
      // follow the rule to the list it now belongs to
      if (out.set_aside && filter !== "aside") setFilter("aside");
      if (!out.set_aside && filter === "aside") setFilter("all");
      onChanged();
    } catch (e) {
      setErr(e.detail?.message || e.message);
    }
    setBusy(false);
  }
  async function addRule() {
    setErr(null);
    try {
      const out = await sopApi.addRule(setInfo.id, { title: "New rule", kind: "limit", metric: "ias", max_value: 100, severity: "caution" });
      setData((d) => ({ ...d, rules: [...d.rules, out] }));
      setFilter("all"); setSearch(""); setTagSel([]); setSel(out.id); setEdit({ ...out }); setTab("edit"); setWarn(out.issues || []);
      onChanged();
    } catch (e) { setErr(e.detail?.message || e.message); }
  }
  async function del() {
    try {
      await sopApi.deleteRule(sel);
      const rest = rules.filter((r) => r.id !== sel);
      setData((d) => ({ ...d, rules: rest }));
      setSel(rest[0]?.id ?? null); setEdit(rest[0] ? { ...rest[0] } : null);
      onChanged();
    } catch (e) { setErr(e.message); }
  }

  if (!data) return <div className="muted">Loading rules…</div>;

  return (
    <>
      <SetBar s={setInfo} meta={meta} others={others} onPick={onPick} onChanged={() => { onChanged(); load(sel); }} setErr={setErr} />
      {err && <div className="sop-error">{err}</div>}
      <div className="sop-split">
        <div className="sop-list">
          <input className="sop-search" type="search" placeholder="Search rules…" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search rules" />
          <div className="sop-filters">
            {[["all", "All"], ["on", "On"], ["off", "Switched off"], ["aside", "Set aside"]].map(([k, t]) => (
              <button type="button" key={k} className={filter === k ? "is-on" : ""} onClick={() => setFilter(k)}
                title={k === "aside" ? "Kept with this rule set but not used in lessons. Edit them and add them back when they're right." : ""}>
                {t} <span>{counts[k]}</span></button>
            ))}
          </div>
          <LessonFilter meta={meta} rules={rules} value={lessonSel} onChange={setLessonSel} />
          <TagFilter meta={meta} rules={rules} value={tagSel} onChange={setTagSel} />
          <div className="sop-list__rows">
            {visible.length === 0 && <div className="muted sop-empty">{filter === "aside" ? "No rules are set aside." : "No rules match."}</div>}
            {visible.map((r) => (
              <RuleRow key={r.id} meta={meta} rule={r} selected={r.id === sel} onSelect={() => select(r.id)}
                dim={!r.enabled || r.set_aside}
                status={r.id === sel && dirty ? "Unsaved" : r.set_aside ? "Set aside" : !r.enabled ? "Off" : null}
                statusClass={r.id === sel && dirty ? "is-warn" : ""} />
            ))}
          </div>
          <div className="sop-list__foot">
            <button type="button" className="secondary" onClick={addRule}>+ Add rule</button>
            <span className="muted sop-small"><kbd>J</kbd>/<kbd>K</kbd> move</span>
          </div>
        </div>

        <div className="sop-detail">
          <div className="sop-tabs">
            <button type="button" className={tab === "edit" ? "is-on" : ""} onClick={() => setTab("edit")}>Rule</button>
            <button type="button" className={tab === "history" ? "is-on" : ""} onClick={() => setTab("history")}>History{saved ? " of this rule" : ""}</button>
            <button type="button" className={tab === "all" ? "is-on" : ""} onClick={() => setTab("all")}>All changes</button>
          </div>
          {tab === "edit" && (!edit ? <div className="card muted">This rule set has no rules yet.</div> : (
            <>
              <div className="card">
                <div className="sop-detail__head">
                  <h2>{edit.title || "Untitled rule"}</h2>
                  {saved?.set_aside ? <span className="sop-chip sop-chip--warn">Set aside · not used in lessons</span> : (
                    <label className="sop-toggle">
                      <input type="checkbox" checked={!!saved?.enabled} disabled={busy || dirty}
                        title={dirty ? "Save or revert your edits first" : ""}
                        onChange={(e) => save({ enabled: e.target.checked })} />
                      <span>{saved?.enabled ? "On" : "Off"}</span>
                    </label>
                  )}
                </div>
                <p className="sop-plain">{describe(meta, edit)}</p>
                {saved?.set_aside && (
                  <div className="sop-aside">
                    <b>Why it was set aside:</b> {saved.set_aside_reason || "No reason recorded."}
                    <div className="muted sop-small">You can edit and save it as it is. <b>Add back</b> puts it into the rule set once it passes every check.</div>
                  </div>
                )}
                {saved?.set_aside && blocked && <div className="sop-error">Before it can be added back: {blocked}</div>}
                {warn.map((w, i) => <div key={i} className="sop-warn">{saved?.set_aside ? "Check" : "Saved, but check"}: {w}</div>)}
                <LlmNote note={saved?.note} />
                <RuleEditor meta={meta} rule={edit} onChange={setEdit} quoteEditable />
                <div className="sop-btnrow">
                  <button type="button" disabled={!dirty || busy} onClick={() => save()}>{busy ? "Saving…" : "Save changes"}</button>
                  <button type="button" className="secondary" disabled={!dirty || busy} onClick={() => setEdit({ ...saved })}>Revert</button>
                  {saved?.set_aside
                    ? <button type="button" disabled={busy} title="Checks the rule, then puts it into the rule set (your unsaved edits are saved with it)"
                        onClick={() => save({ ...editable(edit), set_aside: false })}>Add back to the rule set</button>
                    : <button type="button" className="secondary" disabled={busy} title="Keep the rule with this set but stop using it in lessons"
                        onClick={() => save({ ...(dirty ? editable(edit) : {}), set_aside: true })}>Set aside</button>}
                  <div className="spacer" />
                  <button type="button" className="secondary sop-danger" onClick={() => setConfirm("delete")}>Delete rule</button>
                </div>
                <div className="muted sop-small sop-meta-line">
                  {saved.origin === "llm" ? "Drafted by the LLM" : "Added by hand"}
                  {saved.updated_by ? ` · last edited by ${saved.updated_by}` : ""} · {new Date(saved.updated_at).toLocaleString()}
                </div>
              </div>
              {data.document_id && edit.source_page && (
                <SourceViewer page={edit.source_page} quote={edit.source_quote}
                  imageUrl={(n, qq) => sopApi.docPageImage(data.document_id, n, qq)} loadText={(n) => sopApi.docPage(data.document_id, n)} />
              )}
            </>
          ))}
          {tab === "history" && saved && <History meta={meta} setId={setInfo.id} ruleId={saved.id} />}
          {tab === "all" && <History meta={meta} setId={setInfo.id} />}
        </div>
      </div>
      {confirm === "delete" && (
        <ConfirmDialog title={`Delete "${saved?.title}"?`} confirmLabel="Delete"
          message="The rule is removed from this rule set. The deletion is kept in the history. To stop using a rule but keep it, switch it off instead."
          onCancel={() => setConfirm(null)} onConfirm={() => { setConfirm(null); del(); }} />
      )}
      {pendingSel !== null && (
        <ConfirmDialog title="Discard your edits?" confirmLabel="Discard"
          message="This rule has unsaved changes."
          onCancel={() => setPendingSel(null)}
          onConfirm={() => { const id = pendingSel; setPendingSel(null); const r = rules.find((x) => x.id === id); setSel(id); setEdit(r ? { ...r } : null); }} />
      )}
    </>
  );
}

const ACTION_TXT = { create: "Created", update: "Edited", delete: "Deleted", activate: "Made active", deactivate: "Deactivated", rename: "Renamed" };
const FIELD_TXT = { tags: "tags", title: "title", kind: "type", metric: "measured value", target: "target", tol_hi: "+ tolerance", tol_lo: "− tolerance",
  min_value: "minimum", max_value: "maximum", op: "comparison", value: "value", gate_phase: "deadline phase", capture_at: "moment",
  phases: "phases", conditions: "conditions", severity: "severity", source_quote: "source", source_page: "page", note: "note", enabled: "on/off", name: "name",
  aoi: "look at", gaze_measure: "gaze check", set_aside: "set aside", set_aside_reason: "set-aside reason" };

function changedFields(b, a) {
  if (!b || !a) return [];
  return Object.keys(FIELD_TXT).filter((k) => k in a && JSON.stringify(b[k]) !== JSON.stringify(a[k]))
    .map((k) => ({ k, from: b[k], to: a[k] }));
}
const show = (v, k) => (k === "set_aside" ? (v ? "yes" : "no") : v == null || v === "" ? "—" : Array.isArray(v) ? (v.length ? v.map((x) => (typeof x === "object" ? `${x.metric} ${x.op} ${x.value}` : x)).join(", ") : "—") : typeof v === "boolean" ? (v ? "on" : "off") : String(v));

function History({ meta, setId, ruleId }) {
  const [rows, setRows] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    setRows(null);
    sopApi.history(setId, ruleId).then(setRows).catch((e) => setErr(e.message));
  }, [setId, ruleId]);
  if (err) return <div className="sop-error">{err}</div>;
  if (!rows) return <div className="muted">Loading history…</div>;
  if (!rows.length) return <div className="card muted">No changes recorded.</div>;
  return (
    <div className="card sop-history">
      {rows.map((h) => {
        const snap = h.after || h.before;
        const diffs = h.action === "update" || h.action === "rename" ? changedFields(h.before, h.after) : [];
        return (
          <div key={h.id} className="sop-hist">
            <div className="sop-hist__head">
              <b>{ACTION_TXT[h.action] || h.action}</b>
              {!ruleId && snap?.title && <span> · {snap.title}</span>}
              <span className="spacer" />
              <span className="muted sop-small">{h.changed_by || "unknown"} · {new Date(h.changed_at).toLocaleString()}</span>
            </div>
            {diffs.length > 0 && (
              <ul className="sop-hist__diff">
                {diffs.map((x) => <li key={x.k}><span className="muted">{FIELD_TXT[x.k]}:</span> <s>{show(x.from, x.k)}</s> → <b>{show(x.to, x.k)}</b></li>)}
              </ul>
            )}
            {(h.action === "create" || h.action === "delete") && snap?.kind && <div className="muted sop-small">{describe(meta, snap)}</div>}
            {h.action === "deactivate" && h.after?.replaced_by && <div className="muted sop-small">Replaced by a newly saved rule set</div>}
          </div>
        );
      })}
    </div>
  );
}
