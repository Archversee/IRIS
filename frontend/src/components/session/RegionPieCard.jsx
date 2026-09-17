import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from "recharts";

function PieTip({ active, payload }) {
  if (!active || !payload || !payload.length) return null;
  const d = payload[0].payload;
  return (
    <div style={{ background: "#0f1a24", border: "1px solid #263341", borderRadius: 6, padding: "6px 10px", fontSize: 12 }}>
      {d.name}: {d.pct.toFixed(1)}%
    </div>
  );
}

// Cumulative percentage of time spent looking at each AOI, up to the
// current playback position.
export default function RegionPieCard({ regions }) {
  return (
    <div className="rev-card">
      <h3>Region viewing %</h3>
      {regions.length === 0 ? (
        <div className="scan-empty">No eye-tracking data.</div>
      ) : (
        <>
          <ResponsiveContainer width="100%" height={150}>
            <PieChart>
              <Pie data={regions} dataKey="value" nameKey="name" cx="50%" cy="50%"
                innerRadius={30} outerRadius={62} paddingAngle={2} stroke="none">
                {regions.map((r, i) => <Cell key={i} fill={r.color} />)}
              </Pie>
              <Tooltip content={<PieTip />} />
            </PieChart>
          </ResponsiveContainer>
          <div className="pie-legend">
            {regions.map((r, i) => (
              <div className="lg" key={i}>
                <span className="dot" style={{ background: r.color }} />
                {r.name}<span className="pct">{r.pct.toFixed(0)}%</span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
