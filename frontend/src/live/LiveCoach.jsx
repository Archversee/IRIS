import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMavisState, useMavisStatus } from "./hooks/useMavisSocket.js";
import { mavisApi } from "./api/mavisApi.js";
import MainMenu from "./components/MainMenu.jsx";
import InFlightPanel from "./components/InFlightPanel.jsx";
import ReadingLesson from "./components/reading/ReadingLesson.jsx";
import "./live.css";

// Slice B/C/I: main menu -> reading lesson (preflight/cockpit walk-around,
// no telemetry, shown via ?reading=<id> -- these are never selected
// server-side, see LessonTree.jsx's comment) OR in-flight HUD. Embedded
// in the shared sidebar shell, not fullscreen. See docs/LIVE_REBUILD_PLAN.md.
export default function LiveCoach() {
  const status = useMavisStatus();
  const state = useMavisState();
  const [searchParams, setSearchParams] = useSearchParams();
  // Also true if a session is already active (e.g. navigated back to
  // /live mid-lesson) so this doesn't force you back through the menu.
  const [startedHere, setStartedHere] = useState(false);
  const inLesson = startedHere || Boolean(state?.session_id);
  const readingId = searchParams.get("reading");

  async function endSession() {
    if (!window.confirm("End this lesson and go back to the main menu?")) return;
    try {
      await mavisApi.quit();
    } catch {
      // best-effort -- still return to the menu even if the request failed
    }
    setStartedHere(false);
  }

  async function backToMenuFromScorecard() {
    try {
      await mavisApi.quit();
    } catch {
      // best-effort
    }
    setStartedHere(false);
  }

  if (readingId && !inLesson) {
    return (
      <div className="mavis-shell">
        <ReadingLesson
          lessonId={readingId}
          data={state?.reading_lessons?.[readingId]}
          onExit={() => setSearchParams({}, { replace: true })}
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
        <InFlightPanel state={state} onMainMenu={backToMenuFromScorecard} />
      ) : (
        <MainMenu
          state={state}
          onStarting={() => setStartedHere(true)}
          onOpenReading={(id) => setSearchParams({ reading: id })}
        />
      )}
    </div>
  );
}
