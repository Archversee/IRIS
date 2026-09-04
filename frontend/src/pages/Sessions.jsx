import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";

export default function Sessions() {
  const [sessions, setSessions] = useState([]);
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    try {
      setSessions(await api.listSessions());
      setErr(null);
    } catch (e) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function remove(id) {
    if (!confirm("Delete this session and all its data?")) return;
    await api.deleteSession(id);
    load();
  }

  return (
    <div className="card">
      <h2>Sessions</h2>
      {err && <p className="err">Could not reach API: {err}</p>}
      {loading ? (
        <p className="muted">Loading…</p>
      ) : sessions.length === 0 ? (
        <p className="muted">
          No sessions yet. <Link to="/upload">Upload some data →</Link>
        </p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Pilot</th>
              <th>Aircraft</th>
              <th>Sim</th>
              <th>Started</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {sessions.map((s) => (
              <tr key={s.id}>
                <td>{s.name}</td>
                <td>{s.pilot_name || "—"}</td>
                <td>{s.aircraft || "—"}</td>
                <td>{s.sim_source || "—"}</td>
                <td>{s.started_at ? new Date(s.started_at).toLocaleString() : "—"}</td>
                <td style={{ whiteSpace: "nowrap" }}>
                  <Link to={`/sessions/${s.id}/review`}>Review</Link>{" · "}
                  <Link to={`/sessions/${s.id}/analytics`}>Analytics</Link>{" · "}
                  <a href="#" onClick={(e) => { e.preventDefault(); remove(s.id); }}>
                    Delete
                  </a>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
