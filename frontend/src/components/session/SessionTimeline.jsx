import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine, ReferenceArea, CartesianGrid,
} from "recharts";
import { METRICS, WL_BAND_COLOR, WL_BAND_LABEL, WL_MAX, WL_MIN } from "./constants.js";
import { fmt } from "./utils.js";

function TLTooltip({ active, payload }) {
  if (!active || !payload || !payload.length) return null;
  const d = payload[0].payload;
  const rows = [
    ["Altitude", fmt(d.altitudeR, 0), "ft"],
    ["Airspeed", fmt(d.airspeedR, 1), "kt"],
    ["Vert speed", fmt(d.vspeedR, 0), "fpm"],
  ];
  if (d.workloadIndex) {
    rows.push(["Workload", d.workloadR == null ? "—" : fmt(d.workloadR, 2), WL_BAND_LABEL[d.workloadBand] || ""],
      ["Pupil activity (LHIPA)", fmt(d.lhipaR, 2), "/s"],
      ["Scan spread", fmt(d.scanSpreadR, 2), "bits"], ["Scan randomness", fmt(d.scanRandomR, 2), "bits"]);
  }
  return (
    <div style={{ background: "#0f1a24", border: "1px solid #263341", borderRadius: 6, padding: "8px 10px", fontSize: 12 }}>
      <div style={{ color: "#8194a6", marginBottom: 4 }}>T + {d.t}s</div>
      {rows.map(([k, v, u], i) => (
        <div key={i} style={{ display: "flex", gap: 12, justifyContent: "space-between" }}>
          <span style={{ color: "#8194a6" }}>{k}</span>
          <span style={{ fontVariantNumeric: "tabular-nums" }}>{v} {u}</span>
        </div>
      ))}
    </div>
  );
}

// Workload's y position on the chart (it's drawn on the fixed WL_MIN..WL_MAX scale)
const wlY = (wl) => (wl - WL_MIN) / (WL_MAX - WL_MIN);

// Metrics drawn from the workload index can't be shown until it has loaded.
function unavailable(m, hasIndex) {
  return m.needs === "index" && !hasIndex;
}

// Chart + transport controls + flight event buttons (MAVIS to-do steps / phases,
// or a takeoff/landing guess for sessions without any).
// `readOnly` (Live Monitor) keeps just the trend chart: no scrubbing, no
// clickable phases/event buttons, no playback transport.
export default function SessionTimeline({
  readOnly = false,
  chartSeries, timelineTicks, filters, setFilters, hasIndex = false, workloadBands = null,
  flightPhases, chartMinT, chartMaxT, totalT, curT,
  onChartMouseDown, onChartMouseMove, onChartMouseUp,
  seekTo, openPhase,
  playing, onTogglePlay, seek, onRestart,
  speed, setSpeed,
  live, followingLive, goLive,
}) {
  // the Live Monitor (readOnly) leaves out what comes from the after-flight workload index
  const metrics = readOnly ? METRICS.filter((m) => !m.needs) : METRICS;
  // workload bands: shaded Low / Medium / High zones behind the chart
  const showBands = !!workloadBands && filters.workload && hasIndex;
  const zones = showBands ? [
    { band: "low", y1: 0, y2: wlY(workloadBands.medium), opacity: 0.05 },
    { band: "medium", y1: wlY(workloadBands.medium), y2: wlY(workloadBands.high), opacity: 0.08 },
    { band: "high", y1: wlY(workloadBands.high), y2: 1, opacity: 0.1 },
  ] : [];
  return (
    <div className="rev-timeline">
      <div className="tl-head">
        <h2>Session timeline</h2>
        <div className="filters">
          {metrics.map((m) => {
            const disabled = unavailable(m, hasIndex);
            return (
              <button key={m.key} disabled={disabled}
                title={m.key === "workload"
                  ? "Eye-based workload index: fixation rate, blink rate and pupil activity (LHIPA) against this pilot's "
                    + "usual on this flight. "
                    + "0 = usual. Shaded zones: green Low, amber Medium, red High."
                  : m.needs === "index" ? `${m.label}: gaze entropy over the AOIs (${m.unit}), from the workload index.` : undefined}
                className={"filter-chip" + (filters[m.key] && !disabled ? " on" : "")}
                onClick={() => setFilters((f) => ({ ...f, [m.key]: !f[m.key] }))}>
                <span className="swatch" style={{ background: m.color }} />{m.label}
              </button>
            );
          })}
          {flightPhases.length > 0 && (
            <button className={"filter-chip" + (filters.events !== false ? " on" : "")}
              title="Show or hide the shaded event boxes on the chart (the event buttons below stay)"
              onClick={() => setFilters((f) => ({ ...f, events: f.events === false }))}>
              <span className="swatch" style={{ background: "repeating-linear-gradient(45deg, #38bdf8 0 2px, #a78bfa 2px 4px)" }} />
              Events
            </button>
          )}
        </div>
      </div>
      <ResponsiveContainer width="100%" height={150}>
        <LineChart data={chartSeries} margin={{ top: 4, right: 8, bottom: 0, left: -28 }}
          style={{ cursor: readOnly ? "default" : "pointer" }}
          onMouseDown={readOnly ? undefined : onChartMouseDown}
          onMouseMove={readOnly ? undefined : onChartMouseMove}
          onMouseUp={readOnly ? undefined : onChartMouseUp}
        >
          <CartesianGrid stroke="#1b2530" strokeDasharray="3 3" />
          <XAxis dataKey="t" type="number" domain={["dataMin", "dataMax"]} ticks={timelineTicks} interval={0}
            stroke="#5c6f82" tick={{ fontSize: 11 }} unit="s" />
          <YAxis stroke="#5c6f82" tick={false} domain={[0, 1]} width={30} />
          <Tooltip content={<TLTooltip />} />
          {zones.map((z) => (
            <ReferenceArea key={`zone-${z.band}`} y1={z.y1} y2={z.y2} fill={WL_BAND_COLOR[z.band]} fillOpacity={z.opacity}
              stroke="none" style={{ pointerEvents: "none" }} ifOverflow="hidden"
              label={{ value: `${WL_BAND_LABEL[z.band]} workload`, position: "insideTopRight",
                       fill: WL_BAND_COLOR[z.band], fillOpacity: 0.75, fontSize: 10 }} />
          ))}
          {filters.events !== false && flightPhases.map((p) => {
            const minSpan = Math.max(1, totalT * 0.008);
            let x1 = p.start, x2 = p.end;
            if (x2 - x1 < minSpan) {
              const pad = (minSpan - (x2 - x1)) / 2;
              x1 = Math.max(chartMinT, p.start - pad);
              x2 = Math.min(chartMaxT, p.end + pad);
              if (x2 - x1 < minSpan) {
                if (x1 === chartMinT) x2 = Math.min(chartMaxT, x1 + minSpan);
                else if (x2 === chartMaxT) x1 = Math.max(chartMinT, x2 - minSpan);
              }
            }
            // display only: the shaded event boxes let clicks and drags through
            // to the chart underneath, so scrubbing works across them (the
            // event buttons below the chart are what you click to open one)
            return (
              <ReferenceArea key={p.key} x1={x1} x2={x2} className="tl-event-area"
                fill={p.color} fillOpacity={0.16} stroke={p.color} strokeOpacity={0.6}
                style={{ pointerEvents: "none" }}
                label={{ value: p.label, position: "insideTop", fill: p.color, fontSize: 11, fontWeight: 600 }} />
            );
          })}
          {metrics.map((m) =>
            filters[m.key] && !unavailable(m, hasIndex) ? (
              <Line key={m.key} type="monotone" dataKey={m.key} stroke={m.color}
                dot={false} strokeWidth={1.6} isAnimationActive={false} />
            ) : null
          )}
          {workloadBands && filters.workload && [workloadBands.medium, workloadBands.high].map((v) => (
            <ReferenceLine key={v} y={wlY(v)} stroke="#f5a623" strokeOpacity={0.45} strokeDasharray="4 4" />
          ))}
          <ReferenceLine x={+curT.toFixed(1)} stroke="#ff5c5c" strokeWidth={1.5} />
        </LineChart>
      </ResponsiveContainer>
      {!readOnly && flightPhases.length > 0 && (
        <>
          <div className="phase-buttons-label">Events:</div>
          <div className="phase-buttons-row">
            {flightPhases.map((p) => {
              const span = chartMaxT - chartMinT;
              const leftPct = span > 0 ? ((p.start - chartMinT) / span) * 100 : 0;
              // MAVIS events tile the flight: draw each across its own start..end
              const widthPct = p.event && span > 0 ? Math.max(0.5, ((p.end - p.start) / span) * 100) : null;
              return (
                <button key={p.key} className={"phase-btn" + (p.event ? " ev" : "")}
                  style={{ left: `${leftPct}%`, ...(widthPct != null ? { width: `calc(${widthPct}% - 2px)` } : {}),
                           borderColor: p.color, color: p.color }}
                  onClick={() => openPhase(p)}
                  title={`${p.label}${p.curriculum ? ` (${p.curriculum})` : ""}: ${p.start.toFixed(1)}s–${p.end.toFixed(1)}s`
                    + (p.grade ? ` · grade ${p.grade}` : "")
                    + (p.outcome && p.outcome !== "completed" ? ` · ${p.outcome}` : "")}>
                  {p.label}
                </button>
              );
            })}
          </div>
        </>
      )}
      {!readOnly && <div className="tl-clock">{curT.toFixed(1)}s / {totalT.toFixed(1)}s</div>}
      {!readOnly && <div className="tl-transport">
        <button className="tl-btn" title="Restart" onClick={onRestart}>↺</button>
        <button className="tl-btn" title="Back 10s" onClick={() => seek(-10)}>« 10s</button>
        <button className="tl-btn play" title="Play / pause" onClick={onTogglePlay}>
          {playing ? "❚❚" : "▶"}
        </button>
        <button className="tl-btn" title="Forward 10s" onClick={() => seek(10)}>10s »</button>
        <select className="tl-speed" title="Speed" value={speed} onChange={(e) => setSpeed(+e.target.value)}>
          <option value={0.5}>0.5×</option>
          <option value={1}>1×</option>
          <option value={2}>2×</option>
          <option value={5}>5×</option>
        </select>
        {live && !followingLive && (
          <button className="tl-btn go-live" title="Jump back to the live edge" onClick={goLive}>● Go Live</button>
        )}
      </div>}
    </div>
  );
}
