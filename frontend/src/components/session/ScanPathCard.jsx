// Gaze fixation log: which AOI the pilot was looking at, in order, up to
// the current playback position, with a filter menu and minimum-dwell input.
export default function ScanPathCard({
  curEye, curAoi,
  aoiFilterOpen, setAoiFilterOpen, aoiFilterRef,
  allAois, hiddenAois, toggleAoiHidden, scanLogColor,
  minAoiDwellInput, setMinAoiDwellInput,
  scanLogRows, seekTo,
}) {
  return (
    <div className="rev-card">
      <div className="card-head">
        <h3>Scan path</h3>
        <span className="tag">gaze · {curEye ? (curEye.blink ? "blinking" : curAoi) : "no eye data"}</span>
        <div className="aoi-filter" ref={aoiFilterRef}>
          <button type="button" className="aoi-filter-btn" onClick={() => setAoiFilterOpen((o) => !o)}>
            Instruments {hiddenAois.size > 0 ? `(${allAois.length - hiddenAois.size}/${allAois.length})` : ""} ▾
          </button>
          {aoiFilterOpen && (
            <div className="aoi-filter-menu">
              {allAois.map((name) => (
                <label key={name} className="aoi-filter-item">
                  <span className="aoi-filter-name">
                    <span className="swatch" style={{ background: scanLogColor.get(name) }} />
                    {name}
                  </span>
                  <input type="checkbox" checked={!hiddenAois.has(name)}
                    onChange={() => toggleAoiHidden(name)} />
                </label>
              ))}
            </div>
          )}
        </div>
        <label className="min-dwell" title="Minimum fixation time">
          min glance
          <input type="number" min={0} step={0.1} value={minAoiDwellInput}
            onChange={(e) => setMinAoiDwellInput(e.target.value)} />
        </label>
      </div>
      {scanLogRows.length === 0 ? (
        <div className="scan-empty">No gaze fixations yet at this point in the flight.</div>
      ) : (
        <>
          <div className="scan-log-header">
            <span className="h-num">#</span>
            <span className="h-aoi">Area of Interest</span>
            <span className="h-range">Time range</span>
            <span className="h-dur">Duration</span>
          </div>
          <div className="scan-log">
            {scanLogRows.map((r, i) => {
              const color = scanLogColor.get(r.aoi);
              return (
                <button
                  key={i}
                  type="button"
                  className={"scan-log-row" + (r.isCurrent ? " current" : "")}
                  onClick={() => seekTo(r.start)}
                  title={`Jump to ${r.start.toFixed(1)}s`}
                >
                  <span className="badge" style={{ background: color }}>{r.order}</span>
                  <span className="aoi" style={r.isCurrent ? { color } : undefined}>{r.aoi}</span>
                  <span className="range">{r.start.toFixed(1)} → {r.isCurrent ? "now" : r.end.toFixed(1) + "s"}</span>
                  <span className="dur" style={r.isCurrent ? { color } : undefined}>{r.dur.toFixed(1)}s</span>
                </button>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
