// Single draggable floating window for a flight-phase (takeoff/landing)
// analytics breakdown. Scoring is not implemented yet -- placeholder layout.
export default function PhaseModal({ w, onClose, onMouseDown, onDragStart, seekTo }) {
  return (
    <div className="phase-modal" style={{ left: w.x, top: w.y, zIndex: w.z }}
      onMouseDown={onMouseDown}>
      <div className="phase-modal-head" onMouseDown={onDragStart}>
        <h3>{w.label}</h3>
        <button className="phase-modal-close" onClick={onClose}>×</button>
      </div>
      <div className="phase-modal-sub">
        {w.start.toFixed(1)}s – {w.end.toFixed(1)}s
        <span className="phase-modal-dur">({(w.end - w.start).toFixed(1)}s)</span>
        <button className="phase-modal-jump" onClick={() => seekTo(w.start)}>
          Jump to start
        </button>
      </div>
      <div className="phase-pillars">
        <div className="pillar">
          <h4>Flight Precision Index</h4>
          <div className="pillar-score">—</div>
          <ul>
            <li><span>Glideslope RMSE</span><span>—</span></li>
            <li><span>Localizer RMSE</span><span>—</span></li>
            <li><span>Airspeed RMSE</span><span>—</span></li>
            <li><span>Altitude hold RMSE</span><span>—</span></li>
            <li><span>Control smoothness</span><span>—</span></li>
          </ul>
        </div>
        <div className="pillar">
          <h4>Visual Attention &amp; Scan Quality</h4>
          <div className="pillar-score">—</div>
          <ul>
            <li><span>Cross-check freq (OTW↔PFD)</span><span>—</span></li>
            <li><span>Avg fixation duration</span><span>—</span></li>
            <li><span>Primary/secondary coverage</span><span>—</span></li>
          </ul>
        </div>
        <div className="pillar">
          <h4>Cognitive Cost Index</h4>
          <div className="pillar-score">—</div>
          <ul>
            <li><span>Workload (pupil Z-score)</span><span>—</span></li>
            <li><span>Stress/fatigue (blink rate)</span><span>—</span></li>
          </ul>
        </div>
      </div>
      <div className="phase-modal-note">Scoring engine not implemented yet — placeholder layout only.</div>
    </div>
  );
}
