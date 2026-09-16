import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine, ReferenceArea, CartesianGrid,
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

const VIDEO_SYNC_TOLERANCE = 0.15; // seconds of drift tolerated before re-seeking a paused/scrubbed video
const VIDEO_SYNC_TOLERANCE_PLAYING = 0.75; // looser while playing 
const MIN_AOI_DWELL_SEC_DEFAULT = 0.1; // AOI glances shorter than this are treated as tracking artifacts, not real looks
const CHART_MAX_POINTS = 2000; // thinning the timeline chart for display

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

// linear interpolation between the two eye samples bracketing t, for a smooth sub-sample gaze position 
function interpolatedGaze(eyeArr, timeline, t) {
  if (!eyeArr.length) return null;
  const i = nearestIndexForTime(timeline, t);
  let j = i;
  if (timeline[i].t < t && i + 1 < eyeArr.length) j = i + 1;
  else if (timeline[i].t > t && i - 1 >= 0) j = i - 1;

  const a = eyeArr[i], b = eyeArr[j];
  if (i === j || a.blink || b.blink || a.aoi !== b.aoi
      || a.gaze_point_x == null || b.gaze_point_x == null) {
    return { x: a.gaze_point_x, y: a.gaze_point_y };
  }
  const lo = timeline[i].t <= timeline[j].t ? a : b;
  const hi = timeline[i].t <= timeline[j].t ? b : a;
  const ta = Math.min(timeline[i].t, timeline[j].t);
  const tb = Math.max(timeline[i].t, timeline[j].t);
  const frac = tb > ta ? Math.min(1, Math.max(0, (t - ta) / (tb - ta))) : 0;
  return {
    x: lo.gaze_point_x + (hi.gaze_point_x - lo.gaze_point_x) * frac,
    y: lo.gaze_point_y + (hi.gaze_point_y - lo.gaze_point_y) * frac,
  };
}

// name of the drawn AOI zone containing a gaze point, in the
// instrument recording's native pixel space -- see AoiEditor.jsx
function zoneForPoint(zones, x, y) {
  for (const z of zones) {
    const xMin = Math.min(z.x1, z.x2), xMax = Math.max(z.x1, z.x2);
    const yMin = Math.min(z.y1, z.y2), yMax = Math.max(z.y1, z.y2);
    if (x >= xMin && x <= xMax && y >= yMin && y <= yMax) return z.name;
  }
  return null;
}

// refines the broad "Instruments" AOI into a specific dial when the gaze point falls inside a drawn zone
function effectiveAoi(e, zones) {
  if (e.aoi === "Instruments" && e.gaze_point_x != null && e.gaze_point_y != null) {
    const zoneName = zoneForPoint(zones, e.gaze_point_x, e.gaze_point_y);
    if (zoneName) return zoneName;
  }
  return e.aoi || "unlabelled";
}

// Keep one <video> element following the app's cursor-driven clock (the master clock),
function useSyncedVideo(ref, src, offsetSec, curT, playing, speed) {
  useEffect(() => {
    const video = ref.current;
    if (!video || !src || Number.isNaN(video.duration)) return;
    const target = Math.max(0, curT + offsetSec);
    const tolerance = playing ? VIDEO_SYNC_TOLERANCE_PLAYING : VIDEO_SYNC_TOLERANCE;
    if (Math.abs(video.currentTime - target) > tolerance) {
      video.currentTime = target;
    }
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

// Eye Gaze Animations
function useGazeStretch(gaze, curT) {
  const prevRef = useRef(null); // { x, y, t }
  const [stretch, setStretch] = useState({ angleDeg: 0, factor: 1 });

  useEffect(() => {
    if (!gaze || gaze.x == null) return;
    const prev = prevRef.current;
    prevRef.current = { x: gaze.x, y: gaze.y, t: curT };
    if (!prev) return;
    const dt = curT - prev.t;
    if (dt <= 0) return;
    const dx = gaze.x - prev.x, dy = gaze.y - prev.y;
    const dist = Math.hypot(dx, dy);
    const speed = dist / dt; // px/sec in the 1920x1080 native space
    const factor = 1 + Math.min(1.8, speed / 4000); // cap how far it can stretch
    const angleDeg = dist > 0.5 ? Math.atan2(dy, dx) * (180 / Math.PI) : 0;
    setStretch({ angleDeg, factor });
  }, [gaze?.x, gaze?.y, curT]);

  return stretch;
}

export default function Review() {
  const { id } = useParams();
  const nav = useNavigate();
  const [summary, setSummary] = useState(null);
  const [flight, setFlight] = useState([]);
  const [eye, setEye] = useState([]);
  const [aoiZones, setAoiZones] = useState([]);
  const [err, setErr] = useState(null);
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [scrubbing, setScrubbing] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [filters, setFilters] = useState({ altitude: true, airspeed: true, vspeed: false, workload: true });
  const [minAoiDwellInput, setMinAoiDwellInput] = useState(String(MIN_AOI_DWELL_SEC_DEFAULT));
  const [selectedPhase, setSelectedPhase] = useState(null);
  const [phasePos, setPhasePos] = useState({ x: 0, y: 0 }); // floating popup position, top-left in viewport px
  const minAoiDwellSec = Math.max(0, parseFloat(minAoiDwellInput) || 0);
  const timer = useRef(null);
  const virtualT = useRef(0); // continuous playback clock, independent of the snapped/displayed sample
  const instrumentVideoRef = useRef(null);
  const otwVideoRef = useRef(null);
  const [groundScrubbing, setGroundScrubbing] = useState(false);
  const groundTrackRef = useRef(null);
  useEffect(() => {
    if (!groundScrubbing) return;
    const stop = () => setGroundScrubbing(false);
    window.addEventListener("mouseup", stop);
    return () => window.removeEventListener("mouseup", stop);
  }, [groundScrubbing]);

  useEffect(() => {
    (async () => {
      try {
        const [sm, fl, ey] = await Promise.all([
          api.summary(id), api.flight(id), api.eye(id),
        ]);
        setSummary(sm); setFlight(fl); setEye(ey);
      } catch (e) { setErr(e.message); }
    })();
    api.listAoiZones().then(setAoiZones).catch(() => {});
  }, [id]);

  const t0 = flight.length ? new Date(flight[0].ts).getTime() : 0;
  const elapsed = (ts) => (new Date(ts).getTime() - t0) / 1000;
  const curT = flight.length ? elapsed(flight[Math.min(cursor, flight.length - 1)].ts) : 0;
  const totalT = flight.length ? elapsed(flight[flight.length - 1].ts) : 0;

  // nearest eye sample for each flight sample
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


  const eyeTimeline = useMemo(() => eye.map((e) => ({ t: elapsed(e.ts) })), [eye]);
  const flightTimeline = useMemo(() => flight.map((r) => ({ t: elapsed(r.ts) })), [flight]);
  const gaze = eyeTimeline.length ? interpolatedGaze(eye, eyeTimeline, curT) : null;
  const gazeStretch = useGazeStretch(gaze, curT);

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

  // downsampled purely for the chart's own rendering 
  const chartSeries = useMemo(() => {
    if (series.length <= CHART_MAX_POINTS) return series;
    const stride = Math.ceil(series.length / CHART_MAX_POINTS);
    const out = [];
    for (let i = 0; i < series.length; i += stride) out.push(series[i]);
    const last = series[series.length - 1];
    if (out[out.length - 1] !== last) out.push(last);
    return out;
  }, [series]);

  // x-axis ticks every 10s
  const timelineTicks = useMemo(() => {
    if (!series.length) return [];
    const maxT = series[series.length - 1].t;
    const ticks = [];
    for (let t = 0; t <= maxT; t += 10) ticks.push(t);
    return ticks;
  }, [series]);

  // Raw AOI transitions (collapse consecutive identical AOIs)
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
  // (or other tracking artifacts) throw brief AOI blips into the gaze stream, so a threshold is 
  // set to fold back into whichever AOI it interrupted rather than counted as a real glance.
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

  // region dwell, cumulative up to the current playback position dwell is time-weighted rather than sample-counted.
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
  // corrected by cos(latitude))
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

  // static full-route: path never changes after load;
  const groundFullPath = useMemo(
    () => groundTrack.map((p) => `${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(" "),
    [groundTrack]
  );
  const groundFlownIdx = groundTrack.length ? nearestIndexForTime(groundTrack, curT) : -1;
  const groundFlownPath = useMemo(() => {
    if (groundFlownIdx < 0) return "";
    return groundTrack.slice(0, groundFlownIdx + 1).map((p) => `${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(" ");
  }, [groundTrack, groundFlownIdx]);
  const groundCurPoint = groundFlownIdx >= 0 ? groundTrack[groundFlownIdx] : null;

  // Simple heuristic flight-phase detector (takeoff/landing only, from
  // altitude/airspeed alone) -- a placeholder stand-in for the proper
  // MSFS-event-flag segmenter (glideslope intercept, stall warning, etc.)
  // described in the analytics plan. Swap this out once real telemetry
  // event flags are wired up; the timeline/popup wiring doesn't change.
  const flightPhases = useMemo(() => {
    const alts = flight.map((r) => r.altitude_ft).filter((v) => v != null);
    if (alts.length < 2) return [];
    // a flight that barely changes altitude has nothing to detect -- guards
    // against a recording that starts/ends already at cruise from tripping
    // a false "takeoff"/"landing" on ordinary turbulence noise
    if (Math.max(...alts) - Math.min(...alts) < 200) return [];

    const phases = [];
    const MARGIN = 50; // ft -- crude "still near the ground" proxy

    // Takeoff: anchored to THIS flight's own starting altitude, not a
    // shared/global minimum -- departure and arrival airports are commonly
    // at different elevations, so a single ground reference for both ends
    // silently breaks landing detection whenever they don't match.
    const startAlt = flight[0].altitude_ft;
    if (startAlt != null) {
      const threshold = startAlt + MARGIN;
      const climbIdx = flight.findIndex((r) => r.altitude_ft != null && r.altitude_ft > threshold);
      if (climbIdx > 0) {
        let rollStart = 0;
        for (let i = climbIdx; i >= 0; i--) {
          if (flight[i].airspeed_kt != null && flight[i].airspeed_kt < 20) { rollStart = i; break; }
        }
        // rounded the same way series[].t is, so a phase's end can't sit a
        // hair past the chart's own domain max and get silently clipped
        const start = +elapsed(flight[rollStart].ts).toFixed(1);
        const end = +elapsed(flight[climbIdx].ts).toFixed(1);
        if (end > start) phases.push({ key: "takeoff", label: "Takeoff", start, end, color: "#f5a623" });
      }
    }

    // Landing: anchored to THIS flight's own ending altitude, same reasoning.
    // Also requires the recording to actually end at taxi speed -- anchoring
    // to "wherever the last sample is" can't otherwise tell a real touchdown
    // apart from a recording that simply got stopped mid-descent, still airborne.
    const lastIdx = flight.length - 1;
    const endAlt = flight[lastIdx].altitude_ft;
    const endSpeed = flight[lastIdx].airspeed_kt;
    if (endAlt != null && endSpeed != null && endSpeed < 40) {
      const threshold = endAlt + MARGIN;
      let descentIdx = -1;
      for (let i = lastIdx; i >= 0; i--) {
        if (flight[i].altitude_ft != null && flight[i].altitude_ft > threshold) { descentIdx = i; break; }
      }
      if (descentIdx >= 0 && descentIdx < lastIdx) {
        const start = +elapsed(flight[descentIdx].ts).toFixed(1);
        const end = +elapsed(flight[lastIdx].ts).toFixed(1);
        if (end > start) phases.push({ key: "landing", label: "Landing", start, end, color: "#38bdf8" });
      }
    }

    return phases;
  }, [flight]);

  // Chronological AOI log, one row per completed (or ongoing) run, built
  // once from `runs` with explicit start/end/duration, independent of playback position.
  const scanLog = useMemo(() => {
    const out = [];
    for (let i = 0; i < runs.length; i++) {
      const start = runs[i].t;
      const end = i + 1 < runs.length ? runs[i + 1].t : lastEyeT;
      // assign order at chronological position in the whole session
      out.push({ order: i + 1, aoi: runs[i].aoi, start, end, dur: Math.max(0, end - start) });
    }
    return out;
  }, [runs, lastEyeT]);;

  // Color assigned by first appearance in the session
  const scanLogColor = useMemo(() => {
    const map = new Map();
    let i = 0;
    for (const r of scanLog) {
      if (!map.has(r.aoi)) map.set(r.aoi, aoiColor(r.aoi, i++));
    }
    return map;
  }, [scanLog]);

  // const scanLogRows = useMemo(() => [...scanLog].reverse(), [scanLog]); newest first
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

  // playback loop -- advances the cursor by actual elapsed wall-clock time
  // (times speed), not a fixed sample count per tick.
  useEffect(() => {
    if (playing && flight.length) {
      // start from the currently displayed position
      virtualT.current = curT;
      let lastTick = performance.now();
      timer.current = setInterval(() => {
        const now = performance.now();
        const dtSec = (now - lastTick) / 1000;
        lastTick = now;
        // accumulate on the continuous clock itself
        virtualT.current += dtSec * speed;
        if (virtualT.current >= totalT) virtualT.current = 0;
        setCursor(nearestIndexForTime(flightTimeline, virtualT.current));
      }, 16);
    }
    return () => clearInterval(timer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, speed, flight.length, totalT, flightTimeline]);

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
  const curEye = eyeTimeline.length ? eye[nearestIndexForTime(eyeTimeline, curT)] : null;
  //const gaze = eyeTimeline.length ? interpolatedGaze(eye, eyeTimeline, curT) : null;
  const curAoi = curEye && !curEye.blink ? effectiveAoi(curEye, aoiZones) : null;
  const lookingAtOtw = curEye && !curEye.blink && curEye.aoi === "OTW";
  const lookingAtInstruments = curEye && !curEye.blink && curEye.aoi === "Instruments";

  // single entry point for every user-initiated jump (seek buttons, chart click, ground-track click, scan-log rows)
  const seekTo = (targetT) => {
    const clamped = Math.max(0, Math.min(totalT, targetT));
    virtualT.current = clamped;
    setCursor(nearestIndexForTime(flightTimeline, clamped));
  };
  const seek = (deltaSec) => seekTo(curT + deltaSec);

  // clicking/dragging directly on the timeline chart scrubs playback
  const seekToChartEvent = (chartEvent) => {
    if (!chartEvent || chartEvent.activeLabel == null) return;
    seekTo(chartEvent.activeLabel);
  };

  // Ground track is drawn in a 100x100 viewBox with preserveAspectRatio, convert
  // screen coords into the track's own square coordinate space first, then
  // snap to whichever flown point is physically closest to the click.
  const seekToGroundEvent = (clientX, clientY) => {
    const el = groundTrackRef.current;
    if (!el || groundTrack.length < 2) return;
    const rect = el.getBoundingClientRect();
    const scale = Math.min(rect.width, rect.height) / 100;
    if (scale <= 0) return;
    const offsetX = (rect.width - scale * 100) / 2;
    const offsetY = (rect.height - scale * 100) / 2;
    const x = (clientX - rect.left - offsetX) / scale;
    const y = (clientY - rect.top - offsetY) / scale;

    let bestIdx = 0, bestDist = Infinity;
    for (let i = 0; i < groundTrack.length; i++) {
      const dx = groundTrack[i].x - x, dy = groundTrack[i].y - y;
      const d = dx * dx + dy * dy;
      if (d < bestDist) { bestDist = d; bestIdx = i; }
    }
    seekTo(groundTrack[bestIdx].t);
  };

  // opens the phase popup centered on screen the first time it's used for this phase
  const openPhase = (p) => {
    setSelectedPhase(p);
    setPhasePos({
      x: Math.max(16, window.innerWidth / 2 - 360),
      y: Math.max(16, window.innerHeight / 2 - 220),
    });
  };

  // drag the popup by its title bar -- closures capture the drag's own
  // starting point so add/remove listener references always match, and
  // dragging one popup can't get confused by a later render's state
  const onPhaseDragStart = (e) => {
    if (e.target.closest(".phase-modal-close")) return;
    const startX = e.clientX, startY = e.clientY;
    const origin = phasePos;
    const onMove = (ev) => {
      setPhasePos({ x: origin.x + (ev.clientX - startX), y: origin.y + (ev.clientY - startY) });
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

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
          <div className={"rev-screen" + (lookingAtOtw ? " active-screen" : "")}>
            <div className="head">OTW</div>
            <div className="body">
              {otwVideoSrc && (
                <video ref={otwVideoRef} src={otwVideoSrc} className="screen-video"
                  muted playsInline preload="auto" />
              )}
              {otwVideoSrc && lookingAtOtw && gaze.x != null && gaze.y != null && (
                <svg viewBox="0 0 1920 1080" preserveAspectRatio="none" className="aoi-overlay-svg">
                  <g className="gaze-cursor"
                    style={{
                      transform: `translate(${gaze.x}px, ${gaze.y}px) rotate(${gazeStretch.angleDeg}deg) scale(${gazeStretch.factor}, ${1 / Math.sqrt(gazeStretch.factor)})`,
                    }}>
                    <circle r="70" className="gaze-halo" />
                  </g>
                </svg>
              )}
            </div>
          </div>

          <div className={"rev-screen" + (lookingAtInstruments ? " active-screen" : "")}>
            <div className="head">Instruments</div>
            <div className="body">
              {instrumentVideoSrc && (
                <video ref={instrumentVideoRef} src={instrumentVideoSrc} className="screen-video"
                  muted playsInline preload="auto" />
              )}
              {instrumentVideoSrc && (
                <svg viewBox="0 0 1920 1080" preserveAspectRatio="none" className="aoi-overlay-svg">
                  {aoiZones.map((z) => {
                    const active = z.name === curAoi;
                    return (
                      <rect key={z.id} x={Math.min(z.x1, z.x2)} y={Math.min(z.y1, z.y2)}
                        width={Math.abs(z.x2 - z.x1)} height={Math.abs(z.y2 - z.y1)}
                        fill={active ? "rgba(74,222,128,0.25)" : "none"}
                        stroke={active ? "#4ade80" : "#38bdf8"}
                        strokeWidth={active ? "4" : "2.5"}
                        strokeDasharray={active ? "none" : "10 6"}
                        opacity={active ? "0.95" : "0.55"} />
                    );
                  })}
                  {lookingAtInstruments && gaze.x != null && gaze.y != null && (
                    <g className="gaze-cursor"
                      style={{
                        transform: `translate(${gaze.x}px, ${gaze.y}px) rotate(${gazeStretch.angleDeg}deg) scale(${gazeStretch.factor}, ${1 / Math.sqrt(gazeStretch.factor)})`,
                      }}>
                      <circle r="70" className="gaze-halo" />
                    </g>
                  )}
                </svg>
              )}
            </div>
          </div>

          <div className="rev-screen">
            <div className="head">Ground track</div>
            <div className="body">
              {groundTrack.length > 1 ? (
                <svg viewBox="0 0 100 100" className="ground-track-svg" preserveAspectRatio="xMidYMid meet"
                  ref={groundTrackRef}
                  onMouseDown={(e) => { setGroundScrubbing(true); seekToGroundEvent(e.clientX, e.clientY); }}
                  onMouseMove={(e) => { if (groundScrubbing) seekToGroundEvent(e.clientX, e.clientY); }}
                  onMouseUp={() => setGroundScrubbing(false)}
                >
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
            <LineChart data={chartSeries} margin={{ top: 4, right: 8, bottom: 0, left: -28 }}
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
              {flightPhases.map((p) => {
                // widen only the drawn band, never the real start/end used
                // by the popup/seek -- a genuinely short phase (e.g. a
                // landing detected in the last couple seconds) can render
                // as a sliver too thin to see or click on a long timeline.
                // Landing sits at the chart's right edge, so there's often
                // no room to extend forward -- fall back to widening
                // backward (or vice-versa for a phase pinned to the left edge).
                const chartMinT = series.length ? series[0].t : 0;
                const chartMaxT = series.length ? series[series.length - 1].t : totalT;
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
                return (
                  <ReferenceArea key={p.key} x1={x1} x2={x2}
                    fill={p.color} fillOpacity={0.16} stroke={p.color} strokeOpacity={0.6}
                    onClick={() => openPhase(p)} style={{ cursor: "pointer" }}
                    label={{ value: p.label, position: "insideTop", fill: p.color, fontSize: 11, fontWeight: 600 }} />
                );
              })}
              {METRICS.map((m) =>
                filters[m.key] && !(m.key === "workload" && !hasEye) ? (
                  <Line key={m.key} type="monotone" dataKey={m.key} stroke={m.color}
                    dot={false} strokeWidth={1.6} isAnimationActive={false} />
                ) : null
              )}
              <ReferenceLine x={+curT.toFixed(1)} stroke="#ff5c5c" strokeWidth={1.5} />
            </LineChart>
          {/* playback controls */}
          </ResponsiveContainer>
          {flightPhases.length > 0 && (
            <div className="phase-buttons">
              {flightPhases.map((p) => (
                <button key={p.key} className="phase-btn" style={{ borderColor: p.color, color: p.color }}
                  onClick={() => openPhase(p)}>
                  {p.label} · {p.start.toFixed(1)}s–{p.end.toFixed(1)}s
                </button>
              ))}
            </div>
          )}
          <div className="tl-clock">{curT.toFixed(1)}s / {totalT.toFixed(1)}s</div>
          <div className="tl-transport">
            <button className="tl-btn" title="Restart" onClick={() => { seekTo(0); setPlaying(false); }}>↺</button>
            <button className="tl-btn" title="Back 10s" onClick={() => seek(-10)}>« 10s</button>
            <button className="tl-btn play" title="Play / pause" onClick={() => setPlaying((p) => !p)}>
              {playing ? "❚❚" : "▶"}
            </button>
            <button className="tl-btn" title="Forward 10s" onClick={() => seek(10)}>10s »</button>
            <select className="tl-speed" title="Speed" value={speed} onChange={(e) => setSpeed(+e.target.value)}>
              <option value={0.5}>0.5×</option>
              <option value={1}>1×</option>
              <option value={2}>2×</option>
              <option value={5}>5×</option>
            </select>
          </div>
        </div>

        {/* bottom row */}
        <div className="rev-bottom">
          {/* scan path */}
          <div className="rev-card">
            <div className="card-head">
              <h3>Scan path</h3>
              <span className="tag">gaze · {curEye ? (curEye.blink ? "blinking" : curAoi) : "no eye data"}</span>
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

          {/* Flight Telemetry */}
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

          {/* region viewing % */}
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

      {selectedPhase && (
        <div className="phase-modal" style={{ left: phasePos.x, top: phasePos.y }}>
          <div className="phase-modal-head" onMouseDown={onPhaseDragStart}>
            <h3>{selectedPhase.label}</h3>
            <button className="phase-modal-close" onClick={() => setSelectedPhase(null)}>×</button>
          </div>
            <div className="phase-modal-sub">
              {selectedPhase.start.toFixed(1)}s – {selectedPhase.end.toFixed(1)}s
              <span className="phase-modal-dur">({(selectedPhase.end - selectedPhase.start).toFixed(1)}s)</span>
              <button className="phase-modal-jump"
                onClick={() => { seekTo(selectedPhase.start); setSelectedPhase(null); }}>
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
      )}
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