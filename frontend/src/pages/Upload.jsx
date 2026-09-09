import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client.js";

export default function Upload() {
  const nav = useNavigate();
  const [sessions, setSessions] = useState([]);
  const [sessionId, setSessionId] = useState("");
  const [form, setForm] = useState({ name: "", pilot_name: "", aircraft: "", sim_source: "MSFS" });
  const [files, setFiles] = useState({ flight: null, eye: null, events: null });
  const [log, setLog] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [videoFiles, setVideoFiles] = useState([]);
  const [videoLinks, setVideoLinks] = useState({
    instrument: { filename: "", offset_sec: 0 },
    otw: { filename: "", offset_sec: 0 },
  });
  const [videoBusy, setVideoBusy] = useState(null);

  useEffect(() => {
    api.listSessions().then(setSessions).catch((e) => setErr(e.message));
    api.videosAvailable().then(setVideoFiles).catch((e) => setErr(e.message));
  }, []);

  async function saveVideo(screen) {
    if (!sessionId) return setErr("Pick or create a session first.");
    setVideoBusy(screen);
    setErr(null);
    try {
      const { filename, offset_sec } = videoLinks[screen];
      await api.linkVideo(sessionId, screen, {
        filename: filename || null,
        offset_sec: Number(offset_sec) || 0,
      });
      addLog(`${screen} video: linked ${filename || "(cleared)"} @ offset ${offset_sec}s`);
    } catch (e) {
      setErr(e.message);
    } finally {
      setVideoBusy(null);
    }
  }

  function addLog(line) {
    setLog((l) => [...l, line]);
  }

  async function createSession() {
    setErr(null);
    if (!form.name.trim()) return setErr("Session name is required.");
    try {
      const s = await api.createSession(form);
      setSessions((prev) => [s, ...prev]);
      setSessionId(s.id);
      addLog(`Created session "${s.name}" (${s.id.slice(0, 8)}…)`);
    } catch (e) {
      setErr(e.message);
    }
  }

  async function uploadAll() {
    if (!sessionId) return setErr("Pick or create a session first.");
    setBusy(true);
    setErr(null);
    try {
      for (const kind of ["flight", "eye", "events"]) {
        if (files[kind]) {
          const r = await api.ingest(sessionId, kind, files[kind]);
          addLog(`${kind}: inserted ${r.inserted}, skipped ${r.skipped}`);
        }
      }
      addLog("Done. Opening review…");
      setTimeout(() => nav(`/sessions/${sessionId}/review`), 600);
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="card">
        <h2>1. Session</h2>
        <div className="row">
          <div className="col">
            <label>Use existing</label>
            <select value={sessionId} onChange={(e) => setSessionId(e.target.value)}>
              <option value="">— new session below —</option>
              {sessions.map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
          </div>
        </div>
        {!sessionId && (
          <>
            <div className="row">
              <div className="col">
                <label>Name *</label>
                <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
              </div>
              <div className="col">
                <label>Pilot</label>
                <input value={form.pilot_name} onChange={(e) => setForm({ ...form, pilot_name: e.target.value })} />
              </div>
            </div>
            <div className="row">
              <div className="col">
                <label>Aircraft</label>
                <input value={form.aircraft} onChange={(e) => setForm({ ...form, aircraft: e.target.value })} />
              </div>
              <div className="col">
                <label>Sim source</label>
                <input value={form.sim_source} onChange={(e) => setForm({ ...form, sim_source: e.target.value })} />
              </div>
            </div>
            <br />
            <button onClick={createSession}>Create session</button>
          </>
        )}
      </div>

      <div className="card">
        <h2>2. Data files (CSV)</h2>
        <div className="row">
          <div className="col">
            <label>Flight log (simconnect_log.csv)</label>
            <input type="file" accept=".csv" onChange={(e) => setFiles({ ...files, flight: e.target.files[0] })} />
          </div>
          <div className="col">
            <label>Eye tracking (Smart Eye export, .csv or raw .log)</label>
            <input type="file" accept=".csv,.log" onChange={(e) => setFiles({ ...files, eye: e.target.files[0] })} />
          </div>
          <div className="col">
            <label>Events (simconnect_events.csv)</label>
            <input type="file" accept=".csv" onChange={(e) => setFiles({ ...files, events: e.target.files[0] })} />
          </div>
        </div>
        <br />
        <button onClick={uploadAll} disabled={busy || !sessionId}>
          {busy ? "Uploading…" : "Upload & ingest"}
        </button>
      </div>

      <div className="card">
        <h2>3. Videos</h2>
        {videoFiles.length === 0 ? (
          <p className="muted">
            No video files found in the backend's video folder. Drop MP4s in there (see <code>video_dir</code> in
            config.py) and reload this page.
          </p>
        ) : (
          <div className="row">
            {["instrument", "otw"].map((screen) => (
              <div className="col" key={screen}>
                <label>{screen === "instrument" ? "Instrument screen" : "OTW screen"}</label>
                <select
                  value={videoLinks[screen].filename}
                  onChange={(e) =>
                    setVideoLinks((v) => ({ ...v, [screen]: { ...v[screen], filename: e.target.value } }))
                  }
                >
                  <option value="">— none —</option>
                  {videoFiles.map((f) => (
                    <option key={f} value={f}>{f}</option>
                  ))}
                </select>
                <label style={{ marginTop: 8 }}>Sync offset (sec)</label>
                <input
                  type="number" step="0.1"
                  value={videoLinks[screen].offset_sec}
                  onChange={(e) =>
                    setVideoLinks((v) => ({ ...v, [screen]: { ...v[screen], offset_sec: e.target.value } }))
                  }
                />
                <br />
                <button
                  style={{ marginTop: 8 }}
                  onClick={() => saveVideo(screen)}
                  disabled={!sessionId || videoBusy === screen}
                >
                  {videoBusy === screen ? "Saving…" : `Save ${screen} video`}
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {err && <p className="err">{err}</p>}
      {log.length > 0 && (
        <div className="card">
          <h3>Log</h3>
          {log.map((l, i) => (
            <div key={i} className="muted" style={{ fontFamily: "monospace", fontSize: 13 }}>{l}</div>
          ))}
        </div>
      )}
    </>
  );
}
