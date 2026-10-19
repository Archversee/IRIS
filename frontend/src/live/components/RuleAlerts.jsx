import { useEffect, useRef, useState } from "react";
import "./ruleAlerts.css";

// SOP / handbook rules checked live (MAVIS sop/evaluate.py via main.py,
// state.sop_live). Shown in MAVIS's in-flight view and IRIS's Live Monitor:
// what's being broken right now, else the last thing that was, and at the
// end of the run how the flight did. When nothing is being checked it says
// why (no active rule set, aircraft not recognised, database down, ...).
// Warning rules are also spoken by MAVIS.
//
// Always ONE line: when several rules are broken at once, only the most
// severe is shown and the rest sit behind a "+N more" button, so a bad
// moment never floods the screen.
const SEVERITY_ORDER = { warning: 0, caution: 1, advisory: 2 };
const bySeverity = (a, b) => (SEVERITY_ORDER[a.severity] ?? 3) - (SEVERITY_ORDER[b.severity] ?? 3);

function Finding({ f }) {
  const text = `${f.title}: ${f.value_text} · should be ${f.requirement}`;
  return (
    <span className={`mavis-rules__item mavis-rules__item--${f.severity}`} title={text}>
      {f.severity === "warning" ? "⚠ " : ""}{text}
    </span>
  );
}

export default function RuleAlerts({ live, flying = true }) {
  const [open, setOpen] = useState(false);
  const boxRef = useRef(null);
  const active = [...(live?.active || [])].sort(bySeverity);
  const more = active.length - 1;

  useEffect(() => { if (more <= 0) setOpen(false); }, [more]);
  useEffect(() => {
    if (!open) return undefined;
    const close = (e) => { if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false); };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [open]);

  if (!live) {
    return flying ? <div className="mavis-rules"><span className="mavis-rules__label">Rules · loading…</span></div> : null;
  }
  if (live.status === "disabled") return null;   // instructor switched checks off for circuit flights
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
  const last = !active.length && live.recent?.[0];
  return (
    <div className="mavis-rules" aria-live="polite" ref={boxRef}>
      <span className="mavis-rules__label" title={`Strictness: ${live.strictness} (${live.strictness_why})`}>
        Rules · {live.n_rules} checked{live.n_findings ? ` · ${live.n_findings} flagged` : " · all OK so far"}
      </span>
      {live.aircraft_note && (
        <span className="mavis-rules__item mavis-rules__item--off" title={live.aircraft_note}>
          {live.aircraft_note.split(":")[0]}
        </span>
      )}
      {b && (
        <span className="mavis-rules__item mavis-rules__item--sum">
          {b.pass || 0} met · {b.fail || 0} not met{b.instructor ? ` · ${b.instructor} for the instructor` : ""}
        </span>
      )}
      {active.length > 0 && <Finding f={active[0]} />}
      {more > 0 && (
        <span className="mavis-rules__more">
          <button type="button" className="mavis-rules__item mavis-rules__more-btn" aria-expanded={open}
            onClick={() => setOpen((o) => !o)}>
            +{more} more
          </button>
          {open && (
            <div className="mavis-rules__pop" role="list">
              {active.slice(1).map((f) => (
                <div key={f.rule_id} role="listitem"><Finding f={f} /></div>
              ))}
            </div>
          )}
        </span>
      )}
      {last && !b && (
        <span className="mavis-rules__item mavis-rules__item--past"
          title={`${last.title} (${last.value_text})`}>
          Last: {last.title} ({last.value_text}{last.phase ? `, ${last.phase.toLowerCase().replace(/_/g, " ")}` : ""})
        </span>
      )}
    </div>
  );
}
