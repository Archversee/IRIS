import { useEffect, useState } from "react";
import { api } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
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
// One board per mode; the search matches name, circuit or aircraft.
const MODES = [["visitor", "Visitor"], ["trainee", "Trainee"], ["pilot", "Pilot"]];
const PAGE_SIZE = 20;

// 1 … 4 5 [6] 7 8 … 20
function pageList(page, pages) {
  const want = new Set([1, pages, page - 2, page - 1, page, page + 1, page + 2]);
  const out = [];
  for (let p = 1; p <= pages; p++) {
    if (!want.has(p)) continue;
    if (out.length && p - out[out.length - 1] > 1) out.push("…");
    out.push(p);
  }
  return out;
}

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
    if (!confirm(`Delete ${e.pilot}'s score of ${e.total} on ${e.circuit || "this circuit"}? This can't be undone.`)) return;
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
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const first = total ? (page - 1) * PAGE_SIZE + 1 : 0;
  const anyDeletable = rows.some(canDelete);

  return (
    <GuidePage title="Leaderboard" sub="Every circuit posted from MAVIS, ranked by score." wide>
      <div className="mg-tabs">
        <div className="mg-seg">
          {MODES.map(([v, l]) => (
            <button key={v} type="button" className={mode === v ? "is-on" : ""}
              onClick={() => { setMode(v); setPage(1); }}>{l}</button>
          ))}
        </div>
        <input type="search" className="mg-search" value={query} onChange={(e) => setQuery(e.target.value)}
          placeholder="Search name, circuit or aircraft…" aria-label="Search the leaderboard" />
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
              <tr><th>Rank</th><th>Name</th><th>Circuit</th><th>Aircraft</th><th className="num">Score</th>{anyDeletable && <th />}</tr>
            </thead>
            <tbody>
              {rows.map((e) => (
                <tr key={e.id} className={e.rank <= 3 ? `is-top is-top-${e.rank}` : ""}>
                  <td className="mg-rank">{e.rank}</td>
                  <td>{e.pilot}</td>
                  <td>{e.circuit || "–"}</td>
                  <td>{e.aircraft || "–"}</td>
                  <td className="num mg-tot">{e.total ?? "–"}</td>
                  {anyDeletable && (
                    <td className="num">
                      {canDelete(e) && (
                        <button type="button" className="mg-del" onClick={() => remove(e)}
                          title="Delete this score" aria-label={`Delete ${e.pilot}'s score`}>Delete</button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {total > 0 && (
        <nav className="mg-pager" aria-label="Leaderboard pages">
          <span className="mg-pager__info">{first}–{first + rows.length - 1} of {total}</span>
          <div className="mg-pager__btns">
            <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)}>‹ Prev</button>
            {pageList(page, pages).map((p, i) => p === "…"
              ? <span key={`gap${i}`} className="mg-pager__gap">…</span>
              : <button key={p} type="button" className={p === page ? "is-on" : ""} onClick={() => setPage(p)}>{p}</button>)}
            <button type="button" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next ›</button>
          </div>
        </nav>
      )}
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
