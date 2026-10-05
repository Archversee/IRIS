import { useEffect, useState } from "react";

// Binds a live MediaStream (browser screen-capture) to a <video> ref --
// srcObject can't be set as a JSX attribute like src can.
function useStreamBinding(ref, stream) {
  useEffect(() => {
    if (ref.current) ref.current.srcObject = stream || null;
  }, [ref, stream]);
}

// Gaze trail over the Instruments screen (1920x1080 overlay coordinates):
// numbered circles at each glanced zone's centre (1 = oldest), sized by
// glance length, joined by arrows in order. Older glances fade; a dashed
// arrow means the gaze went elsewhere (e.g. OTW) in between.
function ScanTrail({ trail: raw }) {
  if (!raw?.length) return null;
  const n = raw.length;
  const radius = (dur) => Math.max(48, Math.min(110, 48 + dur * 12));
  // The same zone often recurs (the attitude indicator is the scan's hub):
  // fan repeat visits out around the zone centre so their circles, numbers
  // and arrows don't all stack on one spot.
  const seen = new Map();
  const trail = raw.map((p) => {
    const k = seen.get(p.aoi) || 0;
    seen.set(p.aoi, k + 1);
    if (!k) return p;
    const ang = (k - 1) * (Math.PI / 3) - Math.PI / 2;
    return { ...p, x: p.x + Math.cos(ang) * 165, y: p.y + Math.sin(ang) * 165 };
  });
  const alpha = (i) => (n === 1 ? 1 : 0.3 + (0.7 * i) / (n - 1));
  return (
    <g className="scan-trail">
      <defs>
        <marker id="scan-trail-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" fill="#fff" />
        </marker>
      </defs>
      {trail.slice(1).map((p, k) => {
        const a = trail[k];
        const dx = p.x - a.x, dy = p.y - a.y, len = Math.hypot(dx, dy);
        if (len < 1) return null;
        const ux = dx / len, uy = dy / len;
        const ra = radius(a.dur) + 6, rb = radius(p.dur) + 14;
        if (len <= ra + rb) return null;
        return (
          <line key={"l" + k} x1={a.x + ux * ra} y1={a.y + uy * ra} x2={p.x - ux * rb} y2={p.y - uy * rb}
            stroke="#fff" strokeWidth="7" strokeLinecap="round" opacity={alpha(k + 1)}
            strokeDasharray={p.afterGap ? "14 12" : undefined} markerEnd="url(#scan-trail-arrow)" />
        );
      })}
      {trail.map((p, i) => (
        <g key={"c" + i} opacity={alpha(i)} className={p.isCurrent ? "scan-trail__now" : undefined}>
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
