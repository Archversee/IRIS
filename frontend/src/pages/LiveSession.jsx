import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client.js";
import { useSessionData } from "../components/session/useSessionData.js";
import ScreensPanel from "../components/session/ScreensPanel.jsx";
import SessionTimeline from "../components/session/SessionTimeline.jsx";
import FlightTelemetryCard from "../components/session/FlightTelemetryCard.jsx";
import LiveSessionPicker from "../components/session/LiveSessionPicker.jsx";
import RuleAlerts from "../live/components/RuleAlerts.jsx";
import { useMavisState } from "../live/hooks/useMavisSocket.js";
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
//
// Layout: OTW | Instruments | Ground track, then a read-only session
// timeline (trend only -- it always follows the live edge), then flight
// telemetry as MAVIS-style gauges with a toggle to raw numbers.
const POLL_INTERVAL_MS = 3000;

export default function LiveSession() {
  const nav = useNavigate();
  const [running, setRunning] = useState([]);
  const [sessionId, setSessionId] = useState(null);
  const [listErr, setListErr] = useState(null);
  const [listLoaded, setListLoaded] = useState(false); // first poll done (skeleton until then)
  const [otwStream, setOtwStream] = useState(null);
  const [instrumentStream, setInstrumentStream] = useState(null);
  const pollRef = useRef(null);
  // SOP rule checks come straight from MAVIS's live state (not through the
  // IRIS bridge); only shown when MAVIS is flying the session being watched.
  const mavis = useMavisState();

  const { err, summary, flight, flightLoaded, screens, timeline, liveState, followingLive } =
    useSessionData({ id: sessionId, live: true });

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      try {
        const rows = await api.listRunningSessions();
        if (cancelled) return;
        setRunning(rows);
        setListErr(null);
        setListLoaded(true);
        // The session being watched ended (or was never in the list) --
        // drop back to the picker rather than keep polling a dead session.
        if (sessionId && !rows.some((r) => r.id === sessionId)) setSessionId(null);
      } catch (e) {
        if (!cancelled) { setListErr(e.message); setListLoaded(true); }
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
    return <LiveSessionPicker running={running} loaded={listLoaded} error={listErr} onPick={setSessionId} />;
  }

  const waitingForData = summary && flightLoaded && flight.length === 0;
  const altMax = flight.reduce((m, r) => (r.altitude_ft > m ? r.altitude_ft : m), 0);

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
          {mavis?.session_id === sessionId && <RuleAlerts live={mavis?.sop_live} flying={!!mavis?.lesson_running} />}
          <span className={"live-badge" + (followingLive ? " is-live" : "")}>{followingLive ? "● LIVE" : "PAUSED"}</span>
        </div>

        {waitingForData && <p className="sub" style={{ margin: "-4px 0 4px" }}>Waiting for live flight data…</p>}

        {/* Ground-track scrubbing is off here: with no Go Live button, a
            click would leave the view stuck paused off the live edge. */}
        <ScreensPanel {...screens} live
          onGroundMouseDown={undefined} onGroundMouseMove={undefined} onGroundMouseUp={undefined}
          otwStream={otwStream}
          onCaptureOtw={() => captureScreen(otwStream, setOtwStream)}
          onStopOtw={() => { stopStream(otwStream); setOtwStream(null); }}
          instrumentStream={instrumentStream}
          onCaptureInstrument={() => captureScreen(instrumentStream, setInstrumentStream)}
          onStopInstrument={() => { stopStream(instrumentStream); setInstrumentStream(null); }}
        />
        <SessionTimeline {...timeline} readOnly />

        <FlightTelemetryCard {...liveState} altMax={altMax} />
      </div>
    </div>
  );
}
