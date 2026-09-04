import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from "recharts";
import { api } from "../api/client.js";

const num = (v, d = 2) => (v == null ? "—" : Number(v).toFixed(d));
const pct = (v) => (v == null ? "—" : (Number(v) * 100).toFixed(1) + "%");

export default function Analytics() {
  const { id } = useParams();
  const [a, setA] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    api.analytics(id).then(setA).catch((e) => setErr(e.message));
  }, [id]);

  if (err) return <div className="card err">Error: {err}</div>;
  if (!a) return <div className="card muted">Loading…</div>;

  const f = a.flight || {};
  const e = a.eye || {};
  // assume ~10 Hz sampling to turn AOI sample counts into seconds
  const dwell = (a.aoi_dwell || []).map((d) => ({ aoi: d.aoi, seconds: +(d.samples / 10).toFixed(1) }));

  return (
    <>
      <div className="card" style={{ display: "flex", alignItems: "center" }}>
        <h2 style={{ margin: 0, marginRight: "auto" }}>Analytics</h2>
        <Link to={`/sessions/${id}/review`}>← Back to review</Link>
      </div>

      <div className="card">
        <h3>Flight performance</h3>
        <div className="readout">
          <Metric k="Samples" v={f.samples ?? "—"} />
          <Metric k="Avg airspeed (kt)" v={num(f.avg_airspeed_kt, 1)} />
          <Metric k="Max altitude (ft)" v={num(f.max_altitude_ft, 0)} />
          <Metric k="Elevator activity (σ)" v={num(f.elevator_activity, 3)} title="Std-dev of elevator position — lower = smoother pitch control" />
          <Metric k="Rudder activity (σ)" v={num(f.rudder_activity, 3)} />
          <Metric k="Mean |yaw rate|" v={num(f.mean_abs_yaw_rate, 3)} title="Coordination proxy" />
          <Metric k="Low-airspeed time" v={pct(f.low_airspeed_fraction)} title="Fraction of samples under 55 kt — stall-risk proxy" />
          <Metric k="Steep-bank time" v={pct(f.steep_bank_fraction)} title="Fraction of samples with |bank| > 45°" />
        </div>
      </div>

      <div className="card">
        <h3>Eye tracking</h3>
        <div className="readout">
          <Metric k="Samples" v={e.samples ?? "—"} />
          <Metric k="Avg gaze quality" v={num(e.avg_quality, 2)} />
          <Metric k="Blink fraction" v={pct(e.blink_fraction)} />
          <Metric k="Avg pupil L (mm)" v={num(e.avg_pupil_left_mm, 2)} />
        </div>
      </div>

      <div className="card">
        <h3>Gaze dwell by area of interest (s)</h3>
        {dwell.length === 0 ? (
          <p className="muted">No eye-tracking data.</p>
        ) : (
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={dwell} margin={{ top: 5, right: 10, bottom: 5, left: -10 }}>
              <CartesianGrid stroke="#2d3a48" strokeDasharray="3 3" />
              <XAxis dataKey="aoi" stroke="#8b98a5" tick={{ fontSize: 12 }} />
              <YAxis stroke="#8b98a5" tick={{ fontSize: 11 }} unit="s" />
              <Tooltip contentStyle={{ background: "#1a222c", border: "1px solid #2d3a48" }} />
              <Bar dataKey="seconds" fill="#4aa8ff" />
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
    </>
  );
}

function Metric({ k, v, title }) {
  return (
    <div className="metric" title={title || ""}>
      <div className="k">{k}</div>
      <div className="v">{v}</div>
    </div>
  );
}
