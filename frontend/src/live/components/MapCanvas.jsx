import { useEffect, useRef } from "react";

// Ported from the original app's drawMap()/en() (web/static/index.html)
// as closely as practical: same runway-relative along/cross projection,
// same pattern rectangle, same track-segment/aircraft-triangle drawing.
// Not ported: reference-pilot overlay, hover tooltip, manual zoom/pan --
// this always fits the whole flown track in view.

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
    let a0 = -2200, a1 = upA + 600, c0 = -400, c1 = off + 500;
    let acA = null, acC = null;
    if (tel.lat) [acA, acC] = en(tel.lat, tel.lon);
    if (acA != null) {
      a0 = Math.min(a0, acA - 400); a1 = Math.max(a1, acA + 400);
      c0 = Math.min(c0, acC - 400); c1 = Math.max(c1, acC + 400);
    }

    const pad = 34, w = cv.width - 2 * pad, h = cv.height - 2 * pad;
    const sc = Math.min(w / (a1 - a0), h / (c1 - c0));
    const px = (a, c) => [pad + (a - a0) * sc, cv.height - pad - (c - c0) * sc];

    ctx.clearRect(0, 0, cv.width, cv.height);

    // Planned circuit (dashed rounded rectangle)
    ctx.strokeStyle = ACC + "55";
    ctx.lineWidth = 2;
    ctx.setLineDash([7, 7]);
    const fin = L.scoring?.final_length_m || pat.base_turn_past_abeam_m || 500;
    drawRoundedPattern(ctx, [[0, 0], [upA, 0], [upA, off], [-fin, off], [-fin, 0]], px, 220, sc);
    ctx.stroke();
    ctx.setLineDash([]);

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

    // Flown track -- one segment per attempt, old attempts dim/dashed
    let segs = state?.track || [];
    if (segs.length && typeof segs[0][0] === "number") segs = [segs];
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
  }, [state?.tel, state?.track, state?.lesson, state?.night]);

  return (
    <div className="mavis-map" ref={boxRef}>
      <canvas ref={canvasRef} />
    </div>
  );
}
