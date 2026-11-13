import { memo, useEffect, useLayoutEffect, useRef, useState } from "react";

// Binds a live MediaStream (browser screen-capture) to a <video> ref --
// srcObject can't be set as a JSX attribute like src can.
function useStreamBinding(ref, stream) {
  useEffect(() => {
    if (ref.current) ref.current.srcObject = stream || null;
  }, [ref, stream]);
}

// Gaze trail across the OTW and Instruments screens, drawn on one layer
// over the whole screens row (in on-screen pixels): numbered circles
// (newest = 5, counting down to the oldest) at each glance -- instrument glances on their AOI zone,
// OTW glances on the OTW screen where the eyes were -- sized by glance
// length and joined by curved arrows in order, crossing between the two
// screens. Older glances fade. Only labelled areas (AOI zones + OTW) are
// steps -- see scanTrail in useSessionData.js.
//
// Smoothness: every circle keeps a stable key (its glance), so as playback
// runs it fades/grows/slides via CSS transitions instead of being re-drawn,
// and each new connector sweeps in.

// The video picture inside a 16:9 screen body is the whole body (the body
// is kept exactly 16:9), so 1920x1080 maps straight onto it.
const toScreen = (box, x, y) => ({ x: box.x + (x / 1920) * box.w, y: box.y + (y / 1080) * box.h });

// Memoised: the trail changes at the slower view rate, the gaze dot every frame.
const ScanTrail = memo(function ScanTrail({ trail: raw, boxes, maxLen }) {
  if (!raw?.length || !boxes?.instrument || !boxes?.otw) return null;
  const n = raw.length;
  const u = boxes.instrument.w / 1920;                 // 1920-space units -> px
  const radius = (dur) => Math.max(48, Math.min(110, 48 + dur * 12)) * u;
  const alpha = (i) => (n === 1 ? 1 : 0.25 + (0.75 * i) / (n - 1));

  // Numbered from the newest down: the latest glance is always maxLen (5),
  // the one before it 4, and so on -- every number shifts down by one as a
  // new glance arrives. Each step sits on screen with instruments exactly on
  // their zone centre and OTW where the eyes were.
  const top = Math.max(maxLen || n, n);
  const steps = raw.map((p, i) => ({ ...p, step: top - (n - 1 - i), ...toScreen(boxes[p.screen], p.x, p.y) }));

  // Every step is its own circle with its own number, right on its
  // instrument's centre (or where the eyes were, for OTW). Repeat visits to
  // the same instrument simply overlap; circles are drawn oldest first, so
  // the newest is always on top and readable.
  const nodes = steps.map((p, i) => ({ ...p, key: p.id, label: String(p.step), alpha: alpha(i) }));
  const nodeOf = (p) => nodes[steps.indexOf(p)];

  // Curved connector between two circles, trimmed to their edges. It bends
  // to the left of the direction of travel, so A->B and B->A curve to
  // opposite sides instead of drawing on top of each other.
  const curve = (a, b) => {
    const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy);
    const ra = radius(a.dur) + 2, rb = radius(b.dur) + 4;
    if (len <= ra + rb) return null;
    const ux = dx / len, uy = dy / len;
    const x1 = a.x + ux * ra, y1 = a.y + uy * ra, x2 = b.x - ux * rb, y2 = b.y - uy * rb;
    const bend = Math.min(40, len * 0.22);
    const cx = (x1 + x2) / 2 - uy * bend, cy = (y1 + y2) / 2 + ux * bend;
    return `M${x1.toFixed(1)},${y1.toFixed(1)} Q${cx.toFixed(1)},${cy.toFixed(1)} ${x2.toFixed(1)},${y2.toFixed(1)}`;
  };

  return (
    <g className="scan-trail">
      <defs>
        <marker id="scan-trail-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="4.5" markerHeight="4.5" orient="auto">
          <path d="M0,0 L10,5 L0,10 z" fill="#fff" />
        </marker>
      </defs>
      {steps.slice(1).map((b, k) => {
        const d = curve(nodeOf(steps[k]), nodeOf(b));
        if (!d) return null;
        return (
          <path key={`${steps[k].id}-${b.id}`} d={d} pathLength="1" className="scan-trail__link"
            style={{ opacity: alpha(k + 1) }} markerEnd="url(#scan-trail-arrow)" />
        );
      })}
      {nodes.map((nd) => (
        <g key={nd.key} className={"scan-trail__dot" + (nd.isCurrent ? " scan-trail__now" : "") + (nd.isOtw || nd.isPanel ? " is-free" : "")}
          style={{ opacity: nd.alpha }}>
          <circle cx={nd.x} cy={nd.y} r={radius(nd.dur)} fill={nd.color} fillOpacity="0.32" stroke={nd.color} />
          <text x={nd.x} y={nd.y} textAnchor="middle" dominantBaseline="central" className="scan-trail__num">{nd.label}</text>
        </g>
      ))}
    </g>
  );
});

// The gaze halo. An HTML layer moved only by `transform` (no SVG, no
// filter), so the browser slides an already-drawn circle each frame instead
// of repainting the whole video-sized overlay -- that repaint was the
// playback stutter. The track is the size of the screen body, so translate
// percentages are of the body: x/1920, y/1080. `eased` (live view, gaze
// arrives in ~250ms batches) glides between points; replay moves per frame.
const ROUND = { angleDeg: 0, factor: 1 };
function GazeHalo({ gaze, stretch, eased }) {
  if (gaze?.x == null || gaze?.y == null) return null;
  const s = stretch || ROUND;
  return (
    <div className="gaze-layer">
      <div className={"gaze-track" + (eased ? " gaze-track--eased" : "")}
        style={{ transform: `translate(${gaze.x / 19.2}%, ${gaze.y / 10.8}%)` }}>
        <div className="gaze-halo"
          style={{ transform: `translate(-50%, -50%) rotate(${s.angleDeg}deg) scale(${s.factor}, ${1 / Math.sqrt(s.factor)})` }} />
      </div>
    </div>
  );
}

// Where the OTW and Instruments screen bodies sit inside the screens row,
// in the row's own on-screen pixels (re-measured on resize). Using one
// coordinate space for the row lets trail arrows cross between screens.
function useScreenBoxes(rowRef, otwRef, instRef) {
  const [boxes, setBoxes] = useState(null);
  useLayoutEffect(() => {
    const row = rowRef.current;
    if (!row) return;
    const measure = () => {
      const r = row.getBoundingClientRect();
      const rel = (el) => {
        if (!el) return null;
        const b = el.getBoundingClientRect();
        return { x: b.left - r.left, y: b.top - r.top, w: b.width, h: b.height };
      };
      setBoxes({ row: { w: r.width, h: r.height }, otw: rel(otwRef.current), instrument: rel(instRef.current) });
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(row);
    return () => ro.disconnect();
  }, [rowRef, otwRef, instRef]);
  return boxes;
}

// Review (`fitGround`): when the row narrows (the docked column beside it is
// dragged wider), the ground track gives up its width first while OTW and
// Instruments keep theirs (review.css) -- and once it's too thin to read it's
// hidden outright. Hidden = the room left beside OTW + Instruments is under
// GROUND_MIN_W. Only while the screens sit side by side (not stacked).
const GROUND_MIN_W = 120;
function useGroundHidden(enabled, rowRef) {
  const [hidden, setHidden] = useState(false);
  useLayoutEffect(() => {
    const row = rowRef.current;
    if (!enabled || !row) return;
    const measure = () => {
      const cs = getComputedStyle(row);
      if (cs.display !== "flex" || cs.flexDirection !== "row") { setHidden(false); return; }
      const [otw, inst] = row.querySelectorAll(":scope > .rev-screen");
      const gap = parseFloat(cs.columnGap) || 0;
      setHidden(row.clientWidth - otw.offsetWidth - inst.offsetWidth - 2 * gap < GROUND_MIN_W);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(row);
    return () => ro.disconnect();
  }, [enabled, rowRef]);
  return enabled && hidden;
}

// The three video/track panels at the top of the session viewer: OTW,
// Instruments (with AOI zone overlay), and the ground track map.
//
// A screen shows one of four things: a live-captured MediaStream (Live
// page, via the browser's own screen-capture picker), the backend's
// auto-captured window feed (Live page, MJPEG `*Feed` URL -- the picker
// overrides it), a finished OBS file (Review, `src=`), or neither.
export default function ScreensPanel({
  otwVideoRef, otwVideoSrc, otwStream, otwFeed, onOtwFeedError, lookingAtOtw,
  instrumentVideoRef, instrumentVideoSrc, instrumentStream, instrumentFeed, onInstrumentFeedError,
  lookingAtInstruments,
  aoiZones, curAoi, scanTrail, scanTrailLen,
  gaze, gazeStretch,
  groundTrack, groundTrackRef, groundFullPath, groundFlownPath, groundCurPoint,
  groundRoute,
  onGroundMouseDown, onGroundMouseMove, onGroundMouseUp,
  live, onCaptureOtw, onStopOtw, onCaptureInstrument, onStopInstrument,
  fitGround,
}) {
  useStreamBinding(otwVideoRef, otwStream);
  useStreamBinding(instrumentVideoRef, instrumentStream);

  const hasOtw = !!(otwStream || otwFeed || otwVideoSrc);
  const hasInstrument = !!(instrumentStream || instrumentFeed || instrumentVideoSrc);
  const [showTrail, setShowTrail] = useState(true);
  const rowRef = useRef(null), otwBodyRef = useRef(null), instBodyRef = useRef(null);
  const boxes = useScreenBoxes(rowRef, otwBodyRef, instBodyRef);
  const groundHidden = useGroundHidden(fitGround, rowRef);
  // Draw zones + trail even without a linked recording -- the layout of
  // the zones alone still shows where the eyes went.
  const showInstrumentOverlay = hasInstrument || (scanTrail?.length > 0 && aoiZones.length > 0);

  return (
    <div className={"rev-screens" + (groundHidden ? " is-ground-hidden" : "")} ref={rowRef}>
      <div className={"rev-screen" + (lookingAtOtw ? " active-screen" : "")}>
        <div className="head">
          OTW
          {live && (
            <button className="capture-btn" onClick={otwStream ? onStopOtw : onCaptureOtw}>
              {otwStream ? "■ Stop" : "◻ Capture"}
            </button>
          )}
        </div>
        <div className="body" ref={otwBodyRef}>
          {otwStream ? (
            <video ref={otwVideoRef} className="screen-video" autoPlay muted playsInline />
          ) : otwFeed ? (
            <img src={otwFeed} className="screen-video" alt="" onError={onOtwFeedError} />
          ) : otwVideoSrc ? (
            <video ref={otwVideoRef} src={otwVideoSrc} className="screen-video"
              muted playsInline preload="auto" />
          ) : null}
          {hasOtw && lookingAtOtw && <GazeHalo gaze={gaze} stretch={gazeStretch} eased={live} />}
        </div>
      </div>

      <div className={"rev-screen" + (lookingAtInstruments ? " active-screen" : "")}>
        <div className="head">
          Instruments
          {scanTrail && (
            <button type="button" className={"capture-btn trail-btn" + (showTrail ? " is-on" : "")}
              onClick={() => setShowTrail((v) => !v)} title="Show the last few glances as a numbered trail">
              Trail
            </button>
          )}
          {live && (
            <button className="capture-btn" onClick={instrumentStream ? onStopInstrument : onCaptureInstrument}>
              {instrumentStream ? "■ Stop" : "◻ Capture"}
            </button>
          )}
        </div>
        <div className="body" ref={instBodyRef}>
          {instrumentStream ? (
            <video ref={instrumentVideoRef} className="screen-video" autoPlay muted playsInline />
          ) : instrumentFeed ? (
            <img src={instrumentFeed} className="screen-video" alt="" onError={onInstrumentFeedError} />
          ) : instrumentVideoSrc ? (
            <video ref={instrumentVideoRef} src={instrumentVideoSrc} className="screen-video"
              muted playsInline preload="auto" />
          ) : null}
          {showInstrumentOverlay && (
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
            </svg>
          )}
          {showInstrumentOverlay && lookingAtInstruments && <GazeHalo gaze={gaze} stretch={gazeStretch} eased={live} />}
        </div>
      </div>

      <div className="rev-screen rev-screen--ground">
        <div className="head">
          Ground track
          {/* the circuit this session flew, drawn under the track (picked automatically) */}
          {groundRoute && <span className="route-label" title="Planned circuit for this session">{groundRoute.label}</span>}
        </div>
        <div className="body">
          {groundTrack.length > 1 ? (
            <svg viewBox="0 0 100 100" className="ground-track-svg" preserveAspectRatio="xMidYMid meet"
              ref={groundTrackRef}
              onMouseDown={onGroundMouseDown}
              onMouseMove={onGroundMouseMove}
              onMouseUp={onGroundMouseUp}
            >
              {/* MAVIS reference route, under the actual flight path */}
              {groundRoute && (
                <g className="ground-route">
                  <polyline points={groundRoute.path} className="ground-route__path" />
                  <line {...groundRoute.runway} className="ground-route__runway" />
                </g>
              )}
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

      {/* Gaze trail on top of both screens, so arrows can cross between them. */}
      {showTrail && boxes && scanTrail?.length > 0 && (
        <svg className="scan-trail-layer" viewBox={`0 0 ${boxes.row.w} ${boxes.row.h}`} preserveAspectRatio="none">
          <ScanTrail trail={scanTrail} boxes={boxes} maxLen={scanTrailLen} />
        </svg>
      )}
    </div>
  );
}
