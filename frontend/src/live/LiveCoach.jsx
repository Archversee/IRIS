import { useState } from "react";
import { useMavisState, useMavisStatus } from "./hooks/useMavisSocket.js";
import MainMenu from "./components/MainMenu.jsx";
import InFlightPanel from "./components/InFlightPanel.jsx";
import "./live.css";

// Slice B/C: main menu (mode cards, name, lesson picker) -> in-flight HUD
// (avatar, chat, ground track, gauges, controls -- matching the original
// app's layout). Embedded in the shared sidebar shell, not fullscreen.
// See docs/LIVE_REBUILD_PLAN.md.
export default function LiveCoach() {
  const status = useMavisStatus();
  const state = useMavisState();
  // Also true if a session is already active (e.g. navigated back to
  // /live mid-lesson) so this doesn't force you back through the menu.
  const [startedHere, setStartedHere] = useState(false);
  const inLesson = startedHere || Boolean(state?.session_id);

  return (
    <div className={inLesson ? "mavis-shell mavis-shell--hud" : "mavis-shell"}>
      <div className={`live-coach__status live-coach__status--${status}`}>MAVIS: {status}</div>
      {inLesson ? (
        <InFlightPanel state={state} />
      ) : (
        <MainMenu state={state} onStarting={() => setStartedHere(true)} />
      )}
    </div>
  );
}
