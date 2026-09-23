import { useState } from "react";
import { useMavisState, useMavisStatus } from "./hooks/useMavisSocket.js";
import MainMenu from "./components/MainMenu.jsx";
import InFlightPanel from "./components/InFlightPanel.jsx";
import "./live.css";

// Slice B/C: main menu (mode cards, name, lesson picker) -> in-flight
// readout. The menu keeps MAVIS's own dark visual identity (live.css);
// the in-flight readout borrows IRIS's card/metric/pill classes so it
// still reads as one connected screen. Embedded in the shared sidebar
// shell now, not fullscreen -- see docs/LIVE_REBUILD_PLAN.md.
export default function LiveCoach() {
  const status = useMavisStatus();
  const state = useMavisState();
  // Also true if a session is already active (e.g. navigated back to
  // /live mid-lesson) so this doesn't force you back through the menu.
  const [startedHere, setStartedHere] = useState(false);
  const inLesson = startedHere || Boolean(state?.session_id);

  if (!inLesson) {
    return (
      <div className="mavis-shell">
        <div className={`live-coach__status live-coach__status--${status}`}>MAVIS: {status}</div>
        <MainMenu state={state} onStarting={() => setStartedHere(true)} />
      </div>
    );
  }

  return (
    <div className="container live-coach">
      <div className={`live-coach__status live-coach__status--${status}`}>MAVIS: {status}</div>
      <InFlightPanel state={state} />
    </div>
  );
}
