import { useEffect, useRef, useState } from "react";
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

// Pure viewer now -- MAVIS is the only thing that ever starts, stops, or
// logs a session (see mavis_bridge.py/LiveAdapter.py), so this page never
// mints or ends anything. It just lists whichever MAVIS sessions are
// currently in progress (GET /sessions/running, i.e. source='mavis' with
// no ended_at yet) and lets you watch one -- same viewer layout as
// Review.jsx (via the shared useSessionData hook), just fed live instead
// of from a finished DB record. Screen capture uses the browser's own
// getDisplayMedia() picker per screen (see ScreensPanel), not a server-run
// recording -- that's OBS's job now, owned entirely by MAVIS/LiveAdapter.py.
const POLL_INTERVAL_MS = 3000;

export default function LiveSession() {
  const nav = useNavigate();
  const [running, setRunning] = useState([]);
  const [sessionId, setSessionId] = useState(null);
  const [listErr, setListErr] = useState(null);
  const [otwStream, setOtwStream] = useState(null);
  const [instrumentStream, setInstrumentStream] = useState(null);
  const pollRef = useRef(null);

  const { err, summary, flight, flightLoaded, screens, timeline, scanPath, liveState, regionPie, phases, followingLive } =
    useSessionData({ id: sessionId, live: true });

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      try {
        const rows = await api.listRunningSessions();
        if (cancelled) return;
        setRunning(rows);
        setListErr(null);
        // The session being watched ended (or was never in the list) --
        // drop back to the picker rather than keep polling a dead session.
        if (sessionId && !rows.some((r) => r.id === sessionId)) setSessionId(null);
      } catch (e) {
        if (!cancelled) setListErr(e.message);
      }
    }
    poll();
    pollRef.current = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(pollRef.current);
    };
  }, [sessionId]);

  // Browser screen-capture for the two live monitors -- an in-progress OBS
  // recording isn't a seekable/playable file, so the live view captures
  // the screen directly instead. Each button opens the browser's own
  // window/monitor picker.
  function stopStream(stream) {
    stream?.getTracks().forEach((t) => t.stop());
  }
  async function captureScreen(current, setter) {
    try {
      const stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
      stopStream(current);
      stream.getVideoTracks()[0].addEventListener("ended", () => setter(null));
      setter(stream);
    } catch {
      // user cancelled the picker
    }
  }
  const streamsRef = useRef({ otw: null, instrument: null });
  useEffect(() => { streamsRef.current = { otw: otwStream, instrument: instrumentStream }; });
  useEffect(() => () => {
    stopStream(streamsRef.current.otw);
    stopStream(streamsRef.current.instrument);
  }, []);

  if (err) return <div className="rev-error">Couldn't load this session.<br />{err}</div>;

  if (!sessionId) {
    return (
      <div className="rev rev--embedded">
        <div className="rev-main">
          <div className="rev-titlebar">
            <h1>Live Monitor</h1>
            <span className="sub">Pick a session MAVIS is currently running to watch it live.</span>
          </div>
          {listErr && <p className="sub">Couldn't load running sessions: {listErr}</p>}
          {running.length === 0 ? (
            <p className="sub">
              No MAVIS session is running right now. Start Basic Flight or Free Flight in MAVIS, then come back here.
            </p>
          ) : (
            <div className="rev-bottom" style={{ flexWrap: "wrap" }}>
              {running.map((s) => (
                <button
                  key={s.id}
                  className="card"
                  style={{ textAlign: "left", cursor: "pointer", minWidth: 220 }}
                  onClick={() => setSessionId(s.id)}
                >
                  <strong>{s.name || "MAVIS session"}</strong>
                  <div className="muted">
                    {s.aircraft || "aircraft n/a"} · {s.sim_source || "sim n/a"}
                  </div>
                  <div className="muted">
                    started {s.started_at ? new Date(s.started_at).toLocaleTimeString() : "—"}
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    );
  }

  const waitingForData = summary && flightLoaded && flight.length === 0;

  return (
    <div className="rev rev--embedded">
      <div className="rev-rail">
        <a className="rail-btn" title="Analytics" onClick={() => nav(`/sessions/${sessionId}/analytics`)} href="#">▦</a>
        <div className="rail-sep" />
      </div>

      <div className="rev-main">
        <div className="rev-titlebar">
          <h1>{summary?.session?.name || "Live"}</h1>
          <span className="sub">
            {summary ? `${summary.session.aircraft || "aircraft n/a"} · ${summary.session.sim_source || "sim n/a"}` : "loading…"}
          </span>
          <span className="spacer" />
          <span className={"live-badge" + (followingLive ? " is-live" : "")}>{followingLive ? "● LIVE" : "PAUSED"}</span>
          <button className="tl-btn" onClick={() => setSessionId(null)} style={{ marginLeft: 8 }}>
            ‹ Back to sessions
          </button>
        </div>

        {waitingForData && <p className="sub" style={{ margin: "-4px 0 4px" }}>Waiting for live flight data…</p>}

        <ScreensPanel {...screens} live
          otwStream={otwStream}
          onCaptureOtw={() => captureScreen(otwStream, setOtwStream)}
          onStopOtw={() => { stopStream(otwStream); setOtwStream(null); }}
          instrumentStream={instrumentStream}
          onCaptureInstrument={() => captureScreen(instrumentStream, setInstrumentStream)}
          onStopInstrument={() => { stopStream(instrumentStream); setInstrumentStream(null); }}
        />
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
