import { useCallback, useEffect, useMemo, useState } from "react";
import { sopApi } from "./sopApi.js";
import { describe } from "./ruleText.js";

// Which SOP / handbook rules each curriculum lesson checks (MAVIS
// sop/lesson_plans.py). MAVIS recommends the rules whose phases are flown in
// the lesson (e.g. Landing: final, flare and touchdown, roll-out) plus the
// whole-flight ones; an instructor adds or removes rules and can switch a
// lesson's checks off. Only the rules on a lesson's list are checked in it:
// not live, and not in Review. Free Flight always checks every active rule.
//
//  <LessonRulesView meta />       SOP Rules page: every lesson, pick one, edit its list
//  <LessonRuleList lessonId ... /> one lesson's list (also on the lesson start screen)

const SEV_TITLE = { warning: "Warning", caution: "Caution", advisory: "Advisory" };

export function LessonRulesView({ meta }) {
  const [aircraft, setAircraft] = useState("");
  const [lessons, setLessons] = useState(null);
  const [picked, setPicked] = useState(null);
  const [err, setErr] = useState("");

  const load = useCallback(() => {
    sopApi.lessonPlans(aircraft || undefined).then((d) => {
      setLessons(d.lessons);
      if (!aircraft && d.aircraft_id) setAircraft(d.aircraft_id);
      setPicked((p) => p || d.lessons[0]?.id || null);
    }).catch((e) => setErr(e.message));
  }, [aircraft]);
  useEffect(load, [load]);

  const modules = useMemo(() => {
    const m = [];
    for (const l of lessons || []) {
      const g = m.find((x) => x.title === l.module) || (m.push({ title: l.module, lessons: [] }), m[m.length - 1]);
      g.lessons.push(l);
    }
    return m;
  }, [lessons]);

  return (
    <div className="sop-lr">
      <div className="sop-lr__intro">
        <p>
          Each lesson checks only the rules on its own list. MAVIS recommends the rules for the phases that lesson
          flies, and you can add or remove any of them. Free Flight always checks every active rule.
        </p>
        {meta?.aircraft?.length > 1 && (
          <label className="sop-lr__ac">
            Aircraft rules for{" "}
            <select value={aircraft} onChange={(e) => { setAircraft(e.target.value); }}>
              {meta.aircraft.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          </label>
        )}
      </div>
      {err && <div className="sop-error">{err}</div>}
      {!lessons && !err && <div className="muted">Loading lessons…</div>}
      {lessons && (
        <div className="sop-lr__split">
          <nav className="sop-lr__lessons" aria-label="Lessons">
            {modules.map((m) => (
              <div key={m.title} className="sop-lr__module">
                <div className="sop-lr__module-title">{m.title}</div>
                {m.lessons.map((l) => (
                  <button type="button" key={l.id} className={"sop-lr__lesson" + (l.id === picked ? " is-on" : "")}
                    onClick={() => setPicked(l.id)} aria-current={l.id === picked ? "true" : undefined}>
                    <span className="sop-lr__lesson-title">{l.title}</span>
                    <span className="sop-lr__lesson-sub">
                      {l.checks_on
                        ? <>{l.n_included} rule{l.n_included === 1 ? "" : "s"}{l.n_changed ? " · edited" : ""}</>
                        : <span className="sop-lr__off">Checks off</span>}
                    </span>
                  </button>
                ))}
              </div>
            ))}
          </nav>
          <div className="sop-lr__detail">
            {picked && <LessonRuleList key={picked + aircraft} lessonId={picked} aircraft={aircraft || undefined}
              meta={meta} editable onChanged={load} />}
          </div>
        </div>
      )}
    </div>
  );
}

/** One lesson's rule list. editable: the instructor can switch rules and the lesson on/off. */
export function LessonRuleList({ lessonId, aircraft, meta, editable = false, compact = false, onChanged }) {
  const [plan, setPlan] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(null);           // rule id / "on" / "reset" being saved
  const [showOthers, setShowOthers] = useState(false);
  const [q, setQ] = useState("");

  useEffect(() => {
    setPlan(null); setErr("");
    sopApi.lessonPlan(lessonId, aircraft).then(setPlan).catch((e) => setErr(e.message));
  }, [lessonId, aircraft]);

  async function edit(key, body) {
    setBusy(key); setErr("");
    try {
      setPlan(await sopApi.editLessonPlan(lessonId, { ...body, ...(aircraft ? { aircraft } : {}) }));
      onChanged?.();
    } catch (e) { setErr(e.message); }
    finally { setBusy(null); }
  }

  if (err && !plan) return <div className="sop-error">{err}</div>;
  if (!plan) return <div className="muted">Loading rules…</div>;

  const match = (r) => !q.trim() || `${r.title} ${r.why} ${meta ? describe(meta, r) : ""}`.toLowerCase().includes(q.trim().toLowerCase());
  const on = plan.rules.filter((r) => r.included && match(r));
  const others = plan.rules.filter((r) => !r.included && match(r));
  const nOthers = plan.rules.filter((r) => !r.included).length;
  const row = (r) => (
    <li key={r.id} className={"sop-lr__rule" + (r.included ? "" : " is-off")}>
      <span className={"sop-dot sop-dot--" + (r.severity || "caution")} title={SEV_TITLE[r.severity] || ""} />
      <div className="sop-lr__rule-text">
        <div className="sop-lr__rule-title">
          <b>{r.title || "Untitled rule"}</b>
          <span className={"sop-lr__src sop-lr__src--" + (r.layer === "general" ? "book" : "ac")}>
            {r.layer === "general" ? "Training handbook" : "Aircraft SOP"}
          </span>
          {r.changed && editable && <span className="sop-lr__changed">{r.included ? "Added by instructor" : "Removed by instructor"}</span>}
        </div>
        {meta && <div className="sop-lr__rule-sum">{describe(meta, r)}</div>}
        {/* why it was (not) recommended: for whoever edits the list */}
        {editable && (
          <div className={"sop-lr__why" + (r.recommended ? " is-rec" : "")}>
            {r.recommended ? "Recommended · " : "Not recommended · "}{r.why}
          </div>
        )}
      </div>
      {editable && (
        r.included ? (
          <button type="button" className="sop-lr__btn sop-lr__btn--remove" disabled={!!busy || !plan.checks_on}
            onClick={() => edit(r.id, { exclude: [r.id] })} title="Don't check this rule in this lesson">
            {busy === r.id ? "…" : "Remove"}
          </button>
        ) : (
          <button type="button" className="sop-lr__btn sop-lr__btn--add" disabled={!!busy || !plan.checks_on}
            onClick={() => edit(r.id, { include: [r.id] })} title="Check this rule in this lesson too">
            {busy === r.id ? "…" : "+ Add"}
          </button>
        )
      )}
    </li>
  );

  return (
    <section className={"sop-lr__plan" + (compact ? " is-compact" : "")} aria-label={`Rules checked in ${plan.lesson_name}`}>
      <header className="sop-lr__head">
        <div>
          <h2>{plan.lesson_name}</h2>
          <div className="muted sop-lr__phases">
            {plan.phase_words ? <>Flies: {plan.phase_words}</> : "Not flown as a circuit: only whole-flight rules are recommended"}
            {plan.lesson_type !== "normal" && <> · {plan.lesson_type} lesson</>}
          </div>
        </div>
        <span className="spacer" />
        {editable ? (
          <label className="sop-lr__switch">
            <span>Check rules in this lesson</span>
            <button type="button" role="switch" aria-checked={plan.checks_on} disabled={!!busy}
              className={"sop-switch" + (plan.checks_on ? " is-on" : "")}
              onClick={() => edit("on", { checks_on: !plan.checks_on })}>
              <span className="sop-switch__knob" />
            </button>
          </label>
        ) : !plan.checks_on && <span className="sop-lr__off">Rule checks are off for this lesson</span>}
      </header>

      {plan.checks_on ? (
        <>
          <div className="sop-lr__bar">
            <b>{plan.n_included} rule{plan.n_included === 1 ? "" : "s"} will be checked</b>
            {editable && (
              <span className="muted">
                {" "}· {plan.n_recommended} recommended{plan.n_changed ? ` · ${plan.n_changed} changed by an instructor` : ""}
              </span>
            )}
            <span className="spacer" />
            {plan.rules.length > 6 && (
              <input type="search" className="sop-search sop-lr__search" placeholder="Search rules…" value={q}
                onChange={(e) => setQ(e.target.value)} />
            )}
            {editable && plan.n_changed > 0 && (
              <button type="button" className="sop-lr__btn" disabled={!!busy}
                onClick={() => edit("reset", { reset: true })} title="Back to MAVIS's recommendation for this lesson">
                {busy === "reset" ? "Resetting…" : "Reset to recommended"}
              </button>
            )}
          </div>
          {err && <div className="sop-error">{err}</div>}
          {plan.rules.length === 0 ? (
            <div className="sop-lr__empty">No active rules for this aircraft and lesson type. Activate a rule set first.</div>
          ) : (
            <>
              <ul className="sop-lr__rules">
                {on.length ? on.map(row) : <li className="sop-lr__empty">{q ? "No match." : "No rules on this lesson's list."}</li>}
              </ul>
              {nOthers > 0 && (editable || !compact) && (
                <div className="sop-lr__others">
                  <button type="button" className="sop-lr__toggle" aria-expanded={showOthers}
                    onClick={() => setShowOthers((x) => !x)}>
                    {showOthers ? "▾" : "▸"} Other rules, not checked in this lesson ({nOthers})
                  </button>
                  {showOthers && <ul className="sop-lr__rules">{others.map(row)}</ul>}
                </div>
              )}
            </>
          )}
        </>
      ) : (
        <div className="sop-lr__empty">
          Nothing is checked in this lesson: no rule callouts while flying, and no SOP results in Review.
          {editable && " Switch it back on to check its rules."}
        </div>
      )}
      {plan.updated_by && !compact && (
        <div className="muted sop-lr__by">Last changed by {plan.updated_by}{plan.updated_at ? ` · ${new Date(plan.updated_at).toLocaleString()}` : ""}</div>
      )}
    </section>
  );
}

/** Lesson start screen: how many rules this lesson checks; opens to the list
 *  (instructors can change it there, trainees just see it). */
export function LessonRulesCard({ lessonId, editable = false }) {
  const [plan, setPlan] = useState(null);
  const [open, setOpen] = useState(false);
  const [meta, setMeta] = useState(null);       // for each rule's plain-words description, once opened
  useEffect(() => { if (open && !meta) sopApi.meta().then(setMeta).catch(() => {}); }, [open, meta]);
  const [failed, setFailed] = useState(false);
  const load = useCallback(() => {
    if (!lessonId) return;
    sopApi.lessonPlan(lessonId).then((p) => { setPlan(p); setFailed(false); }).catch(() => setFailed(true));
  }, [lessonId]);
  useEffect(() => { setPlan(null); setOpen(false); load(); }, [load]);
  if (!lessonId || failed) return null;          // MAVIS / rules DB not reachable: the menu works without it
  const n = plan?.n_included ?? null;
  return (
    <div className={"lr-card" + (open ? " is-open" : "")}>
      <button type="button" className="lr-card__bar" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <span className="lr-card__icon" aria-hidden="true">✓</span>
        <span className="lr-card__text">
          {!plan ? "Checking which SOP rules this lesson uses…"
            : !plan.checks_on ? "SOP rule checks are off for this lesson"
              : <><b>{n} SOP rule{n === 1 ? "" : "s"}</b> will be checked in this lesson</>}
        </span>
        <span className="lr-card__more">{open ? "Hide" : editable ? "Show / change" : "Show"}</span>
      </button>
      {open && (
        <div className="lr-card__body">
          <LessonRuleList lessonId={lessonId} meta={meta} editable={editable} compact onChanged={load} />
        </div>
      )}
    </div>
  );
}
