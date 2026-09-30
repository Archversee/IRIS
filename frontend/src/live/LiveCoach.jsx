import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMavisState, useMavisStatus } from "./hooks/useMavisSocket.js";
import { useInstructorVoice } from "./hooks/useInstructorVoice.js";
import { mavisApi } from "./api/mavisApi.js";
import { api } from "../api/client.js";
import MainMenu from "./components/MainMenu.jsx";
import FreeFlightMenu from "./components/FreeFlightMenu.jsx";
import InFlightPanel from "./components/InFlightPanel.jsx";
import ReadingLesson from "./components/reading/ReadingLesson.jsx";
import ConfirmDialog from "./components/ConfirmDialog.jsx";
import "./live.css";

// Slice B/C/I: main menu -> reading lesson (preflight/cockpit walk-around,
// no telemetry, shown via ?reading=<id> -- these are never selected
// server-side, see LessonTree.jsx's comment) OR in-flight HUD. Embedded
// in the shared sidebar shell, not fullscreen. See docs/LIVE_REBUILD_PLAN.md.
//
// `freeFlight` (from the /live/freeflight route) swaps the menu screen for
// FreeFlightMenu (single Start button, no visitor/trainee/pilot cards) --
// everything past that point (HUD, scoring, end-of-session flow) is
// identical, since MAVIS logs/records every session the same way now
// regardless of which menu started it.
export default function LiveCoach({ freeFlight = false }) {
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
  // Instructor voice plays in this browser while flying (not over the
  // scorecard -- same rule as the original MAVIS page).
  useInstructorVoice(state?.audio, inLesson && !state?.scorecard);

  // Marks this session as no longer in progress -- Live Monitor's picker
  // (GET /sessions/running, under IRIS now) filters on ended_at is null.
  // Not set merely because a scorecard appeared: "Fly again" keeps flying
  // under the same session_id, so only an actual quit ends it.
  async function markEnded() {
    const sid = state?.session_id;
    if (!sid) return;
    try {
      await api.updateSession(sid, { ended_at: new Date().toISOString() });
    } catch {
      // best-effort -- Live Monitor just keeps listing it as running
    }
  }

  // Topbar "End Session" -- doesn't jump back to the menu immediately
  // anymore: quit() populates state.scorecard (as a practice/incomplete
  // summary if the flight wasn't finished), so this stays on the HUD long
  // enough for that scorecard-then-upload flow below to run, same as a
  // natural circuit completion.
  async function confirmEndSession() {
    setConfirmingEnd(false);
    try {
      await mavisApi.quit();
    } catch {
      // quit() itself failed -- nothing to show a scorecard for, bail to the menu
      setStartedHere(false);
      return;
    }
    await markEnded();
  }

  // From the scorecard's "Main menu" button -- score first (already on
  // screen), then IRIS's own Upload form (name/pilot/aircraft/notes, CSV
  // ingest, video linking) pre-filled with this session, same as any other
  // flight's end-of-session flow. Goes all the way back through Upload
  // rather than straight to "/", since there's now always a save/upload
  // step between finishing a flight and returning to the dashboard.
  async function backToDashboardHome() {
    const sid = state?.session_id;
    try {
      await mavisApi.quit();
    } catch {
      // best-effort
    }
    await markEnded();
    setStartedHere(false);
    nav(sid ? `/upload?session=${sid}` : "/upload");
  }

  // From the scorecard's "Finish & discard" button -- same quit as above,
  // but deletes the IRIS session record instead of going to Upload, then
  // drops back to this page's own start menu (Free Flight / Basic Flight).
  async function finishAndDiscard() {
    const sid = state?.session_id;
    try {
      await mavisApi.quit();
    } catch {
      // best-effort
    }
    if (sid) {
      try {
        await api.deleteSession(sid);
      } catch {
        // best-effort -- fall back to at least marking it ended
        await markEnded();
      }
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

  const toolbar = (
    <div className="mavis-topbar">
      <span
        className={"mavis-status-dot" + (status === "open" ? " is-up" : "")}
        title={status === "open" ? "MAVIS backend running" : `MAVIS backend down (${status})`}
        aria-label={status === "open" ? "MAVIS backend running" : "MAVIS backend down"}
      />
      {inLesson && !state?.scorecard && (
        <button type="button" className="secondary" onClick={() => setConfirmingEnd(true)}>
          ‹ End Session
        </button>
      )}
    </div>
  );

  return (
    <div className={inLesson ? "mavis-shell mavis-shell--hud" : "mavis-shell mavis-shell--menu"}>
      {!inLesson && toolbar}
      {inLesson ? (
        <InFlightPanel state={state} toolbar={toolbar} onMainMenu={backToDashboardHome} onDiscard={finishAndDiscard} />
      ) : freeFlight ? (
        <FreeFlightMenu onStarting={() => setStartedHere(true)} />
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
