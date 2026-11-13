import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import Pager from "../components/Pager.jsx";
import { ScoringGuideContent } from "../live/components/ScoringGuide.jsx";
import "../live/live.css";

// MAVIS reference pages -- Leaderboard and Scoring Guide (opened from the
// Settings drawer) and the Controls guide -- ported from the original MAVIS
// page's pop-ups (#boardView, #guideMenuView, #infoModal in web/static/index.html).

function GuidePage({ title, sub, wide, children }) {
  return (
    <div className="mg-page">
      <div className={"mg-card" + (wide ? " mg-card--wide" : "")}>
        <header className="mg-head">
          <h1>{title}</h1>
          {sub && <p>{sub}</p>}
        </header>
        {children}
      </div>
    </div>
  );
}

// ---- Leaderboard: every posted run, from IRIS's Postgres (routers/leaderboard.py) ----
// One board per mode; the search matches name, curriculum or aircraft.
const MODES = [["visitor", "Visitor"], ["trainee", "Trainee"], ["pilot", "Pilot"]];
const PAGE_SIZE = 20;

export function LeaderboardPage() {
  const [query, setQuery] = useState("");
  const [q, setQ] = useState("");             // debounced query actually sent
  const [mode, setMode] = useState("visitor");
  const [page, setPage] = useState(1);
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [reload, setReload] = useState(0);
  const { user } = useAuth();
  // pilots may delete their own runs (the account they flew under), admins any run
  const canDelete = (e) => !!user && (user.role === "admin" || (e.user_id && e.user_id === user.id));

  async function remove(e) {
    if (!confirm(`Delete ${e.pilot}'s score of ${e.total} on ${e.circuit || "this curriculum"}? This can't be undone.`)) return;
    try {
      await api.deleteLeaderboardEntry(e.id);
      // last row on a later page gone: step back a page
      if (rows.length === 1 && page > 1) setPage(page - 1);
      else setReload((n) => n + 1);
    } catch (x) {
      alert(`Couldn't delete it: ${x.detail || x.message}`);
    }
  }

  useEffect(() => {
    const t = setTimeout(() => { setQ(query.trim()); setPage(1); }, 300);
    return () => clearTimeout(t);
  }, [query]);

  useEffect(() => {
    let cancelled = false;
    api.leaderboard({ q, mode, page, pageSize: PAGE_SIZE })
      .then((d) => { if (!cancelled) { setData(d); setErr(null); } })
      .catch((e) => { if (!cancelled) setErr(e.detail || e.message); });
    return () => { cancelled = true; };
  }, [q, mode, page, reload]);

  const rows = data?.rows || [];
  const total = data?.total || 0;
  // action column: View (rows linked to a session) and Delete (own runs / admin)
  const anyActions = rows.some((e) => e.session_id || canDelete(e));

  return (
    <GuidePage title="Leaderboard" sub="Every flight posted from MAVIS, ranked by score." wide>
      <div className="mg-tabs">
        <div className="mg-seg">
          {MODES.map(([v, l]) => (
            <button key={v} type="button" className={mode === v ? "is-on" : ""}
              onClick={() => { setMode(v); setPage(1); }}>{l}</button>
          ))}
        </div>
        <input type="search" className="mg-search" value={query} onChange={(e) => setQuery(e.target.value)}
          placeholder="Search name, curriculum or aircraft…" aria-label="Search the leaderboard" />
      </div>

      {err ? (
        <div className="mg-empty">Couldn't load the leaderboard: {err}</div>
      ) : !data ? (
        <div className="mg-empty">Loading…</div>
      ) : rows.length === 0 ? (
        <div className="mg-empty">{q ? `Nothing matches “${q}”.` : "No scores yet — be the first!"}</div>
      ) : (
        <div className="mg-table-wrap">
          <table className="mg-lb">
            <thead>
              <tr><th>Rank</th><th>Name</th><th>Curriculum</th><th>Aircraft</th><th className="num">Score</th><th>Grade</th>{anyActions && <th />}</tr>
            </thead>
            <tbody>
              {rows.map((e) => (
                <tr key={e.id} className={e.rank <= 3 ? `is-top is-top-${e.rank}` : ""}>
                  <td className="mg-rank">{e.rank}</td>
                  <td>{e.pilot}</td>
                  <td>{e.circuit || "–"}</td>
                  <td>{e.aircraft || "–"}</td>
                  <td className="num mg-tot">{e.total ?? "–"}</td>
                  <td>{e.grade ? <span className={`mg-grade mg-grade--${e.grade}`}>{e.grade}</span> : "–"}</td>
                  {anyActions && (
                    <td className="num"><div className="mg-actions">
                      {e.session_id && (
                        <Link to={`/sessions/${e.session_id}/review`} className="mg-act"
                          title="Open this session's review">View</Link>
                      )}
                      {canDelete(e) && (
                        <button type="button" className="mg-act mg-act--del" onClick={() => remove(e)}
                          title="Delete this score" aria-label={`Delete ${e.pilot}'s score`}>Delete</button>
                      )}
                    </div></td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Pager page={page} pageSize={PAGE_SIZE} total={total} shown={rows.length}
        onPage={setPage} label="Leaderboard pages" />
    </GuidePage>
  );
}

// ---- Scoring guide ----
export function ScoringGuidePage() {
  return (
    <GuidePage title="How you're scored">
      <div className="mg-scoring"><ScoringGuideContent /></div>
    </GuidePage>
  );
}

// ---- Controls guide: MAVIS's own diagram. Like the original, a photo
// dropped at web/static/cockpit.png replaces the built-in schematic. ----
const GUIDE_IMAGES = ["/mavis/static/cockpit.png", "/mavis/static/cockpit_schematic.svg"];

export function ControlsGuidePage() {
  const [idx, setIdx] = useState(0);
  const src = GUIDE_IMAGES[idx];
  return (
    <GuidePage title="Controls guide" sub="Left hand throttle · right hand stick · feet on the pedals." wide>
      <div className="mg-diagram">
        {src ? (
          <img src={src} alt="Controls guide: throttle, rudder pedals and stick" onError={() => setIdx((i) => i + 1)} />
        ) : (
          <div className="mg-empty">Couldn't load the controls diagram — is MAVIS running?</div>
        )}
      </div>
    </GuidePage>
  );
}
