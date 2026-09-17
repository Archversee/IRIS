import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client.js";
import { useSessionData } from "../components/session/useSessionData.js";
import ScreensPanel from "../components/session/ScreensPanel.jsx";
import SessionTimeline from "../components/session/SessionTimeline.jsx";
import ScanPathCard from "../components/session/ScanPathCard.jsx";
import LiveStateCard from "../components/session/LiveStateCard.jsx";
import RegionPieCard from "../components/session/RegionPieCard.jsx";
import PhaseModal from "../components/session/PhaseModal.jsx";
import "./review.css";

// Top-level "Live" nav destination. No session picker -- "Go Live" creates
// a fresh session on the spot, then this keeps rendering the same viewer
// layout as Review.jsx (via the shared useSessionData hook + components/
// session/* pieces), which fills in as MSFSAdapter.py streams data.
export default function LiveSession() {
  const nav = useNavigate();
  const [sessionId, setSessionId] = useState(null);
  const [starting, setStarting] = useState(false);
  const [createErr, setCreateErr] = useState(null);

  const { err, summary, flight, screens, timeline, scanPath, liveState, regionPie, phases, followingLive } =
    useSessionData({ id: sessionId, live: true });

  async function goLive() {
    setStarting(true);
    setCreateErr(null);
    try {
      const s = await api.createSession({ name: `Live – ${new Date().toLocaleString()}`, sim_source: "MSFS" });
      setSessionId(s.id);
    } catch (e) {
      setCreateErr(e.message);
    } finally {
      setStarting(false);
    }
  }

  if (err) return <div className="rev-error">Couldn't load this session.<br />{err}</div>;

  const notStarted = !sessionId;
  const waitingForData = sessionId && summary && flight.length === 0;

  return (
    <div className="rev">
      <div className="rev-rail">
        <a className="rail-btn" title="Sessions" onClick={() => nav("/sessions")} href="#">‹</a>
        <div className="rail-sep" />
        {sessionId && <a className="rail-btn" title="Analytics" onClick={() => nav(`/sessions/${sessionId}/analytics`)} href="#">▦</a>}
        <a className="rail-btn" title="AOI zones" onClick={() => nav("/aoi-zones")} href="#">▢</a>
      </div>

      <div className="rev-main">
        <div className="rev-titlebar">
          <h1>{summary?.session?.name || "Live"}</h1>
          <span className="sub">
            {summary ? `${summary.session.aircraft || "aircraft n/a"} · ${summary.session.sim_source || "sim n/a"}` : "no active session"}
          </span>
          <span className="spacer" />
          {notStarted ? (
            <button className="tl-btn go-live" disabled={starting} onClick={goLive}>
              {starting ? "Starting…" : "● Go Live"}
            </button>
          ) : (
            <span className={"live-badge" + (followingLive ? " is-live" : "")}>{followingLive ? "● LIVE" : "PAUSED"}</span>
          )}
        </div>

        {(notStarted || waitingForData || createErr) && (
          <p className="sub" style={{ margin: "-4px 0 4px" }}>
            {createErr
              ? `Couldn't start a live session: ${createErr}`
              : notStarted
              ? "Click ● Go Live above to start a new live session."
              : `Waiting for live flight data… point MSFSAdapter.py's SESSION_ID at ${sessionId} and run it.`}
          </p>
        )}

        <ScreensPanel {...screens} />
        <SessionTimeline {...timeline} />

        <div className="rev-bottom">
          <ScanPathCard {...scanPath} />
          <LiveStateCard {...liveState} />
          <RegionPieCard {...regionPie} />
        </div>
      </div>

      {phases.openPhases.map((w) => (
        <PhaseModal key={w.winId} w={w}
          onClose={() => phases.onClose(w.winId)}
          onMouseDown={() => phases.onBringToFront(w.winId)}
          onDragStart={(e) => phases.onDragStart(w.winId, e)}
          seekTo={phases.seekTo}
        />
      ))}
    </div>
  );
}
