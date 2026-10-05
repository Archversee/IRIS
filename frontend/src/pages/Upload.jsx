import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client.js";
import { EMPTY_LINKS, PilotPicker, VideoLinker, saveVideoLinks, usePilotOptions } from "../components/SessionFormParts.jsx";

const EMPTY_DETAILS = { name: "", pilot_name: "", aircraft: "", sim_source: "", notes: "" };

// Manual upload: pick or create a session, upload its CSV data files, link
// videos, then Next -> Review. (A finished MAVIS flight goes to the Save
// Session page instead -- no CSV step there, MAVIS already logged it.)
export default function Upload() {
  const nav = useNavigate();
  const [searchParams] = useSearchParams();
  const [sessions, setSessions] = useState([]);
  const [sessionId, setSessionId] = useState(searchParams.get("session") || "");
  const [form, setForm] = useState({ name: "", pilot_name: "", aircraft: "", sim_source: "MSFS" });
  const [details, setDetails] = useState(EMPTY_DETAILS);
  const [detailsBusy, setDetailsBusy] = useState(false);
  const [files, setFiles] = useState({ flight: null, eye: null });
  const [log, setLog] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [videoFiles, setVideoFiles] = useState([]);
  const [videoLinks, setVideoLinks] = useState(EMPTY_LINKS);
  const [videoBusy, setVideoBusy] = useState(false);
  const [nextBusy, setNextBusy] = useState(false);
  const pilots = usePilotOptions(sessions);

  useEffect(() => {
    api.listSessions().then(setSessions).catch((e) => setErr(e.message));
    api.videosAvailable().then(setVideoFiles).catch((e) => setErr(e.message));
  }, []);

  // Keep the details form in sync with whichever existing session is picked.
  useEffect(() => {
    if (!sessionId) return setDetails(EMPTY_DETAILS);
    const s = sessions.find((s) => s.id === sessionId);
    if (s) {
      setDetails({
        name: s.name || "", pilot_name: s.pilot_name || "",
        aircraft: s.aircraft || "", sim_source: s.sim_source || "", notes: s.notes || "",
      });
    }
  }, [sessionId, sessions]);

  async function saveDetails() {
    setDetailsBusy(true);
    setErr(null);
    try {
      const updated = await api.updateSession(sessionId, details);
      setSessions((prev) => prev.map((s) => (s.id === sessionId ? updated : s)));
      addLog("Session details saved.");
    } catch (e) {
      setErr(e.message);
    } finally {
      setDetailsBusy(false);
    }
  }

  async function saveVideos() {
    if (!sessionId) return setErr("Pick or create a session first.");
    setVideoBusy(true);
    setErr(null);
    try {
      await saveVideoLinks(sessionId, videoLinks);
      addLog("Videos linked.");
    } catch (e) {
      setErr(e.message);
    } finally {
      setVideoBusy(false);
    }
  }

  // Next: save the details + any chosen videos, then open the review.
  async function next() {
    if (!sessionId) return setErr("Pick or create a session first.");
    setNextBusy(true);
    setErr(null);
    try {
      await api.updateSession(sessionId, details);
      await saveVideoLinks(sessionId, videoLinks);
      nav(`/sessions/${sessionId}/review`);
    } catch (e) {
      setErr(e.message);
      setNextBusy(false);
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
      for (const kind of ["flight", "eye"]) {
        if (files[kind]) {
          const r = await api.ingest(sessionId, kind, files[kind]);
          addLog(`${kind}: inserted ${r.inserted}, skipped ${r.skipped}`);
        }
      }
      //addLog("Done. Opening review…");
      //setTimeout(() => nav(`/sessions/${sessionId}/review`), 600);
      addLog("Done. You can now link your videos below, or head to Review when ready.");
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
                <PilotPicker value={form.pilot_name} options={pilots}
                  onChange={(v) => setForm({ ...form, pilot_name: v })} />
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
        {sessionId && (
          <>
            <div className="row">
              <div className="col">
                <label>Name</label>
                <input value={details.name} onChange={(e) => setDetails({ ...details, name: e.target.value })} />
              </div>
              <div className="col">
                <label>Pilot</label>
                <PilotPicker value={details.pilot_name} options={pilots}
                  onChange={(v) => setDetails({ ...details, pilot_name: v })} />
              </div>
            </div>
            <div className="row">
              <div className="col">
                <label>Aircraft</label>
                <input value={details.aircraft} onChange={(e) => setDetails({ ...details, aircraft: e.target.value })} />
              </div>
              <div className="col">
                <label>Sim source</label>
                <input value={details.sim_source} onChange={(e) => setDetails({ ...details, sim_source: e.target.value })} />
              </div>
            </div>
            <div className="row">
              <div className="col" style={{ flex: 1 }}>
                <label>Notes</label>
                <textarea rows={3} value={details.notes} onChange={(e) => setDetails({ ...details, notes: e.target.value })} />
              </div>
            </div>
            <br />
            <button onClick={saveDetails} disabled={detailsBusy}>
              {detailsBusy ? "Saving…" : "Save details"}
            </button>
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
        </div>
        <br />
        <button onClick={uploadAll} disabled={busy || !sessionId}>
          {busy ? "Uploading…" : "Upload & ingest"}
        </button>
      </div>

      <div className="card">
        <h2>3. Videos</h2>
        <VideoLinker files={videoFiles} links={videoLinks} setLinks={setVideoLinks} />
        {videoFiles.length > 0 && (
          <button style={{ marginTop: 12 }} onClick={saveVideos} disabled={!sessionId || videoBusy}>
            {videoBusy ? "Saving…" : "Save videos"}
          </button>
        )}
      </div>

      {err && <p className="err">{err}</p>}
      <div className="save-session__actions">
        <button type="button" className="next-btn" onClick={next} disabled={!sessionId || nextBusy}
          title={sessionId ? "Save and open the session review" : "Pick or create a session first"}>
          {nextBusy ? "Saving…" : "Next › Review"}
        </button>
      </div>
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
