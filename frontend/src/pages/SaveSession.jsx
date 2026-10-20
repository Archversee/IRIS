import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client.js";
import { EMPTY_LINKS, PilotPicker, VideoLinker, saveVideoLinks, usePilotOptions } from "../components/SessionFormParts.jsx";

// End of a MAVIS flight ("Finish & save" on the scorecard): name the
// session, pick the pilot, link its recordings, then Next -> Review.
// No CSV upload here -- MAVIS already logged the flight data; that's the
// separate Upload page's job.
export default function SaveSession() {
  const { id } = useParams();
  const nav = useNavigate();
  const [sessions, setSessions] = useState([]);
  const [details, setDetails] = useState(null);
  const [videoFiles, setVideoFiles] = useState([]);
  const [links, setLinks] = useState(EMPTY_LINKS);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const pilots = usePilotOptions(sessions);

  useEffect(() => {
    api.getSession(id)
      .then((s) => setDetails({
        name: s.name || "", pilot_name: s.pilot_name || "", aircraft: s.aircraft || "",
        sim_source: s.sim_source || "", notes: s.notes || "",
      }))
      .catch((e) => setErr(e.status === 404 ? "This session no longer exists." : e.message));
    api.listSessions().then(setSessions).catch(() => {});
    api.videosAvailable().then(setVideoFiles).catch(() => {});
  }, [id]);

  const set = (k) => (e) => setDetails((d) => ({ ...d, [k]: e.target.value }));

  async function next() {
    if (!details.name.trim()) return setErr("Session name is required.");
    setBusy(true);
    setErr(null);
    try {
      await api.updateSession(id, { ...details, name: details.name.trim(), pilot_name: details.pilot_name.trim() });
      await saveVideoLinks(id, links);
      // the AI debrief (summary + a note per broken rule) is written in the background
      // if config.yaml's debrief.auto_on_save is on; Review → Debrief shows its progress
      api.runDebrief(id, { auto: true }).catch(() => {});
      nav(`/sessions/${id}/review`);
    } catch (e) {
      setErr(e.detail || e.message);
      setBusy(false);
    }
  }

  if (!details) {
    return <div className="container">{err ? <p className="err">{err}</p> : <p className="muted">Loading session…</p>}</div>;
  }

  return (
    <div className="container save-session">
      <header className="save-session__head">
        <div>
          <h1>Save session</h1>
          <p className="muted">Add the details for this flight, link its recordings, then continue to the review.</p>
        </div>
        <span className="save-session__id" title={id}>#{id.slice(0, 8)}</span>
      </header>

      <div className="card">
        <h2>1. Session details</h2>
        <div className="row">
          <div className="col">
            <label>Name *</label>
            <input value={details.name} onChange={set("name")} autoFocus />
          </div>
          <div className="col">
            <label>Pilot</label>
            <PilotPicker value={details.pilot_name} options={pilots}
              onChange={(v) => setDetails((d) => ({ ...d, pilot_name: v }))} />
          </div>
        </div>
        <div className="row">
          <div className="col">
            <label>Aircraft</label>
            <input value={details.aircraft} onChange={set("aircraft")} />
          </div>
          <div className="col">
            <label>Sim source</label>
            <input value={details.sim_source} onChange={set("sim_source")} />
          </div>
        </div>
        <label>Notes</label>
        <textarea rows={3} value={details.notes} onChange={set("notes")} />
      </div>

      <div className="card">
        <h2>2. Videos</h2>
        <VideoLinker files={videoFiles} links={links} setLinks={setLinks} />
      </div>

      {err && <p className="err">{err}</p>}
      <div className="save-session__actions">
        <button type="button" className="next-btn" onClick={next} disabled={busy}>
          {busy ? "Saving…" : "Next › Review"}
        </button>
      </div>
    </div>
  );
}
