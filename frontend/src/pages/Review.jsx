import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine, CartesianGrid,
  PieChart, Pie, Cell,
} from "recharts";
import { api } from "../api/client.js";
import "./review.css";

const AOI_COLORS = {
  OTW: "#14b8a6", Instruments: "#38bdf8",
  airspeed: "#4ade80", altimeter: "#f5a623", attitude: "#a78bfa",
  heading: "#fb7185", throttle: "#facc15",
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

// const SCAN_ROW_CAP = 5; // fixations per scan-path row — row 1 fills before row 2 starts

const VIDEO_SYNC_TOLERANCE = 0.15; // seconds of drift tolerated before re-seeking a paused/scrubbed video
const VIDEO_SYNC_TOLERANCE_PLAYING = 0.75; // looser while playing — natural decode jitter shouldn't trigger a seek every tick

const MIN_AOI_DWELL_SEC_DEFAULT = 0.5; // AOI glances shorter than this are treated as tracking artifacts, not real looks

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

// name of the drawn AOI zone (if any) containing a gaze point, in the
// instrument recording's native pixel space -- see AoiEditor.jsx
function zoneForPoint(zones, x, y) {
  for (const z of zones) {
    const xMin = Math.min(z.x1, z.x2), xMax = Math.max(z.x1, z.x2);
    const yMin = Math.min(z.y1, z.y2), yMax = Math.max(z.y1, z.y2);
    if (x >= xMin && x <= xMax && y >= yMin && y <= yMax) return z.name;
  }
  return null;
}

// refines the broad "Instruments" AOI into a specific dial when the gaze
// point falls inside a drawn zone; any other AOI (OTW, blink, etc.) passes
// through unchanged, since zones only make sense in the panel's own pixel space
function effectiveAoi(e, zones) {
  if (e.aoi === "Instruments" && e.gaze_point_x != null && e.gaze_point_y != null) {
    const zoneName = zoneForPoint(zones, e.gaze_point_x, e.gaze_point_y);
    if (zoneName) return zoneName;
  }
  return e.aoi || "unlabelled";
}

// Keeps one <video> element following the app's cursor-driven clock (the master clock),
// rather than letting the video run free — offsetSec accounts for that recording's start
// not lining up exactly with the flight-data log's first sample.
function useSyncedVideo(ref, src, offsetSec, curT, playing, speed) {
  useEffect(() => {
    const video = ref.current;
    if (!video || !src || Number.isNaN(video.duration)) return;
    const target = Math.max(0, curT + offsetSec);
    const tolerance = playing ? VIDEO_SYNC_TOLERANCE_PLAYING : VIDEO_SYNC_TOLERANCE;
    if (Math.abs(video.currentTime - target) > tolerance) {
      video.currentTime = target;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [curT, src, playing]);

  useEffect(() => {
    const video = ref.current;
    if (!video || !src) return;
    if (playing) video.play().catch(() => {});
    else video.pause();
  }, [playing, src]);

  useEffect(() => {
    const video = ref.current;
    if (video) video.playbackRate = speed;
  }, [speed, src]);
}

export default function Review() {
  const { id } = useParams();
  const nav = useNavigate();
  const [summary, setSummary] = useState(null);
  const [flight, setFlight] = useState([]);
  const [eye, setEye] = useState([]);
  const [events, setEvents] = useState([]);
  const [aoiZones, setAoiZones] = useState([]);
  const [err, setErr] = useState(null);
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [scrubbing, setScrubbing] = useState(false);
  const [speed, setSpeed] = useState(4);
  const [filters, setFilters] = useState({ altitude: true, airspeed: true, vspeed: false, workload: true });
  // kept as raw text (not a number) so a controlled input doesn't fight
  // the user mid-edit -- e.g. typing "0.5" passes through an "0." state
  // that would otherwise get snapped back to "0" on every keystroke
  const [minAoiDwellInput, setMinAoiDwellInput] = useState(String(MIN_AOI_DWELL_SEC_DEFAULT));
  const minAoiDwellSec = Math.max(0, parseFloat(minAoiDwellInput) || 0);
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
    api.listAoiZones().then(setAoiZones).catch(() => {});
  }, [id]);

  const t0 = flight.length ? new Date(flight[0].ts).getTime() : 0;
  const elapsed = (ts) => (new Date(ts).getTime() - t0) / 1000;
  // hoisted above the early returns below: the video-sync effect closes over this
  // and still fires (after commit) even on a render that bails out early with no
  // flight data yet, so it must never be left in the temporal dead zone.
  const curT = flight.length ? elapsed(flight[Math.min(cursor, flight.length - 1)].ts) : 0;
  const totalT = flight.length ? elapsed(flight[flight.length - 1].ts) : 0;

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

  // x-axis ticks every 10s (Recharts' auto ticks land on round numbers like
  // every 100s for a long session, which is too coarse to read the timeline by)
  const timelineTicks = useMemo(() => {
    if (!series.length) return [];
    const maxT = series[series.length - 1].t;
    const ticks = [];
    for (let t = 0; t <= maxT; t += 10) ticks.push(t);
    return ticks;
  }, [series]);

  // Raw AOI transitions (collapse consecutive identical AOIs) -- blinks are
  // excluded rather than shown as "unlabelled": the eye tracker can't
  // resolve gaze position while the eyelid is closed, so those frames
  // aren't a real (if brief) look at nothing, they're just missing data.
  const rawRuns = useMemo(() => {
    const out = [];
    let prev = null;
    for (const e of eye) {
      if (e.blink) continue;
      const a = effectiveAoi(e, aoiZones);
      if (a !== prev) { out.push({ aoi: a, t: elapsed(e.ts) }); prev = a; }
    }
    return out;
  }, [eye, aoiZones]);

  const lastEyeT = eye.length ? elapsed(eye[eye.length - 1].ts) : 0;

  // Merge transitions that don't hold for at least minAoiDwellSec: glasses
  // (or other tracking artifacts) throw brief spurious AOI blips into the
  // gaze stream, so a "look" shorter than this threshold is folded back
  // into whichever AOI it interrupted rather than counted as a real glance.
  const runs = useMemo(() => {
    if (rawRuns.length < 2) return rawRuns;
    let merged = rawRuns.map((r) => ({ ...r }));
    let changed = true;
    while (changed) {
      changed = false;
      for (let i = 1; i < merged.length; i++) {
        const end = i + 1 < merged.length ? merged[i + 1].t : lastEyeT;
        if (end - merged[i].t < minAoiDwellSec) {
          merged[i].aoi = merged[i - 1].aoi;
          changed = true;
        }
      }
      if (changed) {
        const collapsed = [];
        for (const r of merged) {
          if (!collapsed.length || collapsed[collapsed.length - 1].aoi !== r.aoi) collapsed.push(r);
        }
        merged = collapsed;
      }
    }
    return merged;
  }, [rawRuns, minAoiDwellSec, lastEyeT]);

  // region dwell, cumulative up to the current playback position -- built
  // from the cleaned runs above, so blinks and sub-threshold blips are
  // already excluded; dwell is time-weighted rather than sample-counted.
  const regions = useMemo(() => {
    const durations = {};
    for (let i = 0; i < runs.length && runs[i].t <= curT; i++) {
      const end = i + 1 < runs.length ? Math.min(runs[i + 1].t, curT) : curT;
      durations[runs[i].aoi] = (durations[runs[i].aoi] || 0) + Math.max(0, end - runs[i].t);
    }
    const total = Object.values(durations).reduce((a, b) => a + b, 0) || 1;
    return Object.entries(durations)
      .sort((a, b) => b[1] - a[1])
      .map(([name, d], i) => ({ name, value: d, pct: (d / total) * 100, color: aoiColor(name, i) }));
  }, [runs, curT]);

  // ground track: lat/long positions normalized into a square 0-100 viewBox,
  // preserving true relative shape (equal scale on both axes, longitude
  // corrected by cos(latitude) so the path isn't stretched east-west)
  const groundTrack = useMemo(() => {
    const pts = flight
      .map((r) => ({ lat: r.latitude, lon: r.longitude, t: elapsed(r.ts) }))
      .filter((p) => p.lat != null && p.lon != null);
    if (pts.length < 2) return [];

    const lats = pts.map((p) => p.lat);
    const lonScale = Math.cos(((Math.min(...lats) + Math.max(...lats)) / 2) * Math.PI / 180) || 1;
    const xs = pts.map((p) => p.lon * lonScale);
    const ys = pts.map((p) => p.lat);
    const xMin = Math.min(...xs), xMax = Math.max(...xs);
    const yMin = Math.min(...ys), yMax = Math.max(...ys);
    const range = Math.max(xMax - xMin, yMax - yMin) || 1;
    const xOffset = (range - (xMax - xMin)) / 2;
    const yOffset = (range - (yMax - yMin)) / 2;

    const SIZE = 100, PAD = 8, inner = SIZE - PAD * 2;
    return pts.map((p, i) => ({
      x: PAD + ((xs[i] - xMin + xOffset) / range) * inner,
      y: PAD + inner - ((ys[i] - yMin + yOffset) / range) * inner, // north = up
      t: p.t,
    }));
  }, [flight]);

    // Chronological AOI log — one row per completed (or ongoing) run, built
  // once from `runs` with explicit start/end/duration, independent of
  // playback position. Only the "current" flag below depends on curT, so
  // the list itself never reflows while scrubbing — just the highlight
  // moves.
  const scanLog = useMemo(() => {
    const out = [];
    for (let i = 0; i < runs.length; i++) {
      const start = runs[i].t;
      const end = i + 1 < runs.length ? runs[i + 1].t : lastEyeT;
      // order is fixed at chronological position in the whole session —
      // assigned here, before filtering/reversing, so it never shifts as
      // rows enter/leave the visible (curT-filtered) log below.
      out.push({ order: i + 1, aoi: runs[i].aoi, start, end, dur: Math.max(0, end - start) });
    }
    return out;
  }, [runs, lastEyeT]);;

  // Color assigned by first appearance in the session, not by render-time
  // index — so a given AOI's color never shifts as new rows are added.
  const scanLogColor = useMemo(() => {
    const map = new Map();
    let i = 0;
    for (const r of scanLog) {
      if (!map.has(r.aoi)) map.set(r.aoi, aoiColor(r.aoi, i++));
    }
    return map;
  }, [scanLog]);

  // const scanLogRows = useMemo(() => [...scanLog].reverse(), [scanLog]); // newest first
    // Only rows whose glance has actually started by the playhead are shown,
  // newest first. The in-progress row (if curT falls inside it) is clipped
  // to "now" rather than showing its real future end/duration — so
  // rewinding or scrubbing backward makes rows disappear immediately, and
  // nothing reveals what hasn't been "played" yet.
  const scanLogRows = useMemo(() => {
    const rows = [];
    for (let i = scanLog.length - 1; i >= 0; i--) {
      const r = scanLog[i];
      if (r.start > curT) continue;
      const isCurrent = curT < r.end;
      const end = isCurrent ? curT : r.end;
      rows.push({ ...r, end, dur: Math.max(0, end - r.start), isCurrent });
    }
    return rows;
  }, [scanLog, curT]);

  // end a chart drag-scrub even if the mouse is released outside the chart
  useEffect(() => {
    if (!scrubbing) return;
    const stop = () => setScrubbing(false);
    window.addEventListener("mouseup", stop);
    return () => window.removeEventListener("mouseup", stop);
  }, [scrubbing]);

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
  const curAoi = curEye && !curEye.blink ? effectiveAoi(curEye, aoiZones) : null;
  const seek = (deltaSec) => setCursor(nearestIndexForTime(series, curT + deltaSec));

  // clicking/dragging directly on the timeline chart scrubs playback,
  // replacing a separate range-input scrub bar
  const seekToChartEvent = (chartEvent) => {
    if (!chartEvent || chartEvent.activeLabel == null) return;
    setCursor(nearestIndexForTime(series, chartEvent.activeLabel));
  };

  const groundFlownIdx = groundTrack.length ? nearestIndexForTime(groundTrack, curT) : -1;
  const groundFullPath = groundTrack.map((p) => `${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(" ");
  const groundFlownPath = groundFlownIdx >= 0
    ? groundTrack.slice(0, groundFlownIdx + 1).map((p) => `${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(" ")
    : "";
  const groundCurPoint = groundFlownIdx >= 0 ? groundTrack[groundFlownIdx] : null;

  // const visibleRuns = runs.filter((r) => r.t <= curT + 0.05).slice(-(SCAN_ROW_CAP * 2));

  return (
    <div className="rev">
      {/* toolbar rail */}
      <div className="rev-rail">
        <a className="rail-btn" title="Sessions" onClick={() => nav("/sessions")} href="#">‹</a>
        <div className="rail-sep" />
        <a className="rail-btn" title="Analytics" onClick={() => nav(`/sessions/${id}/analytics`)} href="#">▦</a>
        <a className="rail-btn" title="AOI zones" onClick={() => nav("/aoi-zones")} href="#">▢</a>
      </div>

      <div className="rev-main">
        {/* title */}
        <div className="rev-titlebar">
          <h1>{summary.session.name}</h1>
          <span className="sub">{summary.session.aircraft || "aircraft n/a"} · {summary.session.sim_source || "sim n/a"}</span>
          <span className="spacer" />
        </div>

        {/* screens */}
        <div className="rev-screens">
          <div className="rev-screen">
            <div className="head">OTW screen</div>
            <div className="body">
              {otwVideoSrc && (
                <video ref={otwVideoRef} src={otwVideoSrc} className="screen-video"
                  muted playsInline preload="auto" />
              )}
            </div>
          </div>

          <div className="rev-screen">
            <div className="head">Instrument screen<span className="tag">gaze · {curEye ? (curEye.blink ? "blinking" : curAoi) : "no eye data"}</span></div>
            <div className="body">
              {instrumentVideoSrc && (
                <video ref={instrumentVideoRef} src={instrumentVideoSrc} className="screen-video"
                  muted playsInline preload="auto" />
              )}
              {instrumentVideoSrc && aoiZones.length > 0 && (
                <svg viewBox="0 0 1920 1080" preserveAspectRatio="none" className="aoi-overlay-svg">
                  {aoiZones.map((z) => (
                    <rect key={z.id} x={Math.min(z.x1, z.x2)} y={Math.min(z.y1, z.y2)}
                      width={Math.abs(z.x2 - z.x1)} height={Math.abs(z.y2 - z.y1)}
                      fill="none" stroke="#38bdf8" strokeWidth="2.5" strokeDasharray="10 6" opacity="0.55" />
                  ))}
                  {/* gaze-point dot removed for now -- see Review.jsx history to bring it back */}
                </svg>
              )}
            </div>
          </div>

          <div className="rev-screen">
            <div className="head">Ground track</div>
            <div className="body">
              {groundTrack.length > 1 ? (
                <svg viewBox="0 0 100 100" className="ground-track-svg" preserveAspectRatio="xMidYMid meet">
                  <polyline points={groundFullPath} fill="none" stroke="#263341" strokeWidth="1" />
                  <polyline points={groundFlownPath} fill="none" stroke="#38bdf8" strokeWidth="1.5" />
                  {groundCurPoint && (
                    <circle cx={groundCurPoint.x} cy={groundCurPoint.y} r="2.2" fill="#ff5c5c" />
                  )}
                </svg>
              ) : (
                <div className="scan-empty" style={{ padding: 12 }}>No position data.</div>
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
            <LineChart data={series} margin={{ top: 4, right: 8, bottom: 0, left: -28 }}
              style={{ cursor: "pointer" }}
              onMouseDown={(e) => { setScrubbing(true); seekToChartEvent(e); }}
              onMouseMove={(e) => { if (scrubbing) seekToChartEvent(e); }}
              onMouseUp={() => setScrubbing(false)}
            >
              <CartesianGrid stroke="#1b2530" strokeDasharray="3 3" />
              <XAxis dataKey="t" type="number" domain={["dataMin", "dataMax"]} ticks={timelineTicks} interval={0}
                stroke="#5c6f82" tick={{ fontSize: 11 }} unit="s" />
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
          <div className="tl-clock">{curT.toFixed(1)}s / {totalT.toFixed(1)}s</div>
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
            <div className="card-head">
              <h3>Scan path</h3>
              <label className="min-dwell" title="Glances shorter than this are treated as tracking artifacts (e.g. glasses reflections) and folded into the AOI they interrupted">
                min glance
                <input type="number" min={0} step={0.1} value={minAoiDwellInput}
                  onChange={(e) => setMinAoiDwellInput(e.target.value)} />
                s
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
                        onClick={() => setCursor(nearestIndexForTime(series, r.start))}
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