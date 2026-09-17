import { useParams, useNavigate } from "react-router-dom";
import { useSessionData } from "../components/session/useSessionData.js";
import ScreensPanel from "../components/session/ScreensPanel.jsx";
import SessionTimeline from "../components/session/SessionTimeline.jsx";
import ScanPathCard from "../components/session/ScanPathCard.jsx";
import LiveStateCard from "../components/session/LiveStateCard.jsx";
import RegionPieCard from "../components/session/RegionPieCard.jsx";
import PhaseModal from "../components/session/PhaseModal.jsx";
import "./review.css";

export default function Review() {
  const { id } = useParams();
  const nav = useNavigate();
  const { err, summary, flight, screens, timeline, scanPath, liveState, regionPie, phases } =
    useSessionData({ id, live: false });

  if (err) return <div className="rev-error">Couldn't load this session.<br />{err}</div>;
  if (!summary) return <div className="rev-loading">Loading session…</div>;
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

  return (
    <div className="rev">
      <div className="rev-rail">
        <a className="rail-btn" title="Sessions" onClick={() => nav("/sessions")} href="#">‹</a>
        <div className="rail-sep" />
        <a className="rail-btn" title="Analytics" onClick={() => nav(`/sessions/${id}/analytics`)} href="#">▦</a>
        <a className="rail-btn" title="AOI zones" onClick={() => nav("/aoi-zones")} href="#">▢</a>
      </div>

      <div className="rev-main">
        <div className="rev-titlebar">
          <h1>{summary.session.name}</h1>
          <span className="sub">{summary.session.aircraft || "aircraft n/a"} · {summary.session.sim_source || "sim n/a"}</span>
          <span className="spacer" />
        </div>

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
