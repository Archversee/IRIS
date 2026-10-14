import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api/client.js";
import { aoiColor, eventColor, MIN_AOI_DWELL_SEC_DEFAULT, CHART_MAX_POINTS } from "./constants.js";
import { bounds, norm, nearestIndexForTime, interpolatedGaze, effectiveAoi, buildScanRuns } from "./utils.js";
import { useSyncedVideo, useGazeStretch } from "./hooks.js";

const SCAN_TRAIL_LEN = 5; // glances shown in the gaze trail (Instruments + OTW screens)

// Mean gaze point (1920x1080 space of that screen) over one glance, using
// samples whose raw area is `rawAoi` ("OTW" or "Instruments"), or null.
function meanGaze(eye, eyeTimeline, rawAoi, start, end) {
  let sx = 0, sy = 0, n = 0;
  for (let i = nearestIndexForTime(eyeTimeline, start); i < eye.length && eyeTimeline[i].t <= end; i++) {
    const e = eye[i];
    if (!e.blink && e.aoi === rawAoi && e.gaze_point_x != null && e.gaze_point_y != null) {
      sx += e.gaze_point_x; sy += e.gaze_point_y; n++;
    }
  }
  return n ? { x: sx / n, y: sy / n } : null;
}

// All state, data-fetching and derived computations behind a session
// viewer (Review and Live pages both use this). `id` may be null/undefined
// -- fetching just no-ops until a real session id is supplied, which is how
// the Live page defers loading until a running MAVIS session is picked.
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
  // Ground track reference route (MAVIS planned circuit): "" = none.
  const [routes, setRoutes] = useState([]);
  const [routeKey, setRouteKey] = useState(null); // null = not chosen yet -> auto-pick once
  const routeAutoRef = useRef(false);                  // true while routeKey is the automatic pick
  const [err, setErr] = useState(null);
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [scrubbing, setScrubbing] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [filters, setFilters] = useState({ altitude: true, airspeed: true, vspeed: false, workload: true });
  const [minAoiDwellInput, setMinAoiDwellInput] = useState(String(MIN_AOI_DWELL_SEC_DEFAULT));
  const [openPhases, setOpenPhases] = useState([]);
  const [flightEvents, setFlightEvents] = useState([]); // MAVIS-logged events (to-do steps / phases)
  const [sessionFlights, setSessionFlights] = useState([]); // which curriculum each flight came from
  const [flightsLoaded, setFlightsLoaded] = useState(false);
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
    api.listRoutes().then(setRoutes).catch(() => setRoutes([]));
    return () => { cancelled = true; };
  }, [id]);

  // MAVIS event log: one fetch for a finished session; a live one is polled
  // since the newest event is still open and its end arrives when the next starts
  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    const load = () => {
      api.events(id).then((ev) => { if (!cancelled) setFlightEvents(ev); })
        .catch((e) => console.warn("flight events unavailable:", e?.message || e));
      api.flights(id).then((fl) => { if (!cancelled) setSessionFlights(fl); })
        .catch(() => {})
        .finally(() => { if (!cancelled) setFlightsLoaded(true); });
    };
    load();
    const timer = live ? setInterval(load, 3000) : null;
    return () => { cancelled = true; if (timer) clearInterval(timer); };
  }, [id, live]);

  // flight (+ eye) data: Review fetches once over REST; Live streams both
  // over a WebSocket instead, since per-sample REST/DB round trips can't
  // keep up with a real flight (see stream.py) -- eye samples arrive here
  // the same way flight samples do (msg.type "eye_sample"/backlog's
  // eye_samples), which is what drives ScreensPanel's live gaze halo.
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

  // "Nice" round step sizes (seconds) to pick from, smallest first -- a
  // fixed 10s step used to mean a long flight's x-axis got cluttered with
  // hundreds of ticks. Instead, pick the smallest of these that keeps the
  // total tick count within MAX_TIMELINE_TICKS, so the interval naturally
  // grows coarser (10s -> 1min -> 5min -> ...) as the flight gets longer.
  const NICE_TIMELINE_STEPS_SEC = [5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400];
  const MAX_TIMELINE_TICKS = 25;

  const timelineTicks = useMemo(() => {
    if (!series.length) return [];
    const maxT = series[series.length - 1].t;
    if (maxT <= 0) return [0];
    const step = NICE_TIMELINE_STEPS_SEC.find((s) => Math.floor(maxT / s) + 1 <= MAX_TIMELINE_TICKS)
      ?? Math.ceil(maxT / (MAX_TIMELINE_TICKS - 1) / 3600) * 3600; // longer than a full day -- fall back to a round number of hours
    const ticks = [];
    for (let t = 0; t <= maxT; t += step) ticks.push(t);
    return ticks;
  }, [series]);

  const lastEyeT = eye.length ? elapsed(eye[eye.length - 1].ts) : 0;

  // [{aoi, t}] -- see buildScanRuns() in utils.js (shared with Analytics).
  const runs = useMemo(
    () => buildScanRuns(eye, aoiZones, elapsed, minAoiDwellSec, lastEyeT),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [eye, aoiZones, minAoiDwellSec, lastEyeT, t0],
  );

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

  // A Free Flight session has no planned route: MAVIS only has some lesson loaded underneath
  // (the first circuit), and drawing that circuit would also squash the real track into a corner.
  const isFreeFlight = sessionFlights.some((f) => f.curriculum === "free_flight");

  // Auto-pick the reference route once: the session's own lesson if MAVIS
  // flew it, otherwise any route -- from the runway end nearest where the
  // flight starts (within 3 km). Falls back to none. Never for Free Flight.
  useEffect(() => {
    if (routeKey !== null || !routes.length || !flight.length || !summary || !flightsLoaded) return;
    if (isFreeFlight) { setRouteKey(""); return; }
    const start = flight.find((r) => r.latitude != null && r.longitude != null);
    if (!start) return;
    const lessonId = summary.session?.lesson_id;
    const km = (a) => Math.hypot((a[0] - start.latitude) * 111.32,
      (a[1] - start.longitude) * 111.32 * Math.cos((start.latitude * Math.PI) / 180));
    const ranked = routes
      .map((r) => ({ r, d: km(r.runway[0]) - (lessonId && r.lesson_id === lessonId ? 1000 : 0) }))
      .sort((a, b) => a.d - b.d);
    const best = ranked[0];
    routeAutoRef.current = true;
    setRouteKey(best && km(best.r.runway[0]) <= 3 ? best.r.key : "");
  }, [routes, flight, summary, routeKey, flightsLoaded, isFreeFlight]);

  // Live: the flight record can arrive just after the route was auto-picked. If it turns out
  // to be Free Flight, drop that automatic pick (a route the user chose by hand stays).
  useEffect(() => {
    if (isFreeFlight && routeAutoRef.current && routeKey) { routeAutoRef.current = false; setRouteKey(""); }
  }, [isFreeFlight, routeKey]);
  const pickRoute = (k) => { routeAutoRef.current = false; setRouteKey(k); };

  const route = routes.find((r) => r.key === routeKey) || null;

  // Ground track in a 100x100 box. The view fits the flown path AND the
  // reference route (if one is shown), so both use the same projection.
  const groundGeo = useMemo(() => {
    const pts = flight
      .map((r) => ({ lat: r.latitude, lon: r.longitude, t: elapsed(r.ts) }))
      .filter((p) => p.lat != null && p.lon != null);
    if (pts.length < 2) return { track: [], project: null };

    const extra = route ? [...route.path, ...route.runway] : [];
    const lats = [...pts.map((p) => p.lat), ...extra.map((q) => q[0])];
    const lonScale = Math.cos(((Math.min(...lats) + Math.max(...lats)) / 2) * Math.PI / 180) || 1;
    const xsAll = [...pts.map((p) => p.lon * lonScale), ...extra.map((q) => q[1] * lonScale)];
    const xMin = Math.min(...xsAll), xMax = Math.max(...xsAll);
    const yMin = Math.min(...lats), yMax = Math.max(...lats);
    const range = Math.max(xMax - xMin, yMax - yMin) || 1;
    const xOffset = (range - (xMax - xMin)) / 2;
    const yOffset = (range - (yMax - yMin)) / 2;

    const SIZE = 100, PAD = 8, inner = SIZE - PAD * 2;
    const project = (lat, lon) => ({
      x: PAD + ((lon * lonScale - xMin + xOffset) / range) * inner,
      y: PAD + inner - ((lat - yMin + yOffset) / range) * inner,
    });
    return { track: pts.map((p) => ({ ...project(p.lat, p.lon), t: p.t })), project };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flight, route]);
  const groundTrack = groundGeo.track;

  const toPoints = (arr) => arr.map((p) => `${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(" ");
  const groundRoute = useMemo(() => {
    if (!route || !groundGeo.project) return null;
    const P = (q) => groundGeo.project(q[0], q[1]);
    const [a, b] = route.runway.map(P);
    return { path: toPoints(route.path.map(P)), runway: { x1: a.x, y1: a.y, x2: b.x, y2: b.y }, ident: route.runway_ident };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route, groundGeo]);

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

  const heuristicPhases = useMemo(() => {
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

  // Events logged by MAVIS replace the altitude-based takeoff/landing guess: their
  // boundaries come from the lesson's to-do list, so each ends exactly where the
  // next starts. An event still open runs to the newest sample.
  const flightPhases = useMemo(() => {
    if (!flightEvents.length || !flight.length) return heuristicPhases;
    const multi = new Set(flightEvents.map((e) => e.flight)).size > 1;   // several flights in this session
    return flightEvents.map((e, i) => {
      const start = +elapsed(e.start_ts).toFixed(1);
      const end = +(e.end_ts ? elapsed(e.end_ts) : totalT).toFixed(1);
      const name = e.attempt > 1 ? `${e.label} (try ${e.attempt})` : e.label;
      return {
        key: `ev${e.seq}`, event: true, kind: e.kind, flight: e.flight, curriculum: e.flight_title,
        label: multi ? `F${e.flight} · ${name}` : name,
        start, end, color: eventColor(e, i), eventId: e.event_id, outcome: e.outcome,
        score: e.score, grade: e.grade, detail: e.detail,
      };
    }).filter((p) => p.end > p.start);
  }, [flightEvents, heuristicPhases, flight, totalT]);

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

  // Gaze trail across the Instruments and OTW screens: the last few glances
  // up to now. Steps are the named AOI zones, OTW, and plain "Instruments"
  // (on the panel but outside every zone). Unlabelled samples are skipped:
  // not a step, and a return to the same area across one stays a single
  // glance. Zone glances sit at their zone's centre; OTW and plain
  // Instruments glances where the eyes were on that screen (mean gaze
  // point, else the screen centre). Coordinates are each screen's own
  // 1920x1080 space; `screen` says which.
  const scanTrail = useMemo(() => {
    const zoneByName = new Map(aoiZones.map((z) => [z.name, z]));
    const labelled = (aoi) => aoi === "OTW" || aoi === "Instruments" || zoneByName.has(aoi);
    // Walk back from now, merging consecutive same-area runs, until enough steps.
    const steps = [];
    let latest = null; // the run the gaze is in right now (labelled or not)
    for (let i = scanLog.length - 1; i >= 0; i--) {
      const r = scanLog[i];
      if (r.start > curT) continue;
      if (!latest) latest = r;
      if (hiddenAois.has(r.aoi) || !labelled(r.aoi)) continue;
      const end = Math.min(r.end, curT), dur = Math.max(0, end - r.start);
      const head = steps[0];
      if (head && head.aoi === r.aoi) {          // same area again -> one glance
        head.start = r.start; head.id = r.order; head.dur += dur; continue;
      }
      if (steps.length === SCAN_TRAIL_LEN) break;
      steps.unshift({ id: r.order, aoi: r.aoi, start: r.start, end, dur, isCurrent: r === latest && curT < r.end });
    }
    return steps.map((st) => {
      const isOtw = st.aoi === "OTW";
      const z = zoneByName.get(st.aoi);
      return {
        id: st.id, // earliest run of the glance -- stable, so the trail animates instead of re-drawing
        aoi: st.aoi, color: scanLogColor.get(st.aoi), isOtw, isPanel: !isOtw && !z,
        screen: isOtw ? "otw" : "instrument",
        ...(z
          ? { x: (z.x1 + z.x2) / 2, y: (z.y1 + z.y2) / 2 }
          : (meanGaze(eye, eyeTimeline, st.aoi, st.start, st.end) || { x: 960, y: 540 })),
        dur: st.dur, isCurrent: st.isCurrent,
      };
    });
  }, [scanLog, curT, hiddenAois, aoiZones, scanLogColor, eye, eyeTimeline]);

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
      aoiZones, curAoi, scanTrail, scanTrailLen: SCAN_TRAIL_LEN,
      gaze, gazeStretch,
      groundTrack, groundTrackRef, groundFullPath, groundFlownPath, groundCurPoint,
      routes, routeKey: routeKey ?? "", setRouteKey: pickRoute, groundRoute,
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
