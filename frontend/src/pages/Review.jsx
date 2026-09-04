import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, Link } from "react-router-dom";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine, CartesianGrid,
} from "recharts";
import { api } from "../api/client.js";

const num = (v, d = 1) => (v == null ? "—" : Number(v).toFixed(d));

export default function Review() {
  const { id } = useParams();
  const [summary, setSummary] = useState(null);
  const [flight, setFlight] = useState([]);
  const [eye, setEye] = useState([]);
  const [events, setEvents] = useState([]);
  const [err, setErr] = useState(null);
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(4);
  const timer = useRef(null);

  useEffect(() => {
    (async () => {
      try {
        const [sm, fl, ey, ev] = await Promise.all([
          api.summary(id), api.flight(id), api.eye(id), api.events(id),
        ]);
        setSummary(sm);
        setFlight(fl);
        setEye(ey);
        setEvents(ev);
      } catch (e) {
        setErr(e.message);
      }
    })();
  }, [id]);

  const t0 = flight.length ? new Date(flight[0].ts).getTime() : 0;
  const elapsed = (ts) => (new Date(ts).getTime() - t0) / 1000;

  // chart series (elapsed seconds on X)
  const series = useMemo(
    () =>
      flight.map((r) => ({
        t: +elapsed(r.ts).toFixed(2),
        altitude_ft: r.altitude_ft,
        airspeed_kt: r.airspeed_kt,
        bank_deg: r.bank_deg,
      })),
    [flight]
  );

  // map each flight index to nearest eye index (two-pointer, computed once)
  const eyeForFlight = useMemo(() => {
    const map = new Array(flight.length).fill(-1);
    let j = 0;
    for (let i = 0; i < flight.length; i++) {
      const ft = new Date(flight[i].ts).getTime();
      while (
        j + 1 < eye.length &&
        Math.abs(new Date(eye[j + 1].ts).getTime() - ft) <=
          Math.abs(new Date(eye[j].ts).getTime() - ft)
      )
        j++;
      map[i] = eye.length ? j : -1;
    }
    return map;
  }, [flight, eye]);

  // gaze normalization bounds
  const gazeBounds = useMemo(() => {
    const xs = eye.map((e) => e.gaze_point_x).filter((v) => v != null);
    const ys = eye.map((e) => e.gaze_point_y).filter((v) => v != null);
    return {
      xmin: Math.min(...xs), xmax: Math.max(...xs),
      ymin: Math.min(...ys), ymax: Math.max(...ys),
    };
  }, [eye]);

  // lat/long path bounds
  const geoBounds = useMemo(() => {
    const la = flight.map((r) => r.latitude).filter((v) => v != null);
    const lo = flight.map((r) => r.longitude).filter((v) => v != null);
    return {
      lamin: Math.min(...la), lamax: Math.max(...la),
      lomin: Math.min(...lo), lomax: Math.max(...lo),
    };
  }, [flight]);

  // playback loop
  useEffect(() => {
    if (playing && flight.length) {
      timer.current = setInterval(() => {
        setCursor((c) => {
          if (c >= flight.length - 1) return 0;
          return Math.min(flight.length - 1, c + speed);
        });
      }, 100);
    }
    return () => clearInterval(timer.current);
  }, [playing, speed, flight.length]);

  if (err) return <div className="card err">Error: {err}</div>;
  if (!summary) return <div className="card muted">Loading…</div>;
  if (!flight.length)
    return (
      <div className="card">
        <p className="muted">No flight data in this session.</p>
        <Link to="/upload">Upload data →</Link>
      </div>
    );

  const cur = flight[cursor];
  const curEye = eyeForFlight[cursor] >= 0 ? eye[eyeForFlight[cursor]] : null;
  const curT = elapsed(cur.ts);

  const gx =
    curEye && curEye.gaze_point_x != null && gazeBounds.xmax > gazeBounds.xmin
      ? ((curEye.gaze_point_x - gazeBounds.xmin) / (gazeBounds.xmax - gazeBounds.xmin)) * 100
      : 50;
  const gy =
    curEye && curEye.gaze_point_y != null && gazeBounds.ymax > gazeBounds.ymin
      ? ((curEye.gaze_point_y - gazeBounds.ymin) / (gazeBounds.ymax - gazeBounds.ymin)) * 100
      : 50;

  const geoW = 260, geoH = 160, pad = 12;
  const projX = (lo) =>
    geoBounds.lomax > geoBounds.lomin
      ? pad + ((lo - geoBounds.lomin) / (geoBounds.lomax - geoBounds.lomin)) * (geoW - 2 * pad)
      : geoW / 2;
  const projY = (la) =>
    geoBounds.lamax > geoBounds.lamin
      ? geoH - pad - ((la - geoBounds.lamin) / (geoBounds.lamax - geoBounds.lamin)) * (geoH - 2 * pad)
      : geoH / 2;
  const pathPoints = flight
    .filter((r) => r.latitude != null && r.longitude != null)
    .map((r) => `${projX(r.longitude).toFixed(1)},${projY(r.latitude).toFixed(1)}`)
    .join(" ");

  return (
    <>
      <div className="card">
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <h2 style={{ margin: 0, marginRight: "auto" }}>
            {summary.session.name}{" "}
            <span className="muted" style={{ fontSize: 14 }}>
              · {summary.session.aircraft || "?"} · {summary.session.sim_source || "?"}
            </span>
          </h2>
          <Link to={`/sessions/${id}/analytics`}>Analytics →</Link>
        </div>

        {/* transport controls */}
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 14 }}>
          <button onClick={() => setPlaying((p) => !p)}>{playing ? "⏸ Pause" : "▶ Play"}</button>
          <input
            type="range"
            min={0}
            max={flight.length - 1}
            value={cursor}
            onChange={(e) => setCursor(+e.target.value)}
            style={{ flex: 1 }}
          />
          <span className="pill">t = {curT.toFixed(1)}s</span>
          <select value={speed} onChange={(e) => setSpeed(+e.target.value)}>
            <option value={1}>1×</option>
            <option value={4}>4×</option>
            <option value={10}>10×</option>
          </select>
        </div>
      </div>

      <div className="row">
        {/* live readout */}
        <div className="col card">
          <h3>Flight state</h3>
          <div className="readout">
            <Metric k="Altitude (ft)" v={num(cur.altitude_ft)} />
            <Metric k="Airspeed (kt)" v={num(cur.airspeed_kt)} />
            <Metric k="V/S (fpm)" v={num(cur.vertical_speed_fpm, 0)} />
            <Metric k="Heading (°)" v={num(cur.heading_true_deg, 0)} />
            <Metric k="Pitch (°)" v={num(cur.pitch_deg)} />
            <Metric k="Bank (°)" v={num(cur.bank_deg)} />
            <Metric k="Throttle (%)" v={num(cur.throttle_pct, 0)} />
            <Metric k="Gear" v={cur.gear_handle_position ? "DOWN" : "UP"} />
          </div>
        </div>

        {/* gaze + path */}
        <div className="col card">
          <h3>Gaze {curEye ? "" : <span className="muted">(no eye data)</span>}</h3>
          <div className="gaze-box" style={{ width: "100%", height: 160 }}>
            <div className="gaze-dot" style={{ left: `${gx}%`, top: `${gy}%` }} />
          </div>
          <div className="readout" style={{ marginTop: 10 }}>
            <Metric k="AOI" v={curEye?.aoi || "—"} />
            <Metric k="Pupil L (mm)" v={num(curEye?.pupil_diam_left_mm, 2)} />
            <Metric k="Quality" v={num(curEye?.gaze_quality, 2)} />
            <Metric k="Blink" v={curEye?.blink ? "yes" : "no"} />
          </div>
        </div>
      </div>

      <div className="row">
        <div className="col card">
          <h3>Ground track</h3>
          <svg width={geoW} height={geoH} style={{ background: "#06101a", borderRadius: 8 }}>
            <polyline points={pathPoints} fill="none" stroke="#2b5c85" strokeWidth="1.5" />
            <circle cx={projX(cur.longitude)} cy={projY(cur.latitude)} r="5" fill="#4aa8ff" />
          </svg>
        </div>
        <div className="col card">
          <h3>Altitude</h3>
          <TimeChart data={series} dataKey="altitude_ft" color="#4aa8ff" t={curT} events={events} t0={t0} />
        </div>
      </div>

      <div className="card">
        <h3>Airspeed</h3>
        <TimeChart data={series} dataKey="airspeed_kt" color="#3fb950" t={curT} events={events} t0={t0} height={180} />
      </div>
    </>
  );
}

function Metric({ k, v }) {
  return (
    <div className="metric">
      <div className="k">{k}</div>
      <div className="v">{v}</div>
    </div>
  );
}

function TimeChart({ data, dataKey, color, t, events, t0, height = 200 }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 5, right: 10, bottom: 5, left: -10 }}>
        <CartesianGrid stroke="#2d3a48" strokeDasharray="3 3" />
        <XAxis dataKey="t" stroke="#8b98a5" tick={{ fontSize: 11 }} unit="s" />
        <YAxis stroke="#8b98a5" tick={{ fontSize: 11 }} domain={["auto", "auto"]} />
        <Tooltip contentStyle={{ background: "#1a222c", border: "1px solid #2d3a48" }} />
        <Line type="monotone" dataKey={dataKey} stroke={color} dot={false} isAnimationActive={false} />
        <ReferenceLine x={+t.toFixed(2)} stroke="#ff5c5c" />
        {events.map((e, i) => (
          <ReferenceLine
            key={i}
            x={+((new Date(e.ts).getTime() - t0) / 1000).toFixed(2)}
            stroke="#e3b341"
            strokeDasharray="2 2"
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
