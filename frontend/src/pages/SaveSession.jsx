import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client.js";
import { PilotPicker, usePilotOptions } from "../components/SessionFormParts.jsx";
import { useMavisState } from "../live/hooks/useMavisSocket.js";

// End of a MAVIS flight ("Finish & save" on the scorecard): name the
// session, pick the pilot, then Next -> Review. The summary is prefilled
// with the scorecard's own debrief text (MAVIS's live state), and can be
// edited. No CSV upload or video linking here -- MAVIS already logged the
// flight data, and the flight's OBS recordings are added to the review by
// themselves (FlightVideos shows how that's going; Next waits for them).

const SCREENS = [["instrument", "Instrument screen"], ["otw", "OTW screen"]];
const NO_RECORDING_MS = 30000;   // none reported by then: the flight wasn't recorded

// How the flight's videos are coming along. pending: at least one is still on
// its way -- Next waits for it. A video that failed, or that was never recorded,
// doesn't hold the page up.
function useFlightVideos(id) {
  const [screens, setScreens] = useState(null);
  const [gaveUp, setGaveUp] = useState(false);
  const [pending, setPending] = useState(true);
  useEffect(() => {
    let alive = true, timer, last = null;
    const opened = Date.now();
    const poll = async () => {
      let gone = false;
      try {
        last = await api.recordings(id);
      } catch (e) {
        gone = e.status === 404;     // no such session; anything else: try again
      }
      if (!alive) return;
      const late = Date.now() - opened > NO_RECORDING_MS;
      const states = last && Object.values(last).map((s) => s.state);
      const more = !gone && (states ? states.some((s) => s === "processing" || (s === "waiting" && !late)) : !late);
      setScreens(last);
      setGaveUp(late || gone);
      setPending(more);
      if (more) timer = setTimeout(poll, 2000);
    };
    setPending(true);
    poll();
    return () => { alive = false; clearTimeout(timer); };
  }, [id]);
  return { screens, gaveUp, pending };
}

function FlightVideos({ screens, gaveUp, pending }) {
  const line = (s) => {
    if (s?.state === "linked") return ["ok", "Added to the review"];
    if (s?.state === "processing") return ["busy", "Saving the recording…"];
    if (s?.state === "failed") return ["bad", s.detail];
    if (gaveUp) return ["bad", "No recording received. Check that OBS and the adapter are running."];
    return ["busy", "Waiting for the recording…"];
  };

  return (
    <div className="card">
      <h2>Flight videos</h2>
      <ul className="save-session__videos" role="status">
        {SCREENS.map(([key, label]) => {
          const [look, text] = line(screens?.[key]);
          return <li key={key} className={`is-${look}`}><b>{label}</b><span>{text}</span></li>;
        })}
      </ul>
      <p className="muted">
        {pending ? "You can continue once the videos are saved." : "Videos are added to the review automatically."}
      </p>
    </div>
  );
}

export default function SaveSession() {
  const { id } = useParams();
  const nav = useNavigate();
  const [sessions, setSessions] = useState([]);
  const [details, setDetails] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const pilots = usePilotOptions(sessions);
  const videos = useFlightVideos(id);
  // the scorecard of this session's finished flight, while MAVIS still holds it
  const mavis = useMavisState();
  const card = mavis?.session_id === id ? mavis?.scorecard : null;
  // the summary is filled in once, and never over what was already saved or typed
  const notesTouched = useRef(false);

  useEffect(() => {
    api.getSession(id)
      .then((s) => {
        if (s.notes) notesTouched.current = true;
        setDetails({
          name: s.name || "", pilot_name: s.pilot_name || "", aircraft: s.aircraft || "",
          sim_source: s.sim_source || "", notes: s.notes || "",
        });
      })
      .catch((e) => setErr(e.status === 404 ? "This session no longer exists." : e.message));
    api.listSessions().then(setSessions).catch(() => {});
  }, [id]);

  const debrief = (card?.debrief || "").trim();
  useEffect(() => {
    if (!details || !debrief || notesTouched.current) return;
    notesTouched.current = true;
    setDetails((d) => (d.notes ? d : { ...d, notes: debrief }));
  }, [details, debrief]);

  const set = (k) => (e) => setDetails((d) => ({ ...d, [k]: e.target.value }));
  const setNotes = (e) => {
    notesTouched.current = true;
    set("notes")(e);
  };

  async function next() {
    if (!details.name.trim()) return setErr("Session name is required.");
    setBusy(true);
    setErr(null);
    try {
      await api.updateSession(id, { ...details, name: details.name.trim(), pilot_name: details.pilot_name.trim() });
      // the AI debrief (summary + a note per broken rule) is written in the background if
      // config.yaml's debrief.auto_on_save is on (ending the flight and opening Review start
      // it too; it only fills in what's missing); Review → Debrief shows its progress
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
          <p className="muted">Add the details for this flight, then continue to the review.</p>
        </div>
        <span className="save-session__id" title={id}>#{id.slice(0, 8)}</span>
      </header>

      <div className="card">
        <h2>Session details</h2>
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
        <label>Aircraft</label>
        <input value={details.aircraft} onChange={set("aircraft")} />
        <label>Summary</label>
        <textarea rows={5} value={details.notes} onChange={setNotes}
          placeholder={card?.debrief_pending && !notesTouched.current ? "Writing the summary from your score…" : ""} />
      </div>

      <FlightVideos {...videos} />

      {err && <p className="err">{err}</p>}
      <div className="save-session__actions">
        <button type="button" className="next-btn" onClick={next} disabled={busy || videos.pending}>
          {busy ? "Saving…" : videos.pending ? "Saving videos…" : "Next › Review"}
        </button>
      </div>
    </div>
  );
}
