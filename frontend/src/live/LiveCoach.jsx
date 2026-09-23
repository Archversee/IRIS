import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMavisState, useMavisStatus } from "./hooks/useMavisSocket.js";
import { mavisApi } from "./api/mavisApi.js";
import MainMenu from "./components/MainMenu.jsx";
import InFlightPanel from "./components/InFlightPanel.jsx";
import ReadingLesson from "./components/reading/ReadingLesson.jsx";
import ConfirmDialog from "./components/ConfirmDialog.jsx";
import "./live.css";

// Slice B/C/I: main menu -> reading lesson (preflight/cockpit walk-around,
// no telemetry, shown via ?reading=<id> -- these are never selected
// server-side, see LessonTree.jsx's comment) OR in-flight HUD. Embedded
// in the shared sidebar shell, not fullscreen. See docs/LIVE_REBUILD_PLAN.md.
export default function LiveCoach() {
  const status = useMavisStatus();
  const state = useMavisState();
  const [searchParams, setSearchParams] = useSearchParams();
  const nav = useNavigate();
  // Purely local, deliberately -- MAVIS never nulls out session_id on
  // quit (it only mints a fresh one on the next /demo/start), so deriving
  // "in lesson" from its mere presence shows a stale scorecard/session
  // forever after ending one. The original app owns this the same way
  // (a client-side body.inlesson flag), not from server state.
  const [startedHere, setStartedHere] = useState(false);
  const [confirmingEnd, setConfirmingEnd] = useState(false);
  const inLesson = startedHere;
  const readingId = searchParams.get("reading");

  async function confirmEndSession() {
    setConfirmingEnd(false);
    try {
      await mavisApi.quit();
    } catch {
      // best-effort -- still return to the menu even if the request failed
    }
    setStartedHere(false);
  }

  // From the scorecard's "Main menu" -- goes all the way back to the
  // dashboard's own home page, not just MAVIS's mode-selection screen.
  async function backToDashboardHome() {
    try {
      await mavisApi.quit();
    } catch {
      // best-effort
    }
    nav("/");
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
          <button type="button" className="secondary" onClick={() => setConfirmingEnd(true)}>
            ‹ End Session
          </button>
        )}
      </div>
      {inLesson ? (
        <InFlightPanel state={state} onMainMenu={backToDashboardHome} />
      ) : (
        <MainMenu state={state} onStarting={() => setStartedHere(true)} />
      )}
      {confirmingEnd && (
        <ConfirmDialog
          title="End session?"
          message="This will end the current lesson and take you back to the main menu."
          confirmLabel="End session"
          onConfirm={confirmEndSession}
          onCancel={() => setConfirmingEnd(false)}
        />
      )}
    </div>
  );
}
