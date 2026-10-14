import { useCallback, useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api } from "../api/client.js";
import { useSessionData } from "../components/session/useSessionData.js";
import LoadingBar from "../components/session/LoadingBar.jsx";
import ScreensPanel from "../components/session/ScreensPanel.jsx";
import SessionTimeline from "../components/session/SessionTimeline.jsx";
import ScanPathCard from "../components/session/ScanPathCard.jsx";
import FlightTelemetryCard from "../components/session/FlightTelemetryCard.jsx";
import RegionPieCard from "../components/session/RegionPieCard.jsx";
import PhaseModal from "../components/session/PhaseModal.jsx";
import RuleChecksPanel, { RuleChecksChip } from "../components/session/RuleChecksPanel.jsx";
import "./review.css";

export default function Review() {
  const { id } = useParams();
  const nav = useNavigate();
  const { err, summary, flight, flightLoaded, loadProgress, screens, timeline, scanPath, liveState, regionPie, phases } =
    useSessionData({ id, live: false });
  // SOP rule checks: chip in the title bar, drawer with the details, bands on the timeline
  const [rules, setRules] = useState({ evaluation: null, missing: false });
  const [rulesOpen, setRulesOpen] = useState(false);
  const loadRules = useCallback(async () => {
    try {
      setRules({ evaluation: await api.ruleResults(id), missing: false });
    } catch (e) {
      setRules({ evaluation: null, missing: e.status === 404 });
    }
  }, [id]);
  useEffect(() => { loadRules(); }, [loadRules]);

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

  const curriculum = [...new Set((summary.flights || []).map((f) => f.title).filter(Boolean))].join(" · ");
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
          {/* which curriculum the session came from: top right, inside the title bar so the
              page grid keeps its four rows (an extra row squeezes the video screens) */}
          {curriculum && <span className="rev-curriculum" title="Curriculum"><small>Curriculum</small> {curriculum}</span>}
          <RuleChecksChip evaluation={rules.evaluation} missing={rules.missing} onClick={() => setRulesOpen(true)} />
        </div>

        <ScreensPanel {...screens} />
        <SessionTimeline {...timeline} />

        <div className="rev-bottom rev-bottom--telemetry">
          <ScanPathCard {...scanPath} />
          <RegionPieCard {...regionPie} />
          <FlightTelemetryCard {...liveState} altMax={altMax} />
        </div>
      </div>

      {rulesOpen && (
        <RuleChecksPanel sessionId={id} evaluation={rules.evaluation} missing={rules.missing} reload={loadRules}
          onClose={() => setRulesOpen(false)} seekTo={timeline.seekTo} flightStartMs={timeline.flightStartMs} />
      )}

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
