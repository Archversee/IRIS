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
// mints or ends anything. It polls the MAVIS sessions currently in
// progress (GET /sessions/running, i.e. source='mavis' with no ended_at
// yet) and opens straight onto the live one -- same viewer layout as
// Review.jsx (via the shared useSessionData hook), just fed live instead
// of from a finished DB record. The screens come from the backend grabbing
// the OTW/instrument windows by title (routers/capture.py); each screen's
// Capture button still opens the browser's own getDisplayMedia() picker as
// a manual override. Neither is a recording -- that's OBS's job, owned
// entirely by MAVIS/LiveAdapter.py.
//
// Layout: OTW | Instruments | Ground track, then a read-only session
// timeline (trend only -- it always follows the live edge), then flight
// telemetry as MAVIS-style gauges with a toggle to raw numbers.
const POLL_INTERVAL_MS = 3000;

export default function LiveSession() {
  const nav = useNavigate();
  const [sessionId, setSessionId] = useState(null);
  const [listErr, setListErr] = useState(null);
  const [listLoaded, setListLoaded] = useState(false); // first poll done (skeleton until then)
  const [otwStream, setOtwStream] = useState(null);
  const [instrumentStream, setInstrumentStream] = useState(null);
  const pollRef = useRef(null);
  // SOP rule checks come straight from MAVIS's live state (not through the
  // IRIS bridge); only shown when MAVIS is flying the session being watched.
  const mavis = useMavisState();
  const mavisSessionRef = useRef(null);
  useEffect(() => { mavisSessionRef.current = mavis?.session_id ?? null; });

  const { err, summary, flight, flightLoaded, screens, timeline, liveState, followingLive } =
    useSessionData({ id: sessionId, live: true });

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      try {
        const rows = await api.listRunningSessions();
        if (cancelled) return;
        setListErr(null);
        setListLoaded(true);
        // No picking: jump straight into whichever session is live --
        // the one MAVIS is flying right now if it's listed, else the
        // newest (rows come back started_at desc). Keep the current one
        // while it's still running; once it ends, move to the next live
        // one, or fall back to the "waiting for a flight" screen.
        setSessionId((cur) => {
          if (cur && rows.some((r) => r.id === cur)) return cur;
          const flying = rows.find((r) => r.id === mavisSessionRef.current);
          return (flying || rows[0])?.id ?? null;
        });
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
  }, []);

  // Auto screens: the backend grabs the OTW/instrument windows by title
  // (routers/capture.py), so they show up without anyone picking them.
  // Polled so a window opened (or reopened) mid-session gets picked up;
  // each time one (re)appears it gets a fresh nonce = a fresh connection.
  const [feeds, setFeeds] = useState({ otw: null, instrument: null });
  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    async function check() {
      try {
        const st = await api.captureStatus();
        if (cancelled) return;
        setFeeds((cur) => {
          const next = { ...cur };
          for (const s of ["otw", "instrument"]) {
            if (!st[s]?.available) next[s] = null;
            else if (!cur[s]) next[s] = Date.now();
          }
          return next.otw === cur.otw && next.instrument === cur.instrument ? cur : next;
        });
      } catch {
        if (!cancelled) setFeeds({ otw: null, instrument: null });
      }
    }
    check();
    const t = setInterval(check, POLL_INTERVAL_MS);
    return () => { cancelled = true; clearInterval(t); };
  }, [sessionId]);
  // Feed broke (window closed, backend restarted) -- drop it; the next
  // status poll brings it back if the window is still there.
  const dropFeed = (s) => setFeeds((cur) => ({ ...cur, [s]: null }));

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
    return <LiveSessionPicker loaded={listLoaded} error={listErr} />;
  }

  const waitingForData = summary && flightLoaded && flight.length === 0;
  // Straight from MAVIS while it's flying this session (the summary is only
  // loaded once, before MAVIS has read the aircraft): the sim's aircraft, and
  // the curriculum of the flight in progress -- its latest flight.
  const mavisHere = mavis?.session_id === sessionId;
  const aircraft = (mavisHere && mavis?.sim_aircraft) || summary?.session?.aircraft;
  const liveFlights = mavisHere ? mavis?.flights : null;
  const curriculum = liveFlights?.length
    ? liveFlights[liveFlights.length - 1].title
    : (mavisHere && mavis?.active_lesson?.title)
      || (summary?.flights || []).map((f) => f.title).filter(Boolean).pop();
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
            {summary ? `${aircraft || "aircraft n/a"} · ${summary.session.sim_source || "sim n/a"}` : "loading…"}
          </span>
          <span className="spacer" />
          {curriculum && <span className="rev-curriculum" title="Curriculum being flown"><small>Curriculum</small> {curriculum}</span>}
          {mavis?.session_id === sessionId && <RuleAlerts live={mavis?.sop_live} flying={!!mavis?.lesson_running} />}
          <span className={"live-badge" + (followingLive ? " is-live" : "")}>{followingLive ? "● LIVE" : "PAUSED"}</span>
        </div>

        {waitingForData && <p className="sub" style={{ margin: "-4px 0 4px" }}>Waiting for live flight data…</p>}

        {/* Ground-track scrubbing is off here: with no Go Live button, a
            click would leave the view stuck paused off the live edge. */}
        <ScreensPanel {...screens} live
          onGroundMouseDown={undefined} onGroundMouseMove={undefined} onGroundMouseUp={undefined}
          otwFeed={feeds.otw && api.captureFeedUrl("otw", feeds.otw)}
          onOtwFeedError={() => dropFeed("otw")}
          instrumentFeed={feeds.instrument && api.captureFeedUrl("instrument", feeds.instrument)}
          onInstrumentFeedError={() => dropFeed("instrument")}
          otwStream={otwStream}
          onCaptureOtw={() => captureScreen(otwStream, setOtwStream)}
          onStopOtw={() => { stopStream(otwStream); setOtwStream(null); }}
          instrumentStream={instrumentStream}
          onCaptureInstrument={() => captureScreen(instrumentStream, setInstrumentStream)}
          onStopInstrument={() => { stopStream(instrumentStream); setInstrumentStream(null); }}
        />
        <SessionTimeline {...timeline} readOnly />

        <FlightTelemetryCard {...liveState} altMax={altMax} live
          bands={mavisHere ? mavis?.bands : null} />
      </div>
    </div>
  );
}
