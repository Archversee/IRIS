import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine, CartesianGrid,
  PieChart, Pie, Cell,
} from "recharts";
import { api } from "../api/client.js";
import "./review.css";

const AOI_COLORS = {
  airspeed: "#38bdf8", altimeter: "#4ade80", attitude: "#f5a623",
  heading: "#a78bfa", outside: "#14b8a6", throttle: "#fb7185",
  unlabelled: "#64748b",
};
const PALETTE = ["#38bdf8", "#4ade80", "#f5a623", "#a78bfa", "#14b8a6", "#fb7185", "#facc15", "#60a5fa"];
function aoiColor(name, i = 0) {
  return AOI_COLORS[name] || PALETTE[i % PALETTE.length];
}

const METRICS = [
  { key: "altitude", label: "Altitude", unit: "ft", color: "#38bdf8", src: "altitude_ft" },
  { key: "airspeed", label: "Airspeed", unit: "kt", color: "#4ade80", src: "airspeed_kt" },
  { key: "vspeed", label: "Vert speed", unit: "fpm", color: "#a78bfa", src: "vertical_speed_fpm" },
  { key: "workload", label: "Workload", unit: "", color: "#f5a623", src: "workload" },
];

const SCAN_ROW_CAP = 5; // fixations per scan-path row — row 1 fills before row 2 starts

const VIDEO_SYNC_TOLERANCE = 0.15; // seconds of drift tolerated before re-seeking a video

const fmt = (v, d = 1) => (v == null || Number.isNaN(v) ? "—" : Number(v).toFixed(d));

function bounds(arr, key) {
  let mn = Infinity, mx = -Infinity;
  for (const r of arr) {
    const v = r[key];
    if (v == null || Number.isNaN(v)) continue;
    if (v < mn) mn = v;
    if (v > mx) mx = v;
  }
  return [mn, mx];
}
const norm = (v, [mn, mx]) => (v == null || mx <= mn ? null : (v - mn) / (mx - mn));

// nearest index in a { t } array sorted ascending by time
function nearestIndexForTime(arr, t) {
  let lo = 0, hi = arr.length - 1;
  if (hi < 0) return 0;
  if (t <= arr[0].t) return 0;
  if (t >= arr[hi].t) return hi;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (arr[mid].t < t) lo = mid + 1; else hi = mid;
  }
  if (lo > 0 && Math.abs(arr[lo - 1].t - t) <= Math.abs(arr[lo].t - t)) return lo - 1;
  return lo;
}

// Keeps one <video> element following the app's cursor-driven clock (the master clock),
// rather than letting the video run free — offsetSec accounts for that recording's start
// not lining up exactly with the flight-data log's first sample.
function useSyncedVideo(ref, src, offsetSec, curT, playing, speed) {
  useEffect(() => {
    const video = ref.current;
    if (!video || !src || Number.isNaN(video.duration)) return;
    const target = Math.max(0, curT + offsetSec);
    if (Math.abs(video.currentTime - target) > VIDEO_SYNC_TOLERANCE) {
      video.currentTime = target;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [curT, src]);

  useEffect(() => {
    const video = ref.current;
    if (!video || !src) return;
    if (playing) video.play().catch(() => {});
    else video.pause();
  }, [playing, src]);

  useEffect(() => {
    const video = ref.current;
    if (video) video.playbackRate = speed;
  }, [speed]);
}

export default function Review() {
  const { id } = useParams();
  const nav = useNavigate();
  const [summary, setSummary] = useState(null);
  const [flight, setFlight] = useState([]);
  const [eye, setEye] = useState([]);
  const [events, setEvents] = useState([]);
  const [err, setErr] = useState(null);
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(4);
  const [filters, setFilters] = useState({ altitude: true, airspeed: true, vspeed: false, workload: true });
  const timer = useRef(null);
  const instrumentVideoRef = useRef(null);
  const otwVideoRef = useRef(null);

  useEffect(() => {
    (async () => {
      try {
        const [sm, fl, ey, ev] = await Promise.all([
          api.summary(id), api.flight(id), api.eye(id), api.events(id),
        ]);
        setSummary(sm); setFlight(fl); setEye(ey); setEvents(ev);
      } catch (e) { setErr(e.message); }
    })();
  }, [id]);

  const t0 = flight.length ? new Date(flight[0].ts).getTime() : 0;
  const elapsed = (ts) => (new Date(ts).getTime() - t0) / 1000;
  // hoisted above the early returns below: the video-sync effect closes over this
  // and still fires (after commit) even on a render that bails out early with no
  // flight data yet, so it must never be left in the temporal dead zone.
  const curT = flight.length ? elapsed(flight[Math.min(cursor, flight.length - 1)].ts) : 0;

  // nearest eye sample for each flight sample (two-pointer, once)
  const eyeForFlight = useMemo(() => {
    const map = new Array(flight.length).fill(-1);
    let j = 0;
    for (let i = 0; i < flight.length; i++) {
      const ft = new Date(flight[i].ts).getTime();
      while (j + 1 < eye.length &&
        Math.abs(new Date(eye[j + 1].ts).getTime() - ft) <= Math.abs(new Date(eye[j].ts).getTime() - ft))
        j++;
      map[i] = eye.length ? j : -1;
    }
    return map;
  }, [flight, eye]);

  // workload proxy = mean pupil diameter at the aligned eye sample,
  // smoothed with a centred moving average (raw pupil is too jittery to read).
  const workloadRaw = useMemo(() => {
    const raw = flight.map((_, i) => {
      const e = eyeForFlight[i] >= 0 ? eye[eyeForFlight[i]] : null;
      if (!e) return null;
      const vals = [e.pupil_diam_left_mm, e.pupil_diam_right_mm].filter((v) => v != null);
      return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
    });
    const W = 15; // ~1.5s window at 10 Hz
    return raw.map((_, i) => {
      let sum = 0, n = 0;
      for (let k = Math.max(0, i - W); k <= Math.min(raw.length - 1, i + W); k++) {
        if (raw[k] != null) { sum += raw[k]; n++; }
      }
      return n ? sum / n : null;
    });
  }, [flight, eye, eyeForFlight]);
  const hasEye = eye.length > 0;

  // one independent recording per screen, each with its own sync offset (set via the video-link API)
  const instrumentVideoSrc = summary?.session?.instrument_video_url || null;
  const instrumentOffsetSec = summary?.session?.instrument_video_offset_sec ?? 0;
  const otwVideoSrc = summary?.session?.otw_video_url || null;
  const otwOffsetSec = summary?.session?.otw_video_offset_sec ?? 0;

  // normalized timeline series (shapes comparable across metrics)
  const series = useMemo(() => {
    if (!flight.length) return [];
    const b = {
      altitude: bounds(flight, "altitude_ft"),
      airspeed: bounds(flight, "airspeed_kt"),
      vspeed: bounds(flight, "vertical_speed_fpm"),
      workload: [Math.min(...workloadRaw.filter((v) => v != null)), Math.max(...workloadRaw.filter((v) => v != null))],
    };
    return flight.map((r, i) => ({
      t: +elapsed(r.ts).toFixed(1),
      altitude: norm(r.altitude_ft, b.altitude), altitudeR: r.altitude_ft,
      airspeed: norm(r.airspeed_kt, b.airspeed), airspeedR: r.airspeed_kt,
      vspeed: norm(r.vertical_speed_fpm, b.vspeed), vspeedR: r.vertical_speed_fpm,
      workload: norm(workloadRaw[i], b.workload), workloadR: workloadRaw[i],
    }));
  }, [flight, workloadRaw]);

  // AOI scan-path runs (collapse consecutive identical AOIs)
  const runs = useMemo(() => {
    const out = [];
    let prev = null;
    for (const e of eye) {
      const a = e.aoi || "unlabelled";
      if (a !== prev) { out.push({ aoi: a, t: elapsed(e.ts) }); prev = a; }
    }
    return out;
  }, [eye]);

  // region dwell (whole session)
  const regions = useMemo(() => {
    const counts = {};
    for (const e of eye) { const a = e.aoi || "unlabelled"; counts[a] = (counts[a] || 0) + 1; }
    const total = Object.values(counts).reduce((a, b) => a + b, 0) || 1;
    return Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .map(([name, n], i) => ({ name, value: n, pct: (n / total) * 100, color: aoiColor(name, i) }));
  }, [eye]);

  // playback loop
  useEffect(() => {
    if (playing && flight.length) {
      timer.current = setInterval(() => {
        setCursor((c) => (c >= flight.length - 1 ? 0 : Math.min(flight.length - 1, c + speed)));
      }, 100);
    }
    return () => clearInterval(timer.current);
  }, [playing, speed, flight.length]);

  // each screen's recording follows the app's cursor-driven clock independently
  useSyncedVideo(instrumentVideoRef, instrumentVideoSrc, instrumentOffsetSec, curT, playing, speed);
  useSyncedVideo(otwVideoRef, otwVideoSrc, otwOffsetSec, curT, playing, speed);

  if (err) return <div className="rev-error">Couldn't load this session.<br />{err}</div>;
  if (!summary) return <div className="rev-loading">Loading session…</div>;
  if (!flight.length)
    return (
      <div className="rev-error">
        No flight data in this session yet.<br />
        <button className="rail-btn" style={{ width: "auto", padding: "6px 14px", marginTop: 12 }}
          onClick={() => nav("/upload")}>Upload data</button>
      </div>
    );

  const cur = flight[cursor];
  const curEyeIdx = eyeForFlight[cursor];
  const curEye = curEyeIdx >= 0 ? eye[curEyeIdx] : null;
  const seek = (deltaSec) => setCursor(nearestIndexForTime(series, curT + deltaSec));

  const visibleRuns = runs.filter((r) => r.t <= curT + 0.05).slice(-(SCAN_ROW_CAP * 2));

  return (
    <div className="rev">
      {/* toolbar rail */}
      <div className="rev-rail">
        <a className="rail-btn" title="Sessions" onClick={() => nav("/sessions")} href="#">‹</a>
        <div className="rail-sep" />
        <a className="rail-btn" title="Analytics" onClick={() => nav(`/sessions/${id}/analytics`)} href="#">▦</a>
      </div>

      <div className="rev-main">
        {/* title */}
        <div className="rev-titlebar">
          <h1>{summary.session.name}</h1>
          <span className="sub">{summary.session.aircraft || "aircraft n/a"} · {summary.session.sim_source || "sim n/a"}</span>
          <span className="spacer" />
          <span className="clock">T + {curT.toFixed(1)}s</span>
        </div>

        {/* screens */}
        <div className="rev-screens">
          <div className="rev-screen">
            <div className="head">Instrument screen<span className="tag">gaze · {curEye?.aoi || "no eye data"}</span></div>
            <div className="body">
              {instrumentVideoSrc && (
                <video ref={instrumentVideoRef} src={instrumentVideoSrc} className="screen-video"
                  muted playsInline preload="auto" />
              )}
            </div>
          </div>

          <div className="rev-screen">
            <div className="head">OTW screen</div>
            <div className="body">
              {otwVideoSrc && (
                <video ref={otwVideoRef} src={otwVideoSrc} className="screen-video"
                  muted playsInline preload="auto" />
              )}
            </div>
          </div>
        </div>

        {/* timeline */}
        <div className="rev-timeline">
          <div className="tl-head">
            <h2>Session timeline</h2>
            <div className="filters">
              {METRICS.map((m) => {
                const disabled = m.key === "workload" && !hasEye;
                return (
                  <button key={m.key} disabled={disabled}
                    className={"filter-chip" + (filters[m.key] && !disabled ? " on" : "")}
                    onClick={() => setFilters((f) => ({ ...f, [m.key]: !f[m.key] }))}>
                    <span className="swatch" style={{ background: m.color }} />{m.label}
                  </button>
                );
              })}
            </div>
          </div>
          <ResponsiveContainer width="100%" height={150}>
            <LineChart data={series} margin={{ top: 4, right: 8, bottom: 0, left: -28 }}>
              <CartesianGrid stroke="#1b2530" strokeDasharray="3 3" />
              <XAxis dataKey="t" stroke="#5c6f82" tick={{ fontSize: 11 }} unit="s" />
              <YAxis stroke="#5c6f82" tick={false} domain={[0, 1]} width={30} />
              <Tooltip content={<TLTooltip />} />
              {METRICS.map((m) =>
                filters[m.key] && !(m.key === "workload" && !hasEye) ? (
                  <Line key={m.key} type="monotone" dataKey={m.key} stroke={m.color}
                    dot={false} strokeWidth={1.6} isAnimationActive={false} />
                ) : null
              )}
              <ReferenceLine x={+curT.toFixed(1)} stroke="#ff5c5c" strokeWidth={1.5} />
              {events.map((e, i) => (
                <ReferenceLine key={i} x={+elapsed(e.ts).toFixed(1)} stroke="#f5a623" strokeDasharray="2 3"
                  label={{ value: e.label, position: "top", fill: "#f5a623", fontSize: 10 }} />
              ))}
            </LineChart>
          </ResponsiveContainer>
          <div className="rev-scrub">
            <input type="range" min={0} max={flight.length - 1} value={cursor}
              onChange={(e) => setCursor(+e.target.value)} />
          </div>
          <div className="tl-transport">
            <button className="tl-btn" title="Restart" onClick={() => { setCursor(0); setPlaying(false); }}>↺</button>
            <button className="tl-btn" title="Back 10s" onClick={() => seek(-10)}>« 10s</button>
            <button className="tl-btn play" title="Play / pause" onClick={() => setPlaying((p) => !p)}>
              {playing ? "❚❚" : "▶"}
            </button>
            <button className="tl-btn" title="Forward 10s" onClick={() => seek(10)}>10s »</button>
            <select className="tl-speed" title="Speed" value={speed} onChange={(e) => setSpeed(+e.target.value)}>
              <option value={1}>1×</option>
              <option value={4}>4×</option>
              <option value={10}>10×</option>
            </select>
          </div>
        </div>

        {/* bottom row */}
        <div className="rev-bottom">
          {/* scan path */}
          <div className="rev-card">
            <h3>Instrument scan path</h3>
            {visibleRuns.length === 0 ? (
              <div className="scan-empty">No gaze fixations yet at this point in the flight.</div>
            ) : (
              <div className="scan-flow">
                {[visibleRuns.slice(0, SCAN_ROW_CAP), visibleRuns.slice(SCAN_ROW_CAP)]
                  .filter((row) => row.length)
                  .map((row, ri) => (
                    <div className="scan-row" key={ri}>
                      {row.map((r, i) => (
                        <span key={i} style={{ display: "contents" }}>
                          <span className="scan-node" style={{ borderColor: aoiColor(r.aoi), color: aoiColor(r.aoi) }}>
                            {r.aoi}<span className="t">{r.t.toFixed(1)}s</span>
                          </span>
                          {i < row.length - 1 && <span className="scan-arrow">→</span>}
                        </span>
                      ))}
                    </div>
                  ))}
              </div>
            )}
          </div>

          {/* readouts */}
          <div className="rev-card">
            <h3>Live state</h3>
            <div className="readout-cols">
              <div className="ro-group">
                <h4>Flight state</h4>
                <RO k="Heading" v={`${fmt(cur.heading_true_deg, 0)}°`} />
                <RO k="Pitch" v={`${fmt(cur.pitch_deg)}°`} />
                <RO k="Bank" v={`${fmt(cur.bank_deg)}°`} />
                <RO k="Yaw rate" v={fmt(cur.yaw_rate_radps, 3)} />
                <RO k="Fuel" v={`${fmt(cur.fuel_total_qty_gal)} gal`} />
                <RO k="Lat" v={fmt(cur.latitude, 4)} />
                <RO k="Long" v={fmt(cur.longitude, 4)} />
                <RO k="Wind" v={`${fmt(cur.ambient_wind_kt)} kt`} />
              </div>
              <div className="ro-group">
                <h4>Control input</h4>
                <RO k="Throttle" v={`${fmt(cur.throttle_pct, 0)}%`} />
                <RO k="Elev trim" v={`${fmt(cur.elevator_trim_pct)}%`} />
                <RO k="Elevator" v={fmt(cur.elevator_position, 2)} />
                <RO k="Rudder" v={fmt(cur.rudder_position, 2)} />
                <RO k="Flaps" v={`${fmt(cur.flaps_handle_pct, 0)}%`} />
                <RO k="Gear" v={cur.gear_handle_position ? "DOWN" : "UP"} />
                <RO k="Altitude" v={`${fmt(cur.altitude_ft, 0)} ft`} />
                <RO k="Airspeed" v={`${fmt(cur.airspeed_kt)} kt`} />
              </div>
            </div>
          </div>

          {/* region viewing */}
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
        </div>
      </div>
    </div>
  );
}

function RO({ k, v }) {
  return <div className="ro-row"><span className="k">{k}</span><span className="v">{v}</span></div>;
}

function TLTooltip({ active, payload }) {
  if (!active || !payload || !payload.length) return null;
  const d = payload[0].payload;
  const rows = [
    ["Altitude", fmt(d.altitudeR, 0), "ft"],
    ["Airspeed", fmt(d.airspeedR, 1), "kt"],
    ["Vert speed", fmt(d.vspeedR, 0), "fpm"],
    ["Workload", d.workloadR == null ? "—" : fmt(d.workloadR, 2), "mm pupil"],
  ];
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

function PieTip({ active, payload }) {
  if (!active || !payload || !payload.length) return null;
  const d = payload[0].payload;
  return (
    <div style={{ background: "#0f1a24", border: "1px solid #263341", borderRadius: 6, padding: "6px 10px", fontSize: 12 }}>
      {d.name}: {d.pct.toFixed(1)}%
    </div>
  );
}