import AvatarBox from "./AvatarBox.jsx";
import ChatPanel from "./ChatPanel.jsx";
import MapCanvas from "./MapCanvas.jsx";
import Gauges from "./Gauges.jsx";
import ControlBars from "./ControlBars.jsx";
import ScorecardOverlay from "./ScorecardOverlay.jsx";

// Two-column layout matching the original app's #left (avatar + chat) /
// #right (map, then a bottom row of lesson info / gauges / controls).
export default function InFlightPanel({ state, onMainMenu }) {
  const tel = state?.tel || {};

  return (
    <div className="mavis-hud">
      <div className="mavis-hud__left">
        <AvatarBox speaking={state?.speaking} />
        <ChatPanel transcript={state?.transcript} chatEnabled={state?.session?.policy?.chat} />
      </div>

      <div className="mavis-hud__right">
        <div className="mavis-hud__top">
          <span className="pill">{state?.phase || "—"}</span>
          <span className="muted">Circuit {state?.circuit ?? "—"} / {state?.total_circuits ?? "—"}</span>
        </div>

        <MapCanvas state={state} />

        <div className="mavis-hud__bottom">
          <div className="mavis-hud__card">
            <h4>{state?.active_lesson?.title || "Lesson"}</h4>
            <p className="muted">{state?.night ? "Night" : "Day"}</p>
          </div>
          <div className="mavis-hud__card">
            <Gauges tel={tel} scales={state?.scales} />
          </div>
          <div className="mavis-hud__card">
            <ControlBars tel={tel} />
          </div>
        </div>
      </div>

      {state?.scorecard && <ScorecardOverlay scorecard={state.scorecard} onMainMenu={onMainMenu} />}
    </div>
  );
}
