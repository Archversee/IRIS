import AvatarBox from "./AvatarBox.jsx";
import ChatPanel from "./ChatPanel.jsx";
import LookCard from "./LookCard.jsx";
import TodoCard from "./TodoCard.jsx";
import MapCanvas from "./MapCanvas.jsx";
import ScorecardOverlay from "./ScorecardOverlay.jsx";

// Two columns: avatar + chat on the left, and the ground track (planned
// vs. flown path) filling the whole right side top to bottom.
export default function InFlightPanel({ state, toolbar, onMainMenu, onDiscard }) {
  return (
    <div className="mavis-hud">
      <div className="mavis-hud__left">
        {toolbar}
        <AvatarBox speaking={state?.speaking} />
        <ChatPanel transcript={state?.transcript} chatEnabled={state?.session?.policy?.chat}
          joyPtt={!!state?.ctrl?.ptt} speakerName={state?.session?.name || ""} />
      </div>

      <div className="mavis-hud__right">
        <div className="mavis-hud__top">
          <span className="pill">{state?.phase || "—"}</span>
          <span className="muted">Circuit {state?.circuit ?? "—"} / {state?.total_circuits ?? "—"}</span>
          <SopChip sop={state?.sop} />
        </div>
        <RuleAlerts live={state?.sop_live} />

        <MapCanvas state={state} />
        {state?.lesson?.type === "familiarization" && <TodoCard todo={state?.fam_todo} />}
        {state?.lesson?.type === "familiarization" && <LookCard lookAt={state?.look_at} />}
      </div>

      {state?.scorecard && <ScorecardOverlay scorecard={state.scorecard} onMainMenu={onMainMenu} onDiscard={onDiscard} />}
    </div>
  );
}

// SOP / handbook rules checked live (MAVIS sop/evaluate.py via main.py):
// what's being broken right now, else the last thing that was, and at the end
// of the run how the flight did. Warning rules are also spoken.
function RuleAlerts({ live }) {
  if (!live) return null;
  const b = live.summary?.by_verdict;
  const last = !live.active?.length && live.recent?.[0];
  return (
    <div className="mavis-rules" aria-live="polite">
      <span className="mavis-rules__label" title={`Strictness: ${live.strictness} (${live.strictness_why})`}>
        Rules · {live.n_rules} checked{live.n_findings ? ` · ${live.n_findings} flagged` : ""}
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

// Which SOP rule set this flight uses -- chosen at lesson start from the
// aircraft loaded in the sim (MAVIS sop/runtime.py).
function SopChip({ sop }) {
  if (!sop) return null;
  const hb = sop.n_handbook_rules ? ` + ${sop.n_handbook_rules} handbook rules` : "";
  const text = sop.status === "active" ? `SOP · ${sop.name}${hb}` :
    sop.status === "none" ? `No SOP rules for ${sop.aircraft_name}${hb ? ` · ${sop.n_handbook_rules} handbook rules` : ""}` :
    sop.status === "no_profile" ? `No SOP · unknown aircraft${hb ? ` · ${sop.n_handbook_rules} handbook rules` : ""}` : "SOP rules unavailable";
  const books = sop.handbooks?.length ? `\nTraining handbooks (every aircraft): ${sop.handbooks.join(", ")}` : "";
  const title = sop.status === "active"
    ? `${sop.n_rules - (sop.n_handbook_rules || 0)} rules for ${sop.aircraft_name}` + (sop.detected ? ` (simulator: ${sop.detected})` : " (default aircraft)") + books
    : (sop.detected ? `Simulator reported: ${sop.detected} — ${sop.why}` : sop.why || "");
  return <span className={"pill" + (sop.status === "active" ? "" : " mavis-sop-chip--off")} title={title}>{text}</span>;
}
