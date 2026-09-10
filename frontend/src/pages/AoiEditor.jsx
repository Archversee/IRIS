import { useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";

// Must match the instrument recording's native resolution -- this is the
// same pixel space Smart Eye reports gaze_point_x/y in, so zones drawn
// here line up directly with gaze samples with no scaling.
const NATIVE_W = 1920;
const NATIVE_H = 1080;

export default function AoiEditor() {
  const [sessions, setSessions] = useState([]);
  const [sessionId, setSessionId] = useState("");
  const [videoUrl, setVideoUrl] = useState(null);
  const [frameReady, setFrameReady] = useState(false);
  const [zones, setZones] = useState([]);
  const [dragBox, setDragBox] = useState(null);
  const [err, setErr] = useState(null);
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const dragStart = useRef(null);

  useEffect(() => {
    api.listSessions().then(setSessions).catch((e) => setErr(e.message));
    reloadZones();
  }, []);

  function reloadZones() {
    api.listAoiZones().then(setZones).catch((e) => setErr(e.message));
  }

  async function pickSession(id) {
    setSessionId(id);
    setFrameReady(false);
    setErr(null);
    if (!id) return setVideoUrl(null);
    try {
      const s = await api.summary(id);
      const url = s.session.instrument_video_url;
      if (!url) setErr("This session has no instrument recording linked.");
      setVideoUrl(url || null);
    } catch (e) {
      setErr(e.message);
    }
  }

  function captureFrame() {
    const video = videoRef.current, canvas = canvasRef.current;
    if (!video || !canvas) return;
    canvas.width = NATIVE_W;
    canvas.height = NATIVE_H;
    try {
      canvas.getContext("2d").drawImage(video, 0, 0, NATIVE_W, NATIVE_H);
      setFrameReady(true);
      setErr(null);
    } catch {
      setErr("Couldn't capture a frame from this video (likely a cross-origin issue).");
    }
  }

  function toNative(clientX, clientY) {
    const rect = canvasRef.current.getBoundingClientRect();
    const x = ((clientX - rect.left) / rect.width) * NATIVE_W;
    const y = ((clientY - rect.top) / rect.height) * NATIVE_H;
    return [Math.max(0, Math.min(NATIVE_W, x)), Math.max(0, Math.min(NATIVE_H, y))];
  }

  function onMouseDown(e) {
    if (!frameReady) return;
    const [x, y] = toNative(e.clientX, e.clientY);
    dragStart.current = { x1: x, y1: y };
    setDragBox({ x1: x, y1: y, x2: x, y2: y });
  }

  function onMouseMove(e) {
    if (!dragStart.current) return;
    const [x, y] = toNative(e.clientX, e.clientY);
    setDragBox({ ...dragStart.current, x2: x, y2: y });
  }

  async function onMouseUp() {
    const box = dragBox;
    dragStart.current = null;
    setDragBox(null);
    if (!box) return;
    if (Math.abs(box.x2 - box.x1) < 8 || Math.abs(box.y2 - box.y1) < 8) return; // ignore accidental clicks
    const name = window.prompt("Name this zone (e.g. Airspeed, Altimeter):");
    if (!name) return;
    try {
      await api.createAoiZone({ ...box, name });
      reloadZones();
    } catch (e) {
      setErr(e.message);
    }
  }

  async function removeZone(id) {
    try {
      await api.deleteAoiZone(id);
      reloadZones();
    } catch (e) {
      setErr(e.message);
    }
  }

  return (
    <div className="card">
      <h2>Instrument AOI zones</h2>
      <p className="muted">
        Pick a session with an instrument recording, capture a frame, then drag rectangles over
        each dial you want tracked separately (e.g. Airspeed, Altimeter). Zones are shared across
        all sessions, since the panel layout doesn't change between recordings.
      </p>

      <div className="row">
        <div className="col">
          <label>Session</label>
          <select value={sessionId} onChange={(e) => pickSession(e.target.value)}>
            <option value="">— pick a session —</option>
            {sessions.map((s) => (
              <option key={s.id} value={s.id}>{s.name}</option>
            ))}
          </select>
        </div>
      </div>

      {videoUrl && (
        <>
          <video ref={videoRef} src={videoUrl} style={{ display: "none" }}
            crossOrigin="anonymous" muted playsInline
            onLoadedData={captureFrame} />
          <div style={{ margin: "10px 0" }}>
            <button onClick={captureFrame}>Recapture current frame</button>
          </div>
        </>
      )}

      {frameReady && (
        <div
          style={{ position: "relative", width: "100%", maxWidth: 960, aspectRatio: "16 / 9", cursor: "crosshair" }}
          onMouseDown={onMouseDown} onMouseMove={onMouseMove} onMouseUp={onMouseUp} onMouseLeave={onMouseUp}
        >
          <canvas ref={canvasRef} style={{ width: "100%", height: "100%", display: "block", borderRadius: 8 }} />
          <svg viewBox={`0 0 ${NATIVE_W} ${NATIVE_H}`} preserveAspectRatio="none"
            style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}>
            {zones.map((z) => (
              <g key={z.id}>
                <rect x={Math.min(z.x1, z.x2)} y={Math.min(z.y1, z.y2)}
                  width={Math.abs(z.x2 - z.x1)} height={Math.abs(z.y2 - z.y1)}
                  fill="rgba(56,189,248,0.15)" stroke="#38bdf8" strokeWidth="3" />
                <text x={Math.min(z.x1, z.x2) + 6} y={Math.min(z.y1, z.y2) + 26}
                  fill="#38bdf8" fontSize="26">{z.name}</text>
              </g>
            ))}
            {dragBox && (
              <rect x={Math.min(dragBox.x1, dragBox.x2)} y={Math.min(dragBox.y1, dragBox.y2)}
                width={Math.abs(dragBox.x2 - dragBox.x1)} height={Math.abs(dragBox.y2 - dragBox.y1)}
                fill="rgba(255,255,255,0.15)" stroke="#fff" strokeWidth="2" strokeDasharray="6 4" />
            )}
          </svg>
        </div>
      )}

      <div className="card" style={{ marginTop: 16 }}>
        <h3>Zones ({zones.length})</h3>
        {zones.length === 0 ? (
          <p className="muted">No zones defined yet.</p>
        ) : (
          <table>
            <tbody>
              {zones.map((z) => (
                <tr key={z.id}>
                  <td>{z.name}</td>
                  <td className="muted">
                    ({z.x1.toFixed(0)}, {z.y1.toFixed(0)}) – ({z.x2.toFixed(0)}, {z.y2.toFixed(0)})
                  </td>
                  <td><button onClick={() => removeZone(z.id)}>Delete</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {err && <p className="err">{err}</p>}
    </div>
  );
}
