// Scan path as a coloured strip under the session timeline ("scarf plot"):
// one block per glance, width = how long it lasted, colour = which area.
// Shares the chart's time axis, so it lines up with the altitude/speed
// lines and flight-phase bands above it. Click a block to jump there;
// click a legend chip to hide/show that area.
//
// The strip is inset to match the chart's plot area -- SessionTimeline's
// LineChart has margin.left -28 + a 30px YAxis (= 2px) and margin.right 8.
const fmt = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

export default function ScanStrip({ scanLog, scanLogColor, allAois, hiddenAois, toggleAoiHidden, chartMinT, chartMaxT, curT, seekTo }) {
  const span = chartMaxT - chartMinT;
  if (!scanLog?.length || !(span > 0)) return null;
  const pct = (t) => ((Math.min(Math.max(t, chartMinT), chartMaxT) - chartMinT) / span) * 100;

  // share of total gaze time per area, for the legend
  const totals = new Map();
  let all = 0;
  for (const r of scanLog) { totals.set(r.aoi, (totals.get(r.aoi) || 0) + r.dur); all += r.dur; }

  return (
    <div className="scan-strip">
      <div className="scan-strip__head">
        <span className="scan-strip__title">Scan path</span>
        <div className="scan-strip__legend">
          {allAois.map((a) => {
            const off = hiddenAois.has(a);
            return (
              <button key={a} type="button" className={"scan-strip__chip" + (off ? " is-off" : "")}
                onClick={() => toggleAoiHidden(a)} title={off ? `Show ${a}` : `Hide ${a}`}>
                <span className="swatch" style={{ background: scanLogColor.get(a) }} />
                {a}
                <span className="scan-strip__pct">{all ? Math.round(((totals.get(a) || 0) / all) * 100) : 0}%</span>
              </button>
            );
          })}
        </div>
      </div>
      <div className="scan-strip__track">
        {scanLog.map((r, i) => {
          if (hiddenAois.has(r.aoi) || r.end <= chartMinT || r.start >= chartMaxT) return null;
          const left = pct(r.start), width = Math.max(pct(r.end) - left, 0.15);
          return (
            <button key={i} type="button" className="scan-strip__seg"
              style={{ left: `${left}%`, width: `${width}%`, background: scanLogColor.get(r.aoi) }}
              title={`${r.aoi} · ${r.dur.toFixed(1)}s (${fmt(r.start)}–${fmt(r.end)})`}
              onClick={() => seekTo(r.start)} />
          );
        })}
        <div className="scan-strip__cursor" style={{ left: `${pct(curT)}%` }} />
      </div>
    </div>
  );
}
