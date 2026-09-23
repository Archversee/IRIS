import { useState } from "react";
import { useMavisState, useMavisStatus } from "./hooks/useMavisSocket.js";
import SplashScreen from "./components/SplashScreen.jsx";
import MainMenu from "./components/MainMenu.jsx";
import "./live.css";

// Slice B: splash -> main menu (mode cards, name, lesson picker) -> "in
// lesson". The in-lesson HUD itself (gauges, map, chat, avatar) is Slice C
// and still just dumps raw state -- see docs/LIVE_REBUILD_PLAN.md.
export default function LiveCoach() {
  const status = useMavisStatus();
  const state = useMavisState();
  const [booted, setBooted] = useState(false);
  const [inLesson, setInLesson] = useState(false);

  if (!booted) {
    return <SplashScreen onContinue={() => setBooted(true)} />;
  }

  if (!inLesson) {
    return (
      <div className="mavis-shell">
        <div className={`live-coach__status live-coach__status--${status}`}>MAVIS: {status}</div>
        <MainMenu state={state} onStarting={() => setInLesson(true)} />
      </div>
    );
  }

  return (
    <div className="live-coach">
      <div className={`live-coach__status live-coach__status--${status}`}>
        MAVIS /ws: {status}
        {state && ` — session_id: ${state.session_id ?? "(starting…)"}`}
      </div>
      <pre className="live-coach__dump">
        {state ? JSON.stringify(state, null, 2) : "Waiting for first state push…"}
      </pre>
    </div>
  );
}
