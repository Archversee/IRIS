import { useState } from "react";
import { useMavisState, useMavisStatus } from "./hooks/useMavisSocket.js";
import MainMenu from "./components/MainMenu.jsx";
import InFlightPanel from "./components/InFlightPanel.jsx";

// Slice B/C: main menu (mode cards, name, lesson picker) -> in-flight
// readout. Styled with IRIS's own design system (styles.css) rather than
// a separate MAVIS theme, deliberately minimal -- not a port of the
// original app's full gauge/map HUD. See docs/LIVE_REBUILD_PLAN.md.
export default function LiveCoach() {
  const status = useMavisStatus();
  const state = useMavisState();
  // Also true if a session is already active (e.g. navigated back to
  // /live mid-lesson) so this doesn't force you back through the menu.
  const [startedHere, setStartedHere] = useState(false);
  const inLesson = startedHere || Boolean(state?.session_id);

  return (
    <div className="container">
      <p className="muted" style={{ marginBottom: 16 }}>
        MAVIS: <span className={status === "open" ? "" : "err"}>{status}</span>
      </p>
      {inLesson ? (
        <InFlightPanel state={state} />
      ) : (
        <MainMenu state={state} onStarting={() => setStartedHere(true)} />
      )}
    </div>
  );
}
