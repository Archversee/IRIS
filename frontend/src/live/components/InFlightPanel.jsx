import AvatarBox from "./AvatarBox.jsx";
import ChatPanel from "./ChatPanel.jsx";
import MapCanvas from "./MapCanvas.jsx";
import ScorecardOverlay from "./ScorecardOverlay.jsx";

// Two columns: avatar + chat on the left, and the ground track (planned
// vs. flown path) filling the whole right side top to bottom.
export default function InFlightPanel({ state, toolbar, onMainMenu }) {
  return (
    <div className="mavis-hud">
      <div className="mavis-hud__left">
        {toolbar}
        <AvatarBox speaking={state?.speaking} />
        <ChatPanel transcript={state?.transcript} chatEnabled={state?.session?.policy?.chat} />
      </div>

      <div className="mavis-hud__right">
        <MapCanvas state={state} />
      </div>

      {state?.scorecard && <ScorecardOverlay scorecard={state.scorecard} onMainMenu={onMainMenu} />}
    </div>
  );
}
