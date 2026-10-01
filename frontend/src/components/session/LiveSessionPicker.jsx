import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMavisState } from "../../live/hooks/useMavisSocket.js";

// Live Monitor's start screen: one card per MAVIS session still in
// progress, with a ticking flight time, what's being flown and by whom.

function elapsed(startedAt, now) {
  if (!startedAt) return "--:--";
  const s = Math.max(0, Math.floor((now - new Date(startedAt).getTime()) / 1000));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(sec)}` : `${pad(m)}:${pad(sec)}`;
}

// lesson id -> title from MAVIS's live catalog, falling back to a tidied id.
function useLessonTitles() {
  const catalog = useMavisState()?.lesson_catalog;
  const titles = {};
  (catalog?.modules || []).forEach((m) => (m.lessons || []).forEach((l) => { titles[l.id] = l.title; }));
  return (id) => titles[id] || (id ? id.replace(/^lesson\d*_?/, "").replace(/_/g, " ") : null);
}

const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);

function SessionCard({ s, now, lessonTitle, onWatch }) {
  const lesson = lessonTitle(s.lesson_id);
  return (
    <button type="button" className="lsp-card" onClick={onWatch}>
      <div className="lsp-card__top">
        <span className="lsp-live"><span className="lsp-live__dot" />LIVE</span>
        <span className="lsp-card__timer" title="Time since the session started">{elapsed(s.started_at, now)}</span>
      </div>

      <div className="lsp-card__title">{s.name || "MAVIS session"}</div>
      {s.pilot_name && <div className="lsp-card__pilot">{s.pilot_name}</div>}

      <div className="lsp-chips">
        {lesson && <span className="lsp-chip lsp-chip--accent">{lesson}</span>}
        {s.mode && <span className="lsp-chip">{cap(s.mode)}</span>}
        {s.aircraft && <span className="lsp-chip">{s.aircraft}</span>}
        {s.sim_source && <span className="lsp-chip">{s.sim_source}</span>}
      </div>

      <div className="lsp-card__foot">
        <span className="lsp-card__meta">
          Started {s.started_at ? new Date(s.started_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—"}
          <span className="lsp-card__id" title={s.id}>#{String(s.id).slice(0, 8)}</span>
        </span>
        <span className="lsp-card__cta">Watch live →</span>
      </div>
    </button>
  );
}

export default function LiveSessionPicker({ running, loaded, error, onPick }) {
  const nav = useNavigate();
  const lessonTitle = useLessonTitles();
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="lsp">
      <header className="lsp-head">
        <div>
          <h1>Live Monitor</h1>
          <p>Watch a flight MAVIS is running right now: screens, ground track and telemetry, live.</p>
        </div>
        <span className={"lsp-count" + (running.length ? " is-live" : "")}>
          <span className="lsp-live__dot" />
          {running.length ? `${running.length} live session${running.length > 1 ? "s" : ""}` : "No live sessions"}
        </span>
      </header>

      {error && <div className="lsp-error">Couldn't load running sessions: {error}. Retrying…</div>}

      {!loaded ? (
        <div className="lsp-grid">
          {[0, 1].map((i) => <div key={i} className="lsp-card lsp-card--skeleton" />)}
        </div>
      ) : running.length === 0 ? (
        <div className="lsp-empty">
          <div className="lsp-radar" aria-hidden="true"><span /></div>
          <h2>Waiting for a flight</h2>
          <p>No MAVIS session is running. Start one and it will show up here automatically.</p>
          <div className="lsp-empty__actions">
            <button type="button" onClick={() => nav("/live/freeflight")}>Start Free Flight</button>
            <button type="button" className="secondary" onClick={() => nav("/live")}>Open Basic Flight</button>
          </div>
        </div>
      ) : (
        <div className="lsp-grid">
          {running.map((s) => (
            <SessionCard key={s.id} s={s} now={now} lessonTitle={lessonTitle} onWatch={() => onPick(s.id)} />
          ))}
        </div>
      )}

      <div className="lsp-foot">Refreshes every few seconds · a session drops off this list once it's ended in MAVIS</div>
    </div>
  );
}
