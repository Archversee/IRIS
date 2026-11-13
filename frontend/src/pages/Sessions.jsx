import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";
import Pager from "../components/Pager.jsx";

const POLL_INTERVAL_MS = 5000;
const PAGE_SIZE = 10;

export default function Sessions() {
  const [sessions, setSessions] = useState([]);
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);

  // `quiet` refreshes in place (background poll) without the Loading… flash.
  async function load({ quiet = false } = {}) {
    if (!quiet) setLoading(true);
    try {
      // Sessions MAVIS is still flying belong to Live Monitor, not here --
      // they show up in this list once they've ended.
      const [all, running] = await Promise.all([api.listSessions(), api.listRunningSessions()]);
      const live = new Set(running.map((r) => r.id));
      setSessions(all.filter((s) => !live.has(s.id)));
      setErr(null);
    } catch (e) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  }

  // Re-check every few seconds so a session MAVIS ends (and its save
  // details) shows up here without a reload.
  useEffect(() => {
    load();
    const t = setInterval(() => load({ quiet: true }), POLL_INTERVAL_MS);
    return () => clearInterval(t);
  }, []);

  async function remove(id) {
    if (!confirm("Delete this session and all its data, including its video recordings? This can't be undone.")) return;
    await api.deleteSession(id);
    load();
  }

  // Newest first (the API's order), PAGE_SIZE per page. If the list shrinks
  // (a delete, or the last row of the last page going away) step back to the
  // last page that still exists rather than showing an empty one.
  const pages = Math.max(1, Math.ceil(sessions.length / PAGE_SIZE));
  const shownPage = Math.min(page, pages);
  const pageRows = sessions.slice((shownPage - 1) * PAGE_SIZE, shownPage * PAGE_SIZE);

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
              <th>Curriculum</th>
              <th>Aircraft</th>
              <th>Sim</th>
              <th>Started</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {pageRows.map((s) => (
              <tr key={s.id}>
                <td>{s.name}</td>
                <td>{s.pilot_name || "—"}</td>
                <td>{s.curricula?.length ? s.curricula.join(", ") : "—"}</td>
                <td>{s.aircraft || "—"}</td>
                <td>{s.sim_source || "—"}</td>
                <td>{s.started_at ? new Date(s.started_at).toLocaleString() : "—"}</td>
                <td style={{ whiteSpace: "nowrap" }}>
                  <Link to={`/sessions/${s.id}/review`}>Review</Link>{" · "}
                  <a href="#" onClick={(e) => { e.preventDefault(); remove(s.id); }}>
                    Delete
                  </a>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {!loading && (
        <Pager page={shownPage} pageSize={PAGE_SIZE} total={sessions.length} shown={pageRows.length}
          onPage={setPage} label="Session pages" />
      )}
    </div>
  );
}
