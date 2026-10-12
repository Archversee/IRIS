import AvatarBox from "./AvatarBox.jsx";
import ChatPanel from "./ChatPanel.jsx";
import LookCard from "./LookCard.jsx";
import TodoCard from "./TodoCard.jsx";
import MapCanvas from "./MapCanvas.jsx";
import ScorecardOverlay from "./ScorecardOverlay.jsx";
import DemoBanner from "./DemoBanner.jsx";

// Two columns: avatar + chat on the left, and the ground track (planned
// vs. flown path) filling the whole right side top to bottom.
export default function InFlightPanel({ state, toolbar, onMainMenu, onDiscard, onFlyAgain }) {
  return (
    <div className="mavis-hud">
      <div className="mavis-hud__left">
        {toolbar}
        <AvatarBox speaking={state?.speaking} />
        {/* plain push-to-talk: no speakerName, so MAVIS never gates the mic on an enrolled voice print */}
        <ChatPanel transcript={state?.transcript} chatEnabled={state?.session?.policy?.chat}
          joyPtt={!!state?.ctrl?.ptt} />
      </div>

      <div className="mavis-hud__right">
        <div className="mavis-hud__top">
          <span className="pill">{state?.phase || "—"}</span>
          <span className="muted">Circuit {state?.circuit ?? "—"} / {state?.total_circuits ?? "—"}</span>
          <SopChip sop={state?.sop} />
        </div>

        <DemoBanner demo={state?.demo} />
        {/* the to-do list sits beside the map, in the layout -- never floating
            over the map or the toolbar */}
        <div className="mavis-hud__mapwrap">
          <MapCanvas state={state} />
          {state?.lesson?.type === "familiarization" && <TodoCard todo={state?.fam_todo} />}
        </div>
        {state?.lesson?.type === "familiarization" && <LookCard lookAt={state?.look_at} />}
      </div>

      {state?.scorecard && <ScorecardOverlay scorecard={state.scorecard} onMainMenu={onMainMenu} onDiscard={onDiscard} onFlyAgain={onFlyAgain} />}
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
