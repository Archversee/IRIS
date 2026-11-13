import { useCallback, useEffect, useRef, useState } from "react";
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
import DebriefPanel, { DebriefChip, useDebriefJob } from "../components/session/DebriefPanel.jsx";
import "./review.css";

// The docked column's width, as a share of the page (the CSS clamps it, see
// .rev--docked in review.css). Dragged like the sidebar; null = the default.
const DOCK_KEY = "reviewDockWidth";
function loadDockFrac() {
  try {
    const f = Number(localStorage.getItem(DOCK_KEY));
    return f > 0 && f < 1 ? f : null;
  } catch {
    return null;
  }
}

export default function Review() {
  const { id } = useParams();
  const nav = useNavigate();
  const { err, summary, flight, flightLoaded, loadProgress, screens, timeline, scanPath, liveState, regionPie, phases } =
    useSessionData({ id, live: false });
  // SOP rule checks, the AI debrief and the incidents sit in a column docked
  // beside the videos -- always on screen, nothing to open. The title-bar
  // chips still summarise them; clicking one points at its section.
  const [rules, setRules] = useState({ evaluation: null, missing: false });
  // starts writing the AI debrief + every broken-rule note in the background if any are missing
  const debriefJob = useDebriefJob(id);
  const [flash, setFlash] = useState(null);
  const flashTimer = useRef(null);
  const showDock = (which) => {
    document.getElementById(`rev-dock-${which}`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    setFlash(which);
    clearTimeout(flashTimer.current);
    flashTimer.current = setTimeout(() => setFlash(null), 1200);
  };
  useEffect(() => () => clearTimeout(flashTimer.current), []);

  // Drag the dock's left edge to resize it; double-click puts it back.
  // Widening it squeezes the ground track away first (ScreensPanel fitGround).
  const revRef = useRef(null), dockRef = useRef(null);
  const [dockFrac, setDockFrac] = useState(loadDockFrac);
  const [dockDragging, setDockDragging] = useState(false);
  useEffect(() => {
    if (dockDragging) return;
    try {
      if (dockFrac == null) localStorage.removeItem(DOCK_KEY);
      else localStorage.setItem(DOCK_KEY, String(dockFrac));
    } catch { /* storage unavailable -- fine */ }
  }, [dockFrac, dockDragging]);
  function startDockDrag(e) {
    e.preventDefault();
    setDockDragging(true);
    // a share of the page, so it holds whatever the window/zoom
    const fracAt = (x) => {
      const r = revRef.current.getBoundingClientRect();
      return Math.min(0.95, Math.max(0.05, (r.right - x) / r.width));
    };
    const onMove = (ev) => setDockFrac(fracAt(ev.clientX));
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      // keep what the CSS limits actually allowed, so the edge stays under the pointer next time
      const r = revRef.current.getBoundingClientRect();
      setDockFrac(dockRef.current.getBoundingClientRect().width / r.width);
      setDockDragging(false);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }
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

  return (
    <div ref={revRef} className={"rev rev--embedded rev--no-rail rev--docked rev--zoom80" + (dockDragging ? " is-dock-dragging" : "")}
      style={dockFrac != null ? { "--dock-w": `${(dockFrac * 100).toFixed(2)}%` } : undefined}>
      {/* No rail here -- the sidebar (outside .rev) already covers
          Sessions/Analytics/AOI Zones now that this view is embedded. */}
      <div className="rev-main">
        <div className="rev-titlebar">
          <h1>{summary.session.name}</h1>
          <span className="sub">{summary.session.aircraft || "aircraft n/a"} · {summary.session.sim_source || "sim n/a"}</span>
          <span className="spacer" />
          {/* which curriculum the session came from: top right, inside the title bar so the
              page grid keeps its four rows (an extra row squeezes the video screens) */}
          {/* pilot, score, grade, curriculum -- left to right */}
          {summary.pilot && <span className="rev-curriculum" title="Pilot"><small>Pilot</small> {summary.pilot}</span>}
          {summary.score?.total != null && (
            <span className="rev-curriculum" title="Score"><small>Score</small> {summary.score.total}</span>
          )}
          {summary.score?.grade && (
            <span className="rev-curriculum" title="Grade"><small>Grade</small> {summary.score.grade}</span>
          )}
          {curriculum && <span className="rev-curriculum" title="Curriculum"><small>Curriculum</small> {curriculum}</span>}
          <RuleChecksChip evaluation={rules.evaluation} missing={rules.missing}
            onClick={() => showDock("rules")} />
          <DebriefChip job={debriefJob} onClick={() => showDock("debrief")} />
        </div>

        <ScreensPanel {...screens} fitGround />
        <SessionTimeline {...timeline} />

        <div className="rev-bottom rev-bottom--telemetry">
          <ScanPathCard {...scanPath} />
          <RegionPieCard {...regionPie} />
          <FlightTelemetryCard {...liveState} />
        </div>
      </div>

      <aside className="rev-dock" ref={dockRef} aria-label="Debrief and SOP rule checks">
        <div className="rev-dock__resizer" role="separator" aria-orientation="vertical"
          title="Drag to resize · double-click to reset"
          onPointerDown={startDockDrag} onDoubleClick={() => setDockFrac(null)} />
        <div id="rev-dock-debrief" className={"rev-dock__slot" + (flash === "debrief" ? " is-flash" : "")}>
          <DebriefPanel sessionId={id} />
        </div>
        <div id="rev-dock-rules" className={"rev-dock__slot" + (flash === "rules" ? " is-flash" : "")}>
          <RuleChecksPanel sessionId={id} evaluation={rules.evaluation} missing={rules.missing} reload={loadRules}
            seekTo={timeline.seekTo} flightStartMs={timeline.flightStartMs} />
        </div>
      </aside>

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
