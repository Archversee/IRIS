import { useEffect, useState } from "react";

// Binds a live MediaStream (browser screen-capture) to a <video> ref --
// srcObject can't be set as a JSX attribute like src can.
function useStreamBinding(ref, stream) {
  useEffect(() => {
    if (ref.current) ref.current.srcObject = stream || null;
  }, [ref, stream]);
}

// Gaze trail over the Instruments screen (1920x1080 overlay coordinates):
// numbered circles at each glanced zone (1 = oldest), sized by glance
// length, joined by curved arrows in order. Older glances fade; a dashed
// arrow means the gaze went elsewhere (e.g. OTW) in between.
//
// Smoothness: every glance keeps a stable key (its order in the flight)
// and a fixed position, so as playback runs circles fade/grow via CSS
// transitions instead of being re-drawn, and each new connector sweeps in.
const TRAIL_RING = 150; // px from the zone centre for repeat visits

// Fixed spot for a glance: the zone centre, or one of 4 points on a ring
// around it -- picked from the glance's order, so it never moves while it
// stays in the trail (the hub instrument recurs every other glance, and
// alternating orders land on different spots).
function trailPos(p) {
  const slot = p.id % 5;
  if (!slot) return { x: p.x, y: p.y };
  const ang = (slot - 1) * (Math.PI / 2) - Math.PI / 4;
  return { x: p.x + Math.cos(ang) * TRAIL_RING, y: p.y + Math.sin(ang) * TRAIL_RING };
}

function ScanTrail({ trail: raw }) {
  if (!raw?.length) return null;
  const n = raw.length;
  const radius = (dur) => Math.max(48, Math.min(110, 48 + dur * 12));
  const alpha = (i) => (n === 1 ? 1 : 0.25 + (0.75 * i) / (n - 1));
  const trail = raw.map((p) => ({ ...p, ...trailPos(p) }));

  // Curved connector from a to b, trimmed to the circles' edges; bends
  // gently to one side so back-and-forth moves don't overlap.
  const curve = (a, b) => {
    const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy);
    const ra = radius(a.dur) + 8, rb = radius(b.dur) + 16;
    if (len <= ra + rb) return null;
    const ux = dx / len, uy = dy / len;
    const x1 = a.x + ux * ra, y1 = a.y + uy * ra, x2 = b.x - ux * rb, y2 = b.y - uy * rb;
    const bend = Math.min(120, len * 0.18);
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
      {trail.slice(1).map((b, k) => {
        const d = curve(trail[k], b);
        if (!d) return null;
        return (
          <path key={`${trail[k].id}-${b.id}`} d={d} pathLength="1"
            className={"scan-trail__link" + (b.afterGap ? " is-gap" : "")}
            style={{ opacity: alpha(k + 1) }} markerEnd="url(#scan-trail-arrow)" />
        );
      })}
      {trail.map((p, i) => (
        <g key={p.id} className={"scan-trail__dot" + (p.isCurrent ? " scan-trail__now" : "")} style={{ opacity: alpha(i) }}>
          <circle cx={p.x} cy={p.y} r={radius(p.dur)} fill={p.color} fillOpacity="0.32"
            stroke={p.color} strokeWidth={p.isCurrent ? 10 : 6} />
          <text x={p.x} y={p.y} textAnchor="middle" dominantBaseline="central" className="scan-trail__num">{i + 1}</text>
        </g>
      ))}
    </g>
  );
}

// The three video/track panels at the top of the session viewer: OTW,
// Instruments (with AOI zone overlay), and the ground track map.
//
// A screen shows one of three things: a live-captured MediaStream (Live
// page, via the browser's own screen-capture picker), a finished OBS file
// (Review, `src=`), or neither (nothing linked/captured yet).
export default function ScreensPanel({
  otwVideoRef, otwVideoSrc, otwStream, lookingAtOtw,
  instrumentVideoRef, instrumentVideoSrc, instrumentStream, lookingAtInstruments,
  aoiZones, curAoi, scanTrail,
  gaze, gazeStretch,
  groundTrack, groundTrackRef, groundFullPath, groundFlownPath, groundCurPoint,
  onGroundMouseDown, onGroundMouseMove, onGroundMouseUp,
  live, onCaptureOtw, onStopOtw, onCaptureInstrument, onStopInstrument,
}) {
  useStreamBinding(otwVideoRef, otwStream);
  useStreamBinding(instrumentVideoRef, instrumentStream);

  const hasOtw = !!(otwStream || otwVideoSrc);
  const hasInstrument = !!(instrumentStream || instrumentVideoSrc);
  const [showTrail, setShowTrail] = useState(true);
  // Draw zones + trail even without a linked recording -- the layout of
  // the zones alone still shows where the eyes went.
  const showInstrumentOverlay = hasInstrument || (scanTrail?.length > 0 && aoiZones.length > 0);

  return (
    <div className="rev-screens">
      <div className={"rev-screen" + (lookingAtOtw ? " active-screen" : "")}>
        <div className="head">
          OTW
          {live && (
            <button className="capture-btn" onClick={otwStream ? onStopOtw : onCaptureOtw}>
              {otwStream ? "■ Stop" : "◻ Capture"}
            </button>
          )}
        </div>
        <div className="body">
          {otwStream ? (
            <video ref={otwVideoRef} className="screen-video" autoPlay muted playsInline />
          ) : otwVideoSrc ? (
            <video ref={otwVideoRef} src={otwVideoSrc} className="screen-video"
              muted playsInline preload="auto" />
          ) : null}
          {hasOtw && lookingAtOtw && gaze?.x != null && gaze?.y != null && (
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
        <div className="body">
          {instrumentStream ? (
            <video ref={instrumentVideoRef} className="screen-video" autoPlay muted playsInline />
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
              {showTrail && <ScanTrail trail={scanTrail} />}
              {lookingAtInstruments && gaze?.x != null && gaze?.y != null && (
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
              onMouseDown={onGroundMouseDown}
              onMouseMove={onGroundMouseMove}
              onMouseUp={onGroundMouseUp}
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
  );
}
