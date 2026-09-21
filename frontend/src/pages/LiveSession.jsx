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

// Top-level "Live" nav destination. No session picker -- "Go Live" creates
// a fresh session on the spot, then this keeps rendering the same viewer
// layout as Review.jsx (via the shared useSessionData hook + components/
// session/* pieces), which fills in as LiveAdapter.py streams data.
export default function LiveSession() {
  const nav = useNavigate();
  const [sessionId, setSessionId] = useState(null);
  const [starting, setStarting] = useState(false);
  const [ending, setEnding] = useState(false);
  const [createErr, setCreateErr] = useState(null);
  const [launchWarning, setLaunchWarning] = useState(null);
  const [otwStream, setOtwStream] = useState(null);
  const [instrumentStream, setInstrumentStream] = useState(null);

  const { err, summary, flight, flightLoaded, screens, timeline, scanPath, liveState, regionPie, phases, followingLive } =
    useSessionData({ id: sessionId, live: true });

  async function goLive() {
    setStarting(true);
    setCreateErr(null);
    setLaunchWarning(null);
    try {
      const s = await api.createSession({ name: `Live – ${new Date().toLocaleString()}`, sim_source: "MSFS" });
      setSessionId(s.id);
      // Auto-launches LiveAdapter.py on the backend machine with this
      // session's id already filled in -- if that fails (e.g. the backend
      // isn't running on the same machine as MSFS/OBS), fall back to
      // telling the user to run it by hand instead of blocking Go Live.
      try {
        await api.launchLiveAdapter(s.id);
      } catch (e) {
        setLaunchWarning(
          `Couldn't auto-launch LiveAdapter.py (${e.message}). Run it manually: python Sim_Data_Adapter/LiveAdapter.py ${s.id}`
        );
      }
    } catch (e) {
      setCreateErr(e.message);
    } finally {
      setStarting(false);
    }
  }

  // Tells LiveAdapter.py to shut down cleanly (see stopAdapter's docstring),
  // then sends the user to Upload to fill in the name/pilot/aircraft/notes
  // that weren't known yet when Go Live created this session, plus link
  // the OBS video files -- same page the manual upload flow uses.
  async function endSession() {
    setEnding(true);
    try {
      await api.stopAdapter(sessionId);
    } catch {
      // best-effort -- adapter may already have disconnected/crashed, don't
      // block the user from moving on to fill in the session details
    } finally {
      setEnding(false);
    }
    nav(`/upload?session=${sessionId}`);
  }

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

  const notStarted = !sessionId;
  const waitingForData = sessionId && summary && flightLoaded && flight.length === 0;

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
            <>
              <span className={"live-badge" + (followingLive ? " is-live" : "")}>{followingLive ? "● LIVE" : "PAUSED"}</span>
              <button className="tl-btn" disabled={ending} onClick={endSession} style={{ marginLeft: 8 }}>
                {ending ? "Ending…" : "■ End Session"}
              </button>
            </>
          )}
        </div>

        {(notStarted || waitingForData || createErr || launchWarning) && (
          <p className="sub" style={{ margin: "-4px 0 4px" }}>
            {createErr
              ? `Couldn't start a live session: ${createErr}`
              : notStarted
              ? "Click ● Go Live above to start a new live session."
              : launchWarning
              ? launchWarning
              : "Waiting for live flight data… LiveAdapter.py was launched automatically."}
          </p>
        )}

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
