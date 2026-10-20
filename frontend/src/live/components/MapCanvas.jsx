import { useEffect, useRef, useState } from "react";

// Ported from the original app's drawMap()/en()/patternToLatLon()/
// onMapHover()/mapZoom() (web/static/index.html) as closely as practical:
// same runway-relative along/cross projection, same pattern rectangle,
// same track-segment/aircraft-triangle drawing, same zoom-follows-aircraft
// behaviour, same hover-to-lat/lon tooltip. Not ported: the reference-pilot
// overlay. Unlike the original, the view is centred on the planned circuit
// and can be panned by holding the left mouse button and dragging.

function drawRoundedPattern(ctx, pts, px, radiusM, sc) {
  const corners = pts.map(([a, c]) => px(a, c));
  const n = corners.length;
  ctx.beginPath();
  for (let i = 0; i < n; i++) {
    const p0 = corners[(i - 1 + n) % n], p1 = corners[i], p2 = corners[(i + 1) % n];
    const v1x = p0[0] - p1[0], v1y = p0[1] - p1[1], v2x = p2[0] - p1[0], v2y = p2[1] - p1[1];
    const len1 = Math.hypot(v1x, v1y) || 1, len2 = Math.hypot(v2x, v2y) || 1;
    const r = Math.min(radiusM * sc, len1 / 2, len2 / 2);
    const t1x = p1[0] + (v1x / len1) * r, t1y = p1[1] + (v1y / len1) * r;
    const t2x = p1[0] + (v2x / len2) * r, t2y = p1[1] + (v2y / len2) * r;
    if (i === 0) ctx.moveTo(t1x, t1y);
    else ctx.lineTo(t1x, t1y);
    ctx.quadraticCurveTo(p1[0], p1[1], t2x, t2y);
  }
  ctx.closePath();
}

export default function MapCanvas({ state }) {
  const canvasRef = useRef(null);
  const boxRef = useRef(null);
  const projRef = useRef(null); // last frame's projection, for hover inversion
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ a: 0, c: 0 }); // metres, along/cross runway
  const dragRef = useRef(null); // {x, y, pan} at drag start
  const [dragging, setDragging] = useState(false);
  const [hover, setHover] = useState(null); // {x, y, lat, lon, along, cross}

  useEffect(() => {
    const cv = canvasRef.current;
    const box = boxRef.current;
    const L = state?.lesson;
    if (!cv || !box || !L) return;

    const ctx = cv.getContext("2d");
    cv.width = box.clientWidth;
    cv.height = box.clientHeight;

    const ACC = "#4aa8ff", GOOD = "#3ddc84";
    const rw = L.runway, pat = L.pattern;
    const th = (rw.heading_true * Math.PI) / 180;
    const lat0 = rw.threshold.lat, lon0 = rw.threshold.lon;
    const side = pat.side === "left" ? 1 : -1;
    const off = pat.downwind_offset_nm * 1852;
    const clOff = pat.centerline_offset_m || 0;
    const en = (lat, lon) => {
      const n = (lat - lat0) * 111320;
      const e = (lon - lon0) * 111320 * Math.cos((lat0 * Math.PI) / 180);
      return [e * Math.sin(th) + n * Math.cos(th), side * (-e * Math.cos(th) + n * Math.sin(th)) - side * clOff];
    };

    const tel = state?.tel || {};
    const upA = rw.length_m + 420;
    const fin = L.scoring?.final_length_m || pat.base_turn_past_abeam_m || 500;
    // Flown track -- one segment per attempt (MAVIS keeps the whole flight)
    let segs = state?.track || [];
    if (segs.length && typeof segs[0][0] === "number") segs = [segs];
    // MAVIS's own demonstration flight (airborne lessons): dotted, under the
    // trainee's track, never part of it
    const demoTrk = state?.demo_track || [];

    // View centred on the planned circuit; unzoomed it widens (symmetrically)
    // to keep the aircraft and the entire flown track in view, like IRIS's
    // ground track; zoomed in it follows the aircraft.
    let cA = (upA - fin) / 2, cC = off / 2;
    let halfA = (upA + fin) / 2 + 600, halfC = off / 2 + 500;
    let acA = null, acC = null;
    if (tel.lat) [acA, acC] = en(tel.lat, tel.lon);
    if (zoom > 1 && acA != null) {
      cA = acA; cC = acC;
    } else {
      const fit = (a, c) => {
        halfA = Math.max(halfA, Math.abs(a - cA) + 300);
        halfC = Math.max(halfC, Math.abs(c - cC) + 300);
      };
      if (acA != null) fit(acA, acC);
      segs.forEach((trk) => trk.forEach((p) => fit(...en(p[0], p[1]))));
      demoTrk.forEach((p) => fit(...en(p[0], p[1])));
    }
    halfA /= zoom; halfC /= zoom;
    cA += pan.a; cC += pan.c;

    const pad = 34, w = cv.width - 2 * pad, h = cv.height - 2 * pad;
    const sc = Math.min(w / (2 * halfA), h / (2 * halfC));
    const W2 = cv.width / 2, H2 = cv.height / 2;
    const px = (a, c) => [W2 + (a - cA) * sc, H2 - (c - cC) * sc];
    projRef.current = { th, side, clOff, lat0, lon0, cA, cC, sc, W2, H2 };

    ctx.clearRect(0, 0, cv.width, cv.height);

    if (L.type === "familiarization") {
      // Airborne lessons: no circuit. Only Flight Familiarization has a
      // field to find: its dashed ring is "fly back to here", the lesson
      // ends once the aircraft is inside it -- so it's drawn only during
      // that last step. The others draw no target.
      if (L.field && state?.phase === "FIND_AIRFIELD") {
      const [fa, fc] = en(L.field.lat, L.field.lon);
      const [fx, fy] = px(fa, fc);
      ctx.strokeStyle = GOOD + "88";
      ctx.lineWidth = 2;
      ctx.setLineDash([7, 7]);
      ctx.beginPath();
      ctx.arc(fx, fy, (L.finish_radius_nm || 2) * 1852 * sc, 0, Math.PI * 2);
      ctx.stroke();
      ctx.setLineDash([]);
      }
    } else if (L.segment) {
      // Take-off / Landing: one piece of the circuit -- the climb-out straight
      // ahead off the runway, or the final approach down the extended centreline
      const [a0, a1] = L.segment === "landing" ? [-(L.final_m || 2800), 0] : [rw.length_m, upA + 1200];
      const [x0, y0] = px(a0, 0);
      const [x1, y1] = px(a1, 0);
      ctx.strokeStyle = ACC + "88";
      ctx.lineWidth = 2;
      ctx.setLineDash([7, 7]);
      ctx.beginPath();
      ctx.moveTo(x0, y0);
      ctx.lineTo(x1, y1);
      ctx.stroke();
      ctx.setLineDash([]);
    } else {
      // Planned circuit (dashed rounded rectangle)
      ctx.strokeStyle = ACC + "55";
      ctx.lineWidth = 2;
      ctx.setLineDash([7, 7]);
      drawRoundedPattern(ctx, [[0, 0], [upA, 0], [upA, off], [-fin, off], [-fin, 0]], px, 220, sc);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // Runway
    const [r1x, r1y] = px(0, 0);
    const [r2x, r2y] = px(rw.length_m, 0);
    ctx.strokeStyle = state?.night ? "#e8d24a" : "#cdd9ec";
    ctx.lineWidth = 8;
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(r1x, r1y);
    ctx.lineTo(r2x, r2y);
    ctx.stroke();
    ctx.fillStyle = "#8fa3c4";
    ctx.font = "14px system-ui";
    ctx.fillText("RWY " + rw.ident, r1x - 16, r1y + 22);

    // Short-Field: the aim point (touch down here) and the line to stop before
    if (L.short_field) {
      const [ax, ay] = px(L.short_field.aim_point_m, 0);
      ctx.fillStyle = GOOD;
      ctx.beginPath();
      ctx.arc(ax, ay, 6, 0, Math.PI * 2);
      ctx.fill();
      const [s1x, s1y] = px(L.short_field.stop_by_m, 90);
      const [s2x, s2y] = px(L.short_field.stop_by_m, -90);
      ctx.strokeStyle = "#ff5c5c";
      ctx.lineWidth = 4;
      ctx.beginPath();
      ctx.moveTo(s1x, s1y);
      ctx.lineTo(s2x, s2y);
      ctx.stroke();
      ctx.fillStyle = "#ff8a8a";
      ctx.font = "12px system-ui";
      ctx.fillText("stop before", s1x + 6, s1y - 4);
      ctx.fillStyle = GOOD;
      ctx.fillText("aim", ax - 10, ay - 10);
    }

    // MAVIS's demonstration: a dotted line
    if (demoTrk.length > 1) {
      ctx.strokeStyle = "rgba(205, 217, 236, 0.75)";
      ctx.lineWidth = 2;
      ctx.lineCap = "round";
      ctx.setLineDash([2, 6]);
      ctx.beginPath();
      demoTrk.forEach((p, i) => {
        const [q, r] = px(...en(p[0], p[1]));
        i ? ctx.lineTo(q, r) : ctx.moveTo(q, r);
      });
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // Flown track -- old attempts dim/dashed
    segs.forEach((trk, si) => {
      if (trk.length < 2) return;
      const cur = si === segs.length - 1;
      ctx.strokeStyle = cur ? GOOD : "rgba(143,163,196,.45)";
      ctx.lineWidth = cur ? 2.5 : 1.6;
      ctx.setLineDash(cur ? [] : [5, 6]);
      ctx.shadowColor = cur ? GOOD : "transparent";
      ctx.shadowBlur = cur ? 6 : 0;
      ctx.beginPath();
      trk.forEach((p, i) => {
        const [a, c] = en(p[0], p[1]);
        const [q, r] = px(a, c);
        i ? ctx.lineTo(q, r) : ctx.moveTo(q, r);
      });
      ctx.stroke();
    });
    ctx.setLineDash([]);
    ctx.shadowBlur = 0;

    // Aircraft
    if (tel.lat) {
      const [a, c] = en(tel.lat, tel.lon);
      const [q, r] = px(a, c);
      const hd = ((tel.hdg - rw.heading_true) * Math.PI) / 180 * side + Math.PI / 2;
      ctx.save();
      ctx.translate(q, r);
      ctx.rotate(hd);
      ctx.fillStyle = "#ffb64d";
      ctx.shadowColor = ctx.fillStyle;
      ctx.shadowBlur = 10;
      ctx.beginPath();
      ctx.moveTo(0, -10);
      ctx.lineTo(7, 8);
      ctx.lineTo(0, 3.5);
      ctx.lineTo(-7, 8);
      ctx.closePath();
      ctx.fill();
      ctx.restore();
    }
  }, [state?.tel, state?.track, state?.demo_track, state?.lesson, state?.night, state?.phase, zoom, pan]);

  function patternToLatLon(along, cross) {
    const proj = projRef.current;
    if (!proj) return null;
    const { th, side, clOff, lat0, lon0 } = proj;
    const cp = cross + side * clOff, s = Math.sin(th), co = Math.cos(th);
    const e = s * along - (co * cp) / side, n = co * along + (s * cp) / side;
    const lat = lat0 + n / 111320;
    const lon = lon0 + e / (111320 * Math.cos((lat0 * Math.PI) / 180));
    return { lat, lon };
  }

  // Hold left click and drag to pan; screen pixels -> metres at the
  // current scale (screen y is flipped relative to cross-track).
  function onPointerDown(ev) {
    if (ev.button !== 0) return;
    ev.currentTarget.setPointerCapture(ev.pointerId);
    dragRef.current = { x: ev.clientX, y: ev.clientY, pan };
    setDragging(true);
    setHover(null);
  }
  function onPointerUp(ev) {
    if (!dragRef.current) return;
    ev.currentTarget.releasePointerCapture?.(ev.pointerId);
    dragRef.current = null;
    setDragging(false);
  }

  function onPointerMove(ev) {
    const proj = projRef.current;
    const cv = canvasRef.current;
    if (!proj || !cv) return;
    const d = dragRef.current;
    if (d) {
      setPan({ a: d.pan.a - (ev.clientX - d.x) / proj.sc, c: d.pan.c + (ev.clientY - d.y) / proj.sc });
      return;
    }
    const rect = cv.getBoundingClientRect();
    const x = ev.clientX - rect.left, y = ev.clientY - rect.top;
    const { cA, cC, sc, W2, H2 } = proj;
    const along = cA + (x - W2) / sc;
    const cross = cC - (y - H2) / sc;
    const ll = patternToLatLon(along, cross);
    if (!ll) return setHover(null);
    setHover({ x, y, lat: ll.lat, lon: ll.lon });
  }

  return (
    <div className="mavis-map" ref={boxRef}>
      <canvas
        ref={canvasRef}
        className={dragging ? "is-dragging" : undefined}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onPointerLeave={() => setHover(null)}
      />
      <div className="mavis-map__zoom">
        <button type="button" title="Zoom in" onClick={() => setZoom((z) => Math.min(8, z * 1.5))}>+</button>
        <button type="button" title="Zoom out" onClick={() => setZoom((z) => Math.max(1, z / 1.5))}>−</button>
        <button type="button" title="Re-centre" onClick={() => { setPan({ a: 0, c: 0 }); setZoom(1); }}>⌖</button>
      </div>
      {hover && (
        <div className="mavis-map__hover" style={{ left: hover.x + 12, top: hover.y + 12 }}>
          {hover.lat.toFixed(5)}°, {hover.lon.toFixed(5)}°
        </div>
      )}
    </div>
  );
}
