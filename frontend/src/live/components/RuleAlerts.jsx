import "./ruleAlerts.css";

// SOP / handbook rules checked live (MAVIS sop/evaluate.py via main.py,
// state.sop_live). Shown in MAVIS's in-flight view and IRIS's Live Monitor:
// what's being broken right now, else the last thing that was, and at the
// end of the run how the flight did. When nothing is being checked it says
// why (no active rule set, aircraft not recognised, database down, ...).
// Warning rules are also spoken by MAVIS.
export default function RuleAlerts({ live, flying = true }) {
  if (!live) {
    return flying ? <div className="mavis-rules"><span className="mavis-rules__label">Rules · loading…</span></div> : null;
  }
  if (live.status === "off") {
    return (
      <div className="mavis-rules">
        <span className="mavis-rules__item mavis-rules__item--off" title={live.reason}>
          Rules not checked · {live.reason}
        </span>
      </div>
    );
  }
  const b = live.summary?.by_verdict;
  const last = !live.active?.length && live.recent?.[0];
  return (
    <div className="mavis-rules" aria-live="polite">
      <span className="mavis-rules__label" title={`Strictness: ${live.strictness} (${live.strictness_why})`}>
        Rules · {live.n_rules} checked{live.n_findings ? ` · ${live.n_findings} flagged` : " · all OK so far"}
      </span>
      {b && (
        <span className="mavis-rules__item mavis-rules__item--sum">
          {b.pass || 0} met · {b.fail || 0} not met{b.instructor ? ` · ${b.instructor} for the instructor` : ""}
        </span>
      )}
      {(live.active || []).map((f) => (
        <span key={f.rule_id} className={`mavis-rules__item mavis-rules__item--${f.severity}`}>
          {f.severity === "warning" ? "⚠ " : ""}{f.title}: {f.value_text} · should be {f.requirement}
        </span>
      ))}
      {last && !b && (
        <span className="mavis-rules__item mavis-rules__item--past">
          Last: {last.title} ({last.value_text}{last.phase ? `, ${last.phase.toLowerCase().replace(/_/g, " ")}` : ""})
        </span>
      )}
    </div>
  );
}
