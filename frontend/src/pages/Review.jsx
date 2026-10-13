import { useParams, useNavigate } from "react-router-dom";
import { useSessionData } from "../components/session/useSessionData.js";
import LoadingBar from "../components/session/LoadingBar.jsx";
import ScreensPanel from "../components/session/ScreensPanel.jsx";
import SessionTimeline from "../components/session/SessionTimeline.jsx";
import ScanPathCard from "../components/session/ScanPathCard.jsx";
import FlightTelemetryCard from "../components/session/FlightTelemetryCard.jsx";
import RegionPieCard from "../components/session/RegionPieCard.jsx";
import PhaseModal from "../components/session/PhaseModal.jsx";
import "./review.css";

// Which curriculum this session's flight(s) came from, labelled at the top of the page.
// "Fly again" / Restart add flights to the same session, so each gets its own chip.
function Curriculum({ flights }) {
  if (!flights?.length) return null;
  return (
    <div className="rev-curriculum">
      <span className="label">Curriculum</span>
      {flights.map((f) => (
        <span key={f.flight}
          className={"chip" + (f.curriculum === "free_flight" ? " chip--free" : "")}
          title={[f.module, f.mode && `${f.mode} mode`, f.outcome].filter(Boolean).join(" · ")}>
          {flights.length > 1 && <b>Flight {f.flight}</b>}
          {f.title}
          {f.outcome && f.outcome !== "completed" && <i> · {f.outcome}</i>}
        </span>
      ))}
    </div>
  );
}

export default function Review() {
  const { id } = useParams();
  const nav = useNavigate();
  const { err, summary, flight, flightLoaded, loadProgress, screens, timeline, scanPath, liveState, regionPie, phases } =
    useSessionData({ id, live: false });

  if (err) return <div className="rev-error">Couldn't load this session.<br />{err}</div>;
  if (!summary || !flightLoaded)
    return <div className="rev-loading"><LoadingBar label="Loading session…" progress={loadProgress} /></div>;
  if (!flight.length)
    return (
      <div className="rev-error">
        No flight data in this session yet.
        <br />
        <button className="rail-btn" style={{ width: "auto", padding: "6px 14px", marginTop: 12 }}
          onClick={() => nav("/upload")}>
          Upload data
        </button>
      </div>
    );

  const altMax = flight.reduce((m, r) => (r.altitude_ft > m ? r.altitude_ft : m), 0);

  return (
    <div className="rev rev--embedded rev--no-rail rev--zoom80">
      {/* No rail here -- the sidebar (outside .rev) already covers
          Sessions/Analytics/AOI Zones now that this view is embedded. */}
      <div className="rev-main">
        <div className="rev-titlebar">
          <h1>{summary.session.name}</h1>
          <span className="sub">{summary.session.aircraft || "aircraft n/a"} · {summary.session.sim_source || "sim n/a"}</span>
          <span className="spacer" />
        </div>
        <Curriculum flights={summary.flights} />

        <ScreensPanel {...screens} />
        <SessionTimeline {...timeline} />

        <div className="rev-bottom rev-bottom--telemetry">
          <ScanPathCard {...scanPath} />
          <RegionPieCard {...regionPie} />
          <FlightTelemetryCard {...liveState} altMax={altMax} />
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
