import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api/client.js";
import { aoiColor, MIN_AOI_DWELL_SEC_DEFAULT, CHART_MAX_POINTS } from "./constants.js";
import { bounds, norm, nearestIndexForTime, interpolatedGaze, effectiveAoi } from "./utils.js";
import { useSyncedVideo, useGazeStretch } from "./hooks.js";

// All state, data-fetching and derived computations behind a session
// viewer (Review and Live pages both use this). `id` may be null/undefined
// -- fetching just no-ops until a real session id is supplied, which is how
// the Live page defers loading until "Go Live" creates a session.
//
// `live` swaps the flight data source: Review does a one-shot REST fetch;
// Live opens a WebSocket to stream.py's in-memory buffer instead (no DB
// round trip while the flight is in progress -- see stream.py) and also
// turns on auto-follow-newest-sample.
//
// Returns prop groups meant to be spread straight onto the presentational
// components in components/session/, plus the guard fields (`err`,
// `summary`, `flight`) each page checks before rendering the full layout.
export function useSessionData({ id, live }) {
  const [summary, setSummary] = useState(null);
  const [flight, setFlight] = useState([]);
  const [eye, setEye] = useState([]);
  const [flightLoaded, setFlightLoaded] = useState(false); // distinguishes "still fetching" from "fetched, genuinely empty"
  const [loadProgress, setLoadProgress] = useState(null); // 0..1 while fetching (Review only); null = unknown/indeterminate
  const [aoiZones, setAoiZones] = useState([]);
  const [err, setErr] = useState(null);
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [scrubbing, setScrubbing] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [filters, setFilters] = useState({ altitude: true, airspeed: true, vspeed: false, workload: true });
  const [minAoiDwellInput, setMinAoiDwellInput] = useState(String(MIN_AOI_DWELL_SEC_DEFAULT));
  const [openPhases, setOpenPhases] = useState([]);
  const zCounter = useRef(10);
  const [hiddenAois, setHiddenAois] = useState(new Set());
  const [aoiFilterOpen, setAoiFilterOpen] = useState(false);
  const aoiFilterRef = useRef(null);
  const minAoiDwellSec = Math.max(0, parseFloat(minAoiDwellInput) || 0);
  const timer = useRef(null);
  const virtualT = useRef(0);
  const instrumentVideoRef = useRef(null);
  const otwVideoRef = useRef(null);
  const [groundScrubbing, setGroundScrubbing] = useState(false);
  const groundTrackRef = useRef(null);
  const [followingLive, setFollowingLive] = useState(true); // live mode only: auto-track the newest sample until the user scrubs away

  useEffect(() => {
    if (!groundScrubbing) return;
    const stop = () => setGroundScrubbing(false);
    window.addEventListener("mouseup", stop);
    return () => window.removeEventListener("mouseup", stop);
  }, [groundScrubbing]);

  // session metadata + AOI zones: always a one-shot REST fetch either way
  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    api.summary(id).then((sm) => { if (!cancelled) setSummary(sm); })
      .catch((e) => { if (!cancelled) setErr(e.message); });
    api.listAoiZones().then(setAoiZones).catch(() => {});
    return () => { cancelled = true; };
  }, [id]);

  // flight (+ eye) data: Review fetches once over REST; Live streams over a
  // WebSocket instead, since per-sample REST/DB round trips can't keep up
  // with a real flight (see stream.py). Eye tracking stays post-session
  // only for now, so `eye` just stays empty while live.
  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    setFlightLoaded(false); // reset on every id/live change so a fresh fetch shows "loading", not "empty"
    setLoadProgress(null);

    if (!live) {
      // The real bottleneck is usually the backend's own query+serialize
      // time against a remote DB, which happens before any bytes reach the
      // browser at all -- byte-download progress has nothing to show during
      // that wait and just jumps at the end. So progress here is the max of
      // that (genuinely useful if the payload itself is huge/slow to
      // transfer) and a smooth time-based estimate that fills in the rest,
      // so the bar keeps moving during the opaque server-side wait too.
      const startedAt = performance.now();
      const SIMULATED_CAP = 0.92; // never claims done on its own -- only real completion sets 100%
      const SIMULATED_TAU_SEC = 3;
      const tick = () => {
        if (cancelled) return;
        const elapsedSec = (performance.now() - startedAt) / 1000;
        const simulated = SIMULATED_CAP * (1 - Math.exp(-elapsedSec / SIMULATED_TAU_SEC));
        setLoadProgress((prev) => Math.max(prev ?? 0, simulated));
      };
      const simTimer = setInterval(tick, 150);
      tick();

      const loaded = { flight: 0, eye: 0 };
      const totals = { flight: 0, eye: 0 };
      const reportProgress = () => {
        const total = totals.flight + totals.eye;
        if (!cancelled && total > 0) {
          const real = (loaded.flight + loaded.eye) / total;
          setLoadProgress((prev) => Math.max(prev ?? 0, real));
        }
      };
      Promise.all([
        api.flight(id, undefined, (l, t) => { loaded.flight = l; totals.flight = t; reportProgress(); }),
        api.eye(id, undefined, (l, t) => { loaded.eye = l; totals.eye = t; reportProgress(); }),
      ])
        .then(([fl, ey]) => { if (!cancelled) { setFlight(fl); setEye(ey); } })
        .catch((e) => { if (!cancelled) setErr(e.message); })
        .finally(() => {
          clearInterval(simTimer);
          if (!cancelled) { setFlightLoaded(true); setLoadProgress(1); }
        });
      return () => { cancelled = true; clearInterval(simTimer); };
    }

    // Samples can arrive as fast as the sim polls (up to 60Hz) -- pushing
    // every single one straight into React state would re-render, and
    // recompute every heavy useMemo below over the whole (ever-growing)
    // flight array, up to 60 times a second. Batch them into a buffer and
    // flush into state on a timer instead, so the render rate is bounded
    // no matter how fast data comes in.
    let ws = null;
    let reconnectTimer = null;
    let pendingFlight = [];
    let pendingEye = [];
    const flush = () => {
      if (pendingFlight.length) {
        const batch = pendingFlight;
        pendingFlight = [];
        setFlight((prev) => prev.concat(batch));
      }
      if (pendingEye.length) {
        const batch = pendingEye;
        pendingEye = [];
        setEye((prev) => prev.concat(batch));
      }
    };
    const flushTimer = setInterval(flush, 250);

    const connect = () => {
      ws = new WebSocket(api.liveSocketUrl(id));
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        if (msg.type === "backlog") {
          // backlog supersedes anything still buffered from before reconnect
          pendingFlight = [];
          pendingEye = [];
          setFlight(msg.samples);
          setEye(msg.eye_samples || []);
          setFlightLoaded(true);
        } else if (msg.type === "sample") {
          pendingFlight.push(msg.sample);
        } else if (msg.type === "eye_sample") {
          pendingEye.push(msg.sample);
        }
      };
      ws.onclose = () => {
        if (!cancelled) reconnectTimer = setTimeout(connect, 2000);
      };
    };
    connect();

    return () => {
      cancelled = true;
      clearTimeout(reconnectTimer);
      clearInterval(flushTimer);
      ws.onclose = null; // don't reconnect on our own cleanup close
      ws.close();
    };
  }, [id, live]);

  // live mode only: snap to the newest sample whenever new data lands, unless the user has scrubbed into history
  useEffect(() => {
    if (live && followingLive && flight.length) setCursor(flight.length - 1);
  }, [flight.length, followingLive, live]);

  const t0 = flight.length ? new Date(flight[0].ts).getTime() : 0;
  const elapsed = (ts) => (new Date(ts).getTime() - t0) / 1000;
  const curT = flight.length ? elapsed(flight[Math.min(cursor, flight.length - 1)].ts) : 0;
  const totalT = flight.length ? elapsed(flight[flight.length - 1].ts) : 0;

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

  const workloadRaw = useMemo(() => {
    const raw = flight.map((_, i) => {
      const e = eyeForFlight[i] >= 0 ? eye[eyeForFlight[i]] : null;
      if (!e) return null;
      const vals = [e.pupil_diam_left_mm, e.pupil_diam_right_mm].filter((v) => v != null);
      return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
    });
    const W = 15;
    return raw.map((_, i) => {
      let sum = 0, n = 0;
      for (let k = Math.max(0, i - W); k <= Math.min(raw.length - 1, i + W); k++) {
        if (raw[k] != null) { sum += raw[k]; n++; }
      }
      return n ? sum / n : null;
    });
  }, [flight, eye, eyeForFlight]);
  const hasEye = eye.length > 0;

  const instrumentVideoSrc = summary?.session?.instrument_video_url || null;
  const instrumentOffsetSec = summary?.session?.instrument_video_offset_sec ?? 0;
  const otwVideoSrc = summary?.session?.otw_video_url || null;
  const otwOffsetSec = summary?.session?.otw_video_offset_sec ?? 0;

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

  const chartSeries = useMemo(() => {
    if (series.length <= CHART_MAX_POINTS) return series;
    const stride = Math.ceil(series.length / CHART_MAX_POINTS);
    const out = [];
    for (let i = 0; i < series.length; i += stride) out.push(series[i]);
    const last = series[series.length - 1];
    if (out[out.length - 1] !== last) out.push(last);
    return out;
  }, [series]);

  const chartMinT = series.length ? series[0].t : 0;
  const chartMaxT = series.length ? series[series.length - 1].t : totalT;

  const timelineTicks = useMemo(() => {
    if (!series.length) return [];
    const maxT = series[series.length - 1].t;
    const ticks = [];
    for (let t = 0; t <= maxT; t += 10) ticks.push(t);
    return ticks;
  }, [series]);

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
      y: PAD + inner - ((ys[i] - yMin + yOffset) / range) * inner,
      t: p.t,
    }));
  }, [flight]);

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

  const flightPhases = useMemo(() => {
    const alts = flight.map((r) => r.altitude_ft).filter((v) => v != null);
    if (alts.length < 2) return [];
    if (Math.max(...alts) - Math.min(...alts) < 200) return [];

    const phases = [];
    const MARGIN = 50;

    const startAlt = flight[0].altitude_ft;
    if (startAlt != null) {
      const threshold = startAlt + MARGIN;
      const climbIdx = flight.findIndex((r) => r.altitude_ft != null && r.altitude_ft > threshold);
      if (climbIdx > 0) {
        let rollStart = 0;
        for (let i = climbIdx; i >= 0; i--) {
          if (flight[i].airspeed_kt != null && flight[i].airspeed_kt < 20) { rollStart = i; break; }
        }
        const start = +elapsed(flight[rollStart].ts).toFixed(1);
        const end = +elapsed(flight[climbIdx].ts).toFixed(1);
        if (end > start) phases.push({ key: "takeoff", label: "Takeoff", start, end, color: "#f5a623" });
      }
    }

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

  const scanLog = useMemo(() => {
    const out = [];
    for (let i = 0; i < runs.length; i++) {
      const start = runs[i].t;
      const end = i + 1 < runs.length ? runs[i + 1].t : lastEyeT;
      out.push({ order: i + 1, aoi: runs[i].aoi, start, end, dur: Math.max(0, end - start) });
    }
    return out;
  }, [runs, lastEyeT]);

  const scanLogColor = useMemo(() => {
    const map = new Map();
    let i = 0;
    for (const r of scanLog) {
      if (!map.has(r.aoi)) map.set(r.aoi, aoiColor(r.aoi, i++));
    }
    return map;
  }, [scanLog]);

  const allAois = useMemo(() => [...scanLogColor.keys()], [scanLogColor]);

  const scanLogRows = useMemo(() => {
    const rows = [];
    for (let i = scanLog.length - 1; i >= 0; i--) {
      const r = scanLog[i];
      if (r.start > curT) continue;
      if (hiddenAois.has(r.aoi)) continue;
      const isCurrent = curT < r.end;
      const end = isCurrent ? curT : r.end;
      rows.push({ ...r, end, dur: Math.max(0, end - r.start), isCurrent });
    }
    return rows;
  }, [scanLog, curT, hiddenAois]);

  useEffect(() => {
    if (!aoiFilterOpen) return;
    const onDocClick = (e) => {
      if (aoiFilterRef.current && !aoiFilterRef.current.contains(e.target)) setAoiFilterOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [aoiFilterOpen]);

  const toggleAoiHidden = (name) => {
    setHiddenAois((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name); else next.add(name);
      return next;
    });
  };

  useEffect(() => {
    if (!scrubbing) return;
    const stop = () => setScrubbing(false);
    window.addEventListener("mouseup", stop);
    return () => window.removeEventListener("mouseup", stop);
  }, [scrubbing]);

  useEffect(() => {
    if (playing && flight.length) {
      virtualT.current = curT;
      let lastTick = performance.now();
      timer.current = setInterval(() => {
        const now = performance.now();
        const dtSec = (now - lastTick) / 1000;
        lastTick = now;
        virtualT.current += dtSec * speed;
        if (virtualT.current >= totalT) virtualT.current = 0;
        setCursor(nearestIndexForTime(flightTimeline, virtualT.current));
      }, 16);
    }
    return () => clearInterval(timer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, speed, flight.length, totalT, flightTimeline]);

  useSyncedVideo(instrumentVideoRef, instrumentVideoSrc, instrumentOffsetSec, curT, playing, speed);
  useSyncedVideo(otwVideoRef, otwVideoSrc, otwOffsetSec, curT, playing, speed);

  const ready = !err && summary && flight.length > 0;

  const cur = ready ? flight[cursor] : null;
  const curEye = ready && eyeTimeline.length ? eye[nearestIndexForTime(eyeTimeline, curT)] : null;
  const curAoi = curEye && !curEye.blink ? effectiveAoi(curEye, aoiZones) : null;
  const lookingAtOtw = !!(curEye && !curEye.blink && curEye.aoi === "OTW");
  const lookingAtInstruments = !!(curEye && !curEye.blink && curEye.aoi === "Instruments");

  // any manual jump means the user wants to look at history, not the live edge
  const seekTo = (targetT) => {
    if (live) setFollowingLive(false);
    const clamped = Math.max(0, Math.min(totalT, targetT));
    virtualT.current = clamped;
    setCursor(nearestIndexForTime(flightTimeline, clamped));
  };
  const seek = (deltaSec) => seekTo(curT + deltaSec);

  const goLive = () => {
    setFollowingLive(true);
    setPlaying(false);
    if (flight.length) setCursor(flight.length - 1);
  };

  const seekToChartEvent = (chartEvent) => {
    if (!chartEvent || chartEvent.activeLabel == null) return;
    seekTo(chartEvent.activeLabel);
  };

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

  const openPhase = (p) => {
    setOpenPhases((prev) => {
      const existing = prev.find((w) => w.key === p.key);
      if (existing) {
        zCounter.current += 1;
        return prev.map((w) => (w.key === p.key ? { ...w, z: zCounter.current } : w));
      }
      zCounter.current += 1;
      const stagger = prev.length * 28;
      return [...prev, {
        ...p,
        winId: p.key,
        x: Math.max(16, window.innerWidth / 2 - 360 + stagger),
        y: Math.max(16, window.innerHeight / 2 - 220 + stagger),
        z: zCounter.current,
      }];
    });
  };

  const closePhase = (winId) => setOpenPhases((prev) => prev.filter((w) => w.winId !== winId));

  const bringToFront = (winId) => {
    zCounter.current += 1;
    const z = zCounter.current;
    setOpenPhases((prev) => prev.map((w) => (w.winId === winId ? { ...w, z } : w)));
  };

  const onPhaseDragStart = (winId, e) => {
    if (e.target.closest(".phase-modal-close")) return;
    bringToFront(winId);
    const startX = e.clientX, startY = e.clientY;
    const win = openPhases.find((w) => w.winId === winId);
    if (!win) return;
    const originX = win.x, originY = win.y;
    const onMove = (ev) => {
      const nx = originX + (ev.clientX - startX), ny = originY + (ev.clientY - startY);
      setOpenPhases((prev) => prev.map((w) => (w.winId === winId ? { ...w, x: nx, y: ny } : w)));
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  return {
    err, summary, flight, flightLoaded, loadProgress,

    screens: {
      otwVideoRef, otwVideoSrc, lookingAtOtw,
      instrumentVideoRef, instrumentVideoSrc, lookingAtInstruments,
      aoiZones, curAoi,
      gaze, gazeStretch,
      groundTrack, groundTrackRef, groundFullPath, groundFlownPath, groundCurPoint,
      onGroundMouseDown: (e) => { setGroundScrubbing(true); seekToGroundEvent(e.clientX, e.clientY); },
      onGroundMouseMove: (e) => { if (groundScrubbing) seekToGroundEvent(e.clientX, e.clientY); },
      onGroundMouseUp: () => setGroundScrubbing(false),
    },

    timeline: {
      chartSeries, timelineTicks, filters, setFilters, hasEye,
      flightPhases, chartMinT, chartMaxT, totalT, curT,
      onChartMouseDown: (e) => { setScrubbing(true); seekToChartEvent(e); },
      onChartMouseMove: (e) => { if (scrubbing) seekToChartEvent(e); },
      onChartMouseUp: () => setScrubbing(false),
      seekTo, openPhase,
      playing, onTogglePlay: () => { setPlaying((p) => !p); if (live) setFollowingLive(false); },
      seek, onRestart: () => { seekTo(0); setPlaying(false); },
      speed, setSpeed,
      live, followingLive, goLive,
    },

    scanPath: {
      curEye, curAoi,
      aoiFilterOpen, setAoiFilterOpen, aoiFilterRef,
      allAois, hiddenAois, toggleAoiHidden, scanLogColor,
      minAoiDwellInput, setMinAoiDwellInput,
      scanLogRows, seekTo,
    },

    liveState: { cur },
    regionPie: { regions },

    phases: {
      openPhases,
      onClose: closePhase,
      onBringToFront: bringToFront,
      onDragStart: onPhaseDragStart,
      seekTo,
    },

    followingLive, goLive,
  };
}
