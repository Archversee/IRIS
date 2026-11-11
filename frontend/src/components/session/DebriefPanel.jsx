// Review → Debrief: the whole flight in a few lines, written by the local
// reasoning model from measured facts only, plus those facts at a glance.
// Written automatically when the flight ends or Review is first opened (with a
// note for each broken rule).
// Backend: app/routers/debrief.py, prompt in app/flight_summary.py.
import { useEffect, useState } from "react";
import { api } from "../../api/client.js";
import { useAuth } from "../../auth/AuthContext.jsx";

export const RUNNING = (job) => !!job && job.state !== "done" && job.state !== "error";

// Opening Review starts the background debrief job if anything is missing (the
// backend decides; nothing to click), then follows its progress.
export function useDebriefJob(sessionId) {
  const [job, setJob] = useState(null);
  useEffect(() => {
    let alive = true, timer;
    const poll = () => api.debriefStatus(sessionId)
      .then((x) => {
        if (!alive) return;
        setJob(x.job);
        if (RUNNING(x.job)) timer = setTimeout(poll, 4000);
      })
      .catch(() => {});
    api.runDebrief(sessionId, { auto: true }).catch(() => {}).finally(poll);
    return () => { alive = false; clearTimeout(timer); };
  }, [sessionId]);
  return job;
}

export function DebriefChip({ onClick, job }) {
  const running = RUNNING(job);
  return (
    <button className="rc-chip" onClick={onClick}
      title={running ? `AI debrief: ${job.state}` : "The whole flight: AI debrief and the facts behind it"}>
      Debrief
      {running && <span className="db-spin db-spin--chip" aria-hidden="true" />}
      {running && job.total ? ` ${job.done}/${job.total}` : ""}
    </button>
  );
}

export default function DebriefPanel({ sessionId }) {
  const { user } = useAuth() || {};
  const isInstructor = user && (user.role === "instructor" || user.role === "admin");
  const [d, setD] = useState(null);
  const [err, setErr] = useState("");
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true, timer;
    const go = () => api.debrief(sessionId)
      .then((x) => {
        if (!alive) return;
        setD(x);
        if (RUNNING(x.job)) timer = setTimeout(go, 2500);
      })
      .catch((e) => alive && setErr(e.detail || e.message));
    go();
    return () => { alive = false; clearTimeout(timer); };
  }, [sessionId, tick]);

  async function run(body) {
    setErr("");
    try { await api.runDebrief(sessionId, body); setTick((t) => t + 1); }
    catch (e) { setErr(e.detail || e.message); }
  }

  const s = d?.summary, job = d?.job, running = RUNNING(job);
  return (
    <div className="rc-pane" role="region" aria-label="Flight debrief">
      <div className="rc-head">
        <h2>Debrief</h2>
      </div>
      {err && <div className="rc-err">{err}</div>}
      {!d && !err && <div className="rc-empty">Loading…</div>}
      {d && (
        <>
          {running && (
            <div className="db-job">
              <span className="db-spin" aria-hidden="true" />
              {job.state[0].toUpperCase() + job.state.slice(1)}
              {job.total ? ` · ${job.done} of ${job.total} done` : ""}{job.failed ? ` · ${job.failed} failed` : ""}
              <div className="rc-muted">Written on the debrief PC. Each one can take a minute; you can keep reviewing.</div>
            </div>
          )}
          {job?.state === "error" && (
            <div className="rc-why">
              {job.total && job.done ? `Wrote ${job.done} of ${job.total}. ` : "Couldn't write the debrief: "}{job.error}
              <div className="rc-muted">The model is set in MAVIS's config.yaml under <code>debrief:</code>. It's tried again
                the next time Review is opened.</div>
            </div>
          )}

          {s ? (
            <section className="db-summary">
              {s.sections.map((sec) => (
                <div key={sec.title} className="db-sec">
                  <h3>{sec.title}</h3>
                  <Body text={sec.body} />
                </div>
              ))}
              {s.unsupported_numbers?.length > 0 && (
                <div className="inc-explain__warn">Check these numbers, they aren't in the measured facts: {s.unsupported_numbers.join(", ")}</div>
              )}
              <div className="rc-muted db-meta">
                {s.requested_by === "auto" ? "Written automatically after the flight" : `Written for ${s.requested_by}`}
                {" · "}{new Date(s.created_at).toLocaleString()}
              </div>
            </section>
          ) : !running && (
            <div className="rc-empty">
              No debrief written for this flight yet.
              {!isInstructor && <div className="rc-muted">It's written automatically after the flight; your instructor can also write it.</div>}
            </div>
          )}

          {/* only when something is missing: no "write it again" once there's a summary */}
          {isInstructor && !running && (!s || d.notes_missing > 0) && (
            <div className="rc-tools">
              {!s && (
                <button className="rc-btn primary" onClick={() => run({ redo_summary: true })}>Write the debrief</button>
              )}
              {d.notes_missing > 0 && (
                <button className="rc-btn" onClick={() => run({ all_notes: true })}>
                  Write the {d.notes_missing} missing broken-rule note{d.notes_missing === 1 ? "" : "s"}
                </button>
              )}
            </div>
          )}

          <Facts f={d.facts} />
        </>
      )}
    </div>
  );
}

function Body({ text }) {
  const lines = text.split("\n").filter(Boolean);
  const points = lines.filter((l) => l.trim().startsWith("- "));
  if (points.length === lines.length) return <ul className="db-points">{points.map((l, i) => <li key={i}>{l.trim().slice(2)}</li>)}</ul>;
  return lines.map((l, i) => <p key={i} className="db-p">{l.trim().replace(/^- /, "• ")}</p>);
}

// The measured facts the summary was written from, at a glance.
function Facts({ f }) {
  if (!f) return null;
  const sop = typeof f.sop_check === "object" ? f.sop_check : null;
  const fin = f.final_approach, td = f.touchdown;
  const calls = f.mavis_callouts || [];
  const fixed = calls.reduce((n, c) => n + (c.fixed_it || 0), 0), judged = calls.reduce((n, c) => n + (c.fixed_it != null ? c.times : 0), 0);
  const tiles = [
    ["Rules met", sop ? `${sop.rules_met}` : "–"],
    ["Rules broken", sop ? `${sop.rules_broken}` : "not checked"],
    ["Broken-rule moments", `${f.broken_rule_moments.length + (f.broken_rule_moments_not_listed || 0)}`],
    ["Main cause", f.most_common_causes?.[0]?.cause || "–"],
    ["Final approach speed", fin?.average_speed_kt != null ? `${fin.average_speed_kt.toFixed(0)} kt ±${(fin.speed_varied_by_kt ?? 0).toFixed(1)}` : "–"],
    ["Touchdown sink rate", td?.sink_rate_fpm != null ? `${td.sink_rate_fpm} ft/min` : "–"],
    ["Callouts that fixed it", judged ? `${fixed} of ${judged}` : "–"],
    ["Eye tracker coverage", f.eye_tracker_coverage_pct != null ? `${f.eye_tracker_coverage_pct}%` : "–"],
  ];
  return (
    <section className="rc-group">
      <h3>The flight at a glance</h3>
      <div className="db-tiles">
        {tiles.map(([k, v]) => <div key={k} className="db-tile"><span>{k}</span><b>{v}</b></div>)}
      </div>
      {f.previous_flight && (
        <div className="rc-muted db-prev">
          Flight before ({f.previous_flight.date}): {f.previous_flight.rules_broken.length
            ? `broke ${f.previous_flight.rules_broken.join(", ")} (${f.previous_flight.times_broken}×)` : "no rules broken"}.
        </div>
      )}
    </section>
  );
}
