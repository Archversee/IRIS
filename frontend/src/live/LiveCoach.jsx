import { useMavisState, useMavisStatus } from "./hooks/useMavisSocket.js";
import "./live.css";

// Slice A: proves the WS plumbing to MAVIS works end-to-end (dev proxy or
// prod reverse proxy -> MAVIS's /ws -> this hook -> a render) before any
// real UI is built on top. See docs/LIVE_REBUILD_PLAN.md for the full
// slice plan (B: splash+menu, C: in-flight HUD, D: map, ...).
export default function LiveCoach() {
  const status = useMavisStatus();
  const state = useMavisState();

  return (
    <div className="live-coach">
      <div className={`live-coach__status live-coach__status--${status}`}>
        MAVIS /ws: {status}
        {state && ` — session_id: ${state.session_id ?? "(none yet — press Start in MAVIS)"}`}
      </div>
      <pre className="live-coach__dump">
        {state ? JSON.stringify(state, null, 2) : "Waiting for first state push…"}
      </pre>
    </div>
  );
}
