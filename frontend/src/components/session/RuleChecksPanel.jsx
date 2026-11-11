import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../../api/client.js";
import { sopApi } from "../../sop/sopApi.js";
import { useAuth } from "../../auth/AuthContext.jsx";
import IncidentView from "./IncidentView.jsx";

// SOP / handbook rule checks of one session (MAVIS sop/evaluate.py, stored by
// sop/results.py; instructor verdicts via backend/app/routers/rule_results.py).
// Instructors see every rule, can re-check with another strictness, overrule a
// verdict and sign the results off. Trainees see what to work on first, and
// whether their instructor has reviewed it yet.

const VERDICT = {
  fail: { label: "Not met", cls: "fail" },
  pass: { label: "Met", cls: "pass" },
  instructor: { label: "Instructor to assess", cls: "assess" },
  not_applicable: { label: "Didn't apply", cls: "na" },
  insufficient_data: { label: "No data", cls: "na" },
  not_evaluated: { label: "Not checked yet", cls: "na" },
};
const SEVERITY_ORDER = { warning: 0, caution: 1, advisory: 2 };
const GROUPS = [
  { key: "fail", title: "Not met" },
  { key: "instructor", title: "For the instructor to assess" },
  { key: "pass", title: "Met" },
  { key: "other", title: "Didn't apply / no data" },
];
const SHOW_ALL_KEY = "sopShowAllRules";
const groupOf = (v) => (v === "fail" || v === "pass" || v === "instructor" ? v : "other");

export function RuleChecksChip({ evaluation, missing, onClick }) {
  if (missing) return <button className="rc-chip" onClick={onClick}>SOP checks: not run</button>;
  if (!evaluation) return null;
  const b = evaluation.summary?.by_verdict || {};
  if (!evaluation.summary?.total || !(b.pass || b.fail || b.instructor)) {
    return (
      <button className="rc-chip has-warn" onClick={onClick} title="Open to see why">
        SOP: {evaluation.summary?.total ? "nothing could be judged" : "no rules checked"}
      </button>
    );
  }
  return (
    <button className={"rc-chip" + (b.fail ? " has-fail" : "")} onClick={onClick}
      title="SOP / handbook rule checks of this flight">
      SOP <span className="ok">{b.pass || 0} ✓</span>
      <span className="bad">{b.fail || 0} ✗</span>
      {b.instructor ? <span className="todo">{b.instructor} ?</span> : null}
      {evaluation.signed_off_at ? <span className="signed">signed off</span> : null}
    </button>
  );
}

export default function RuleChecksPanel({ sessionId, evaluation, missing, reload, seekTo, flightStartMs }) {
  const { user } = useAuth() || {};
  const isInstructor = user && (user.role === "instructor" || user.role === "admin");
  const [presets, setPresets] = useState(null);
  const [strictness, setStrictness] = useState("");
  const [useCurrent, setUseCurrent] = useState(false);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [comment, setComment] = useState("");

  useEffect(() => {
    sopApi.strictness().then(setPresets).catch(() => setPresets(null));
  }, []);
  useEffect(() => {
    setComment(evaluation?.instructor_comment || "");
  }, [evaluation?.id, evaluation?.instructor_comment]);

  const run = useCallback(async (label, fn) => {
    setBusy(label); setErr("");
    try { await fn(); await reload(); }
    catch (e) { setErr(e.detail || e.message || String(e)); }
    finally { setBusy(""); }
  }, [reload]);

  const recheck = () => run("Checking…", () => sopApi.evaluate(sessionId, {
    ...(strictness ? { strictness } : {}), use_current_rules: useCurrent || !evaluation,
  }));

  const toT = (epoch) => (flightStartMs ? epoch - flightStartMs / 1000 : 0);
  const [incident, setIncident] = useState(null);     // {resultId, index}: the ±30 s view
  const results = evaluation?.results || [];
  const grouped = useMemo(() => {
    const g = { fail: [], instructor: [], pass: [], other: [] };
    for (const r of results) g[groupOf(r.final_verdict)].push(r);
    // most serious first, then the rule broken most often
    g.fail.sort((a, b) => ((SEVERITY_ORDER[a.severity] ?? 3) - (SEVERITY_ORDER[b.severity] ?? 3))
      || ((b.violations || []).length - (a.violations || []).length));
    return g;
  }, [results]);
  const timesBroken = grouped.fail.reduce((n, r) => n + (r.violations || []).length, 0);
  // Only the broken rules by default; the rest behind a checkbox (remembered).
  const [showAll, setShowAll] = useState(() => {
    try { return localStorage.getItem(SHOW_ALL_KEY) === "1"; } catch { return false; }
  });
  const toggleShowAll = (on) => {
    setShowAll(on);
    try { localStorage.setItem(SHOW_ALL_KEY, on ? "1" : "0"); } catch { /* storage unavailable -- fine */ }
  };
  const nOthers = results.length - grouped.fail.length;
  const ruleRow = (r) => (
    <RuleRow key={r.id} r={r} isInstructor={isInstructor} toT={toT}
      seek={(epoch) => seekTo(Math.max(0, toT(epoch)))} onSaved={reload}
      onAnalyse={(index) => {          // the video jumps there too, a few seconds early
        const v = r.violations?.[index];
        if (v?.t0 != null) seekTo(Math.max(0, toT(v.t0) - 5));
        setIncident({ resultId: r.id, index });
      }} />
  );

  return (
    <>
    {incident && (
      <IncidentView resultId={incident.resultId} index={incident.index} onClose={() => setIncident(null)}
        onWatch={(epoch) => { setIncident(null); seekTo(Math.max(0, toT(epoch))); }} />
    )}
    <div className="rc-pane" role="region" aria-label="SOP rule checks">
      <div className="rc-head">
        <h2>SOP rule checks</h2>
      </div>

      {missing && !evaluation && (
        <div className="rc-empty">
          This flight hasn't been checked against its rules yet.
          {!isInstructor && <div className="rc-muted">Your instructor can run the check.</div>}
        </div>
      )}

      {evaluation && (
        <div className="rc-meta">
          <div>
            <b>{evaluation.strictness_label || evaluation.strictness}</b>
            <span className="rc-muted"> · {evaluation.strictness_why || "strictness"}</span>
          </div>
          <div className="rc-muted">
            {evaluation.source === "live" ? "Checked live during the flight" : "Checked from the recorded flight"}
            {" · "}{new Date(evaluation.created_at).toLocaleString()}
            {evaluation.aircraft_profile ? ` · ${evaluation.aircraft_profile}` : ""}
            {evaluation.n_evaluations > 1 ? ` · check ${evaluation.n_evaluations}` : ""}
          </div>
          {evaluation.signed_off_at ? (
            <div className="rc-signed">
              Reviewed by {evaluation.signed_off_by} · {new Date(evaluation.signed_off_at).toLocaleString()}
              {evaluation.instructor_comment && <div className="rc-comment">“{evaluation.instructor_comment}”</div>}
            </div>
          ) : (
            <div className="rc-provisional">
              {isInstructor ? "Not signed off yet." : "Provisional: your instructor hasn't reviewed these results yet."}
            </div>
          )}
        </div>
      )}

      {evaluation?.aircraft_note && <div className="rc-why">{evaluation.aircraft_note}</div>}
      {evaluation && results.length === 0 && (
        <div className="rc-why">
          <b>No rules were checked.</b> When this flight was checked, {evaluation.aircraft_profile || "the aircraft"} had
          no active rule set with rules for {evaluation.lesson_type || "normal"} lessons, and no training handbook
          was active. {isInstructor
            ? <>Activate a rule set on the <a href="/sop/rules">SOP Rules</a> page, then press Re-check.</>
            : "Your instructor can activate a rule set and re-check this flight."}
        </div>
      )}
      {evaluation && results.length > 0 && !(grouped.fail.length || grouped.pass.length) && (
        <div className="rc-why">
          <b>No rule could be marked met or not met from the flight data.</b>{" "}
          Of {results.length} rules, {grouped.instructor.length} can't be measured from flight data (the instructor
          judges them below) and {grouped.other.length} didn't apply or had no data: each says why, e.g. its phase
          was never flown in this lesson.{" "}
          {isInstructor && grouped.instructor.length > 0 && (
            <>Rules about heights, speeds, bank or descent rate can be made measurable on the{" "}
              <a href="/sop/rules">SOP Rules</a> page (as a band, limit, event or capture rule).</>
          )}
        </div>
      )}

      {err && <div className="rc-err">{err}</div>}

      {evaluation && results.length > 0 && (
        <>
          <div className="rc-tally">
            <div className={"rc-tally__item is-fail" + (grouped.fail.length ? "" : " is-zero")}>
              <b>{grouped.fail.length}</b><span>broken</span>
            </div>
            <div className="rc-tally__item is-pass"><b>{grouped.pass.length}</b><span>met</span></div>
            {grouped.instructor.length > 0 && (
              <div className="rc-tally__item is-assess"><b>{grouped.instructor.length}</b><span>to assess</span></div>
            )}
            <div className="rc-tally__item"><b>{grouped.other.length}</b><span>didn't apply</span></div>
          </div>

          <section className="rc-group">
            <h3>
              Rules broken
              {timesBroken > 0 && <span className="rc-muted"> · {timesBroken} time{timesBroken === 1 ? "" : "s"} in total</span>}
            </h3>
            {grouped.fail.length ? grouped.fail.map(ruleRow)
              : <div className="rc-allmet">✓ No rules broken{grouped.pass.length ? `: all ${grouped.pass.length} measured rules were met` : ""}. Nice flying.</div>}
          </section>

          {nOthers > 0 && (
            <label className="rc-showall">
              <input type="checkbox" checked={showAll} onChange={(e) => toggleShowAll(e.target.checked)} />
              Show the other {nOthers} rule{nOthers === 1 ? "" : "s"}
              <span className="rc-muted">
                {" "}({[grouped.pass.length && `${grouped.pass.length} met`,
                  grouped.instructor.length && `${grouped.instructor.length} to assess`,
                  grouped.other.length && `${grouped.other.length} didn't apply`].filter(Boolean).join(", ")})
              </span>
            </label>
          )}
          {showAll && GROUPS.filter((g) => g.key !== "fail").map((g) => grouped[g.key].length > 0 && (
            <section key={g.key} className="rc-group">
              <h3>{g.title} <span className="rc-muted">({grouped[g.key].length})</span></h3>
              {grouped[g.key].map(ruleRow)}
            </section>
          ))}
        </>
      )}

      {isInstructor && (
        <div className="rc-tools rc-tools--recheck">
          <label>
            Strictness{" "}
            <select value={strictness} onChange={(e) => setStrictness(e.target.value)}>
              <option value="">{evaluation ? "Keep current" : "Trainee's / mode default"}</option>
              {Object.entries(presets?.presets || { lenient: {}, standard: {}, strict: {} }).map(([k, p]) => (
                <option key={k} value={k}>{p.label || k}</option>
              ))}
            </select>
          </label>
          {evaluation && (
            <label className="rc-check">
              <input type="checkbox" checked={useCurrent} onChange={(e) => setUseCurrent(e.target.checked)} />
              Use the rules as they are now
            </label>
          )}
          <button className="rc-btn" disabled={!!busy} onClick={recheck}>
            {busy === "Checking…" ? busy : evaluation ? "Re-check" : "Check now"}
          </button>
          {presets && strictness && (
            <div className="rc-preset rc-muted">
              {Object.entries(presets.help).map(([k, label]) => (
                <span key={k}>{label}: <b>{fmtPreset(k, presets.presets[strictness]?.[k])}</b></span>
              ))}
            </div>
          )}
        </div>
      )}
      {evaluation && isInstructor && (
        <div className="rc-signoff">
          <h3>Sign off</h3>
          <textarea rows={3} placeholder="Comment for the trainee (optional)" value={comment}
            onChange={(e) => setComment(e.target.value)} />
          <div className="rc-row">
            <button className="rc-btn primary" disabled={!!busy}
              onClick={() => run("Signing…", () => api.signOffRules(sessionId, { signed_off: true, comment }))}>
              {evaluation.signed_off_at ? "Update sign-off" : "Sign off results"}
            </button>
            {evaluation.signed_off_at && (
              <button className="rc-btn" disabled={!!busy}
                onClick={() => run("Reopening…", () => api.signOffRules(sessionId, { signed_off: false }))}>
                Reopen
              </button>
            )}
          </div>
        </div>
      )}
    </div>
    </>
  );
}

function fmtPreset(k, v) {
  if (v == null) return "–";
  if (k === "max_out_frac") return `${Math.round(v * 100)}%`;
  if (k === "tol_mult") return `×${v}`;
  return `${v} s`;
}

function mmss(s) {
  if (s == null || !isFinite(s)) return "–";
  const t = Math.max(0, Math.round(s));
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
}
const MAX_SHOWN = 12;

function RuleRow({ r, isInstructor, toT, seek, onSaved, onAnalyse }) {
  const [open, setOpen] = useState(false);
  const [showEvery, setShowEvery] = useState(false);
  const v = VERDICT[r.final_verdict] || VERDICT.not_evaluated;
  const viol = r.violations || [];

  const detail = open && (
    <div className="rc-detail">
      {r.source_quote && (
        <blockquote>“{r.source_quote}”{r.source_page ? <span className="rc-muted"> p. {r.source_page}</span> : null}</blockquote>
      )}
      {r.layer && <div className="rc-muted">{r.layer === "general" ? "Training handbook rule" : "Aircraft SOP rule"}</div>}
    </div>
  );
  const note = r.instructor_note && !isInstructor && <div className="rc-note">Instructor: {r.instructor_note}</div>;
  const judge = isInstructor && <Judge r={r} onSaved={onSaved} />;

  if (r.final_verdict !== "fail") {
    return (
      <div className={"rc-rule sev-" + (r.severity || "caution")}>
        <div className="rc-rule-head" onClick={() => setOpen((o) => !o)}>
          <span className={"rc-pill " + v.cls}>{v.label}</span>
          <span className="rc-title">{r.title}</span>
          {r.severity && <span className={"rc-sev " + r.severity}>{r.severity}</span>}
          {r.instructor_verdict && <span className="rc-overruled" title={`Computed: ${VERDICT[r.verdict]?.label}`}>instructor</span>}
        </div>
        <div className="rc-sum">
          {r.requirement && <span className="rc-req">{r.requirement}</span>} {r.summary}
        </div>
        {note}{detail}{judge}
      </div>
    );
  }

  // Broken: the count up front, then each time it happened.
  const outFor = viol.reduce((t, x) => t + (x.dur_s || 0), 0);
  const shown = showEvery ? viol : viol.slice(0, MAX_SHOWN);
  return (
    <div className={"rc-brk sev-" + (r.severity || "caution")}>
      <div className="rc-brk__top" onClick={() => setOpen((o) => !o)} title="Show where this rule comes from">
        <div className="rc-brk__count">
          {viol.length ? <><b>{viol.length}×</b><span>{viol.length === 1 ? "once" : "times"}</span></>
            : <><b>✗</b><span>judged</span></>}
        </div>
        <div className="rc-brk__what">
          <div className="rc-brk__title">
            <span className="rc-title">{r.title}</span>
            {r.severity && <span className={"rc-sev " + r.severity}>{r.severity}</span>}
          </div>
          {r.requirement && <div className="rc-brk__req">Limit: <b>{r.requirement}</b></div>}
          <div className="rc-brk__sum">
            {r.summary}
            {outFor > 0 && <span className="rc-muted"> · outside the limit for {outFor.toFixed(0)} s in total</span>}
          </div>
          {r.instructor_verdict && (
            <div className="rc-muted rc-brk__by">Marked not met by the instructor{r.verdict !== "fail" ? ` (computed: ${VERDICT[r.verdict]?.label})` : ""}</div>
          )}
        </div>
      </div>
      {viol.length > 0 && (
        <ol className="rc-times">
          {shown.map((x, i) => (
            <li key={i} className="rc-time">
              <span className="rc-time__n">{i + 1}</span>
              <span className="rc-time__at" title="Time into the flight">{x.t0 != null ? mmss(toT(x.t0)) : "–"}</span>
              <span className="rc-time__what">
                {x.phase && <span className="rc-time__phase">{x.phase.toLowerCase().replace(/_/g, " ")}</span>}
                <b>{x.peak_text}</b>
                {x.dur_s != null && <span className="rc-muted"> · {x.dur_s.toFixed(0)} s</span>}
              </span>
              <button className="rc-vbtn" onClick={() => seek(x.t0)} title="Jump the video and timeline to this moment">▶ Watch</button>
              <button className="rc-vbtn rc-vbtn--why" onClick={() => onAnalyse(i)}
                title="The 30 s before and after: what led to it and how it was corrected">Analyse</button>
            </li>
          ))}
          {viol.length > MAX_SHOWN && (
            <li className="rc-time rc-time--more">
              <button className="rc-linkbtn" onClick={() => setShowEvery((x) => !x)}>
                {showEvery ? "Show fewer" : `Show all ${viol.length}`}
              </button>
            </li>
          )}
        </ol>
      )}
      {note}{detail}{judge}
    </div>
  );
}

// Instructor: agree with the computed verdict or overrule it, with a note.
function Judge({ r, onSaved }) {
  const [verdict, setVerdict] = useState(r.instructor_verdict || "");
  const [note, setNote] = useState(r.instructor_note || "");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => { setVerdict(r.instructor_verdict || ""); setNote(r.instructor_note || ""); },
    [r.id, r.instructor_verdict, r.instructor_note]);
  const dirty = verdict !== (r.instructor_verdict || "") || note !== (r.instructor_note || "");

  const save = async () => {
    setSaving(true); setErr("");
    try {
      await api.setRuleVerdict(r.id, { instructor_verdict: verdict || null, instructor_note: note || null });
      await onSaved();
    } catch (e) { setErr(e.detail || e.message); }
    finally { setSaving(false); }
  };

  return (
    <div className="rc-judge">
      <select value={verdict} onChange={(e) => setVerdict(e.target.value)}>
        <option value="">{r.verdict === "instructor" ? "Not assessed" : `Agree (${VERDICT[r.verdict]?.label})`}</option>
        <option value="pass">Met</option>
        <option value="fail">Not met</option>
        <option value="not_applicable">Didn't apply</option>
      </select>
      <input placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
      <button className="rc-btn" disabled={!dirty || saving} onClick={save}>{saving ? "Saving…" : "Save"}</button>
      {r.reviewed_by && <span className="rc-muted">by {r.reviewed_by}</span>}
      {err && <span className="rc-err">{err}</span>}
    </div>
  );
}
