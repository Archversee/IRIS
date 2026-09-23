import { useEffect, useState } from "react";
import { useMavisState, useMavisStatus } from "./hooks/useMavisSocket.js";
import { mavisApi } from "./api/mavisApi.js";
import MainMenu from "./components/MainMenu.jsx";
import InFlightPanel from "./components/InFlightPanel.jsx";
import ReadingLesson from "./components/reading/ReadingLesson.jsx";
import "./live.css";

// Slice B/C/I: main menu -> reading lesson (preflight/cockpit walk-around,
// no telemetry) OR in-flight HUD. Embedded in the shared sidebar shell,
// not fullscreen. See docs/LIVE_REBUILD_PLAN.md.
export default function LiveCoach() {
  const status = useMavisStatus();
  const state = useMavisState();
  // Also true if a session is already active (e.g. navigated back to
  // /live mid-lesson) so this doesn't force you back through the menu.
  const [startedHere, setStartedHere] = useState(false);
  const [dismissedReading, setDismissedReading] = useState(false);
  const inLesson = startedHere || Boolean(state?.session_id);
  const isReading = state?.active_lesson?.kind === "reading";

  // Picking a different (or the same) reading lesson always shows it,
  // even if a previous one was dismissed back to the menu.
  useEffect(() => {
    setDismissedReading(false);
  }, [state?.lesson_id]);

  async function endSession() {
    if (!window.confirm("End this lesson and go back to the main menu?")) return;
    try {
      await mavisApi.quit();
    } catch {
      // best-effort -- still return to the menu even if the request failed
    }
    setStartedHere(false);
  }

  if (isReading && !dismissedReading && !inLesson) {
    return (
      <div className="mavis-shell">
        <ReadingLesson
          lessonId={state.lesson_id}
          data={state?.reading_lessons?.[state.lesson_id]}
          onExit={() => setDismissedReading(true)}
        />
      </div>
    );
  }

  return (
    <div className={inLesson ? "mavis-shell mavis-shell--hud" : "mavis-shell"}>
      <div className="mavis-topbar">
        <span className={`live-coach__status live-coach__status--${status}`}>MAVIS: {status}</span>
        {inLesson && (
          <button type="button" className="secondary" onClick={endSession}>
            ‹ End Session
          </button>
        )}
      </div>
      {inLesson ? (
        <InFlightPanel state={state} />
      ) : (
        <MainMenu state={state} onStarting={() => setStartedHere(true)} />
      )}
    </div>
  );
}
