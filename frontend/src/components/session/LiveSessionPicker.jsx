import { useNavigate } from "react-router-dom";

// Live Monitor's start screen, shown only while no MAVIS session is running
// (LiveSession.jsx jumps straight into a live session as soon as one shows
// up, so there's nothing to pick here).

export default function LiveSessionPicker({ loaded, error }) {
  const nav = useNavigate();

  return (
    <div className="lsp">
      <header className="lsp-head">
        <div>
          <h1>Live Monitor</h1>
          <p>Watch a flight MAVIS is running right now: screens, ground track and telemetry, live.</p>
        </div>
        <span className="lsp-count">
          <span className="lsp-live__dot" />
          No live sessions
        </span>
      </header>

      {error && <div className="lsp-error">Couldn't load running sessions: {error}. Retrying…</div>}

      {!loaded ? (
        <div className="lsp-grid">
          {[0, 1].map((i) => <div key={i} className="lsp-card lsp-card--skeleton" />)}
        </div>
      ) : (
        <div className="lsp-empty">
          <div className="lsp-radar" aria-hidden="true"><span /></div>
          <h2>Waiting for a flight</h2>
          <p>No MAVIS session is running. Start one and the live monitor will open automatically.</p>
          <div className="lsp-empty__actions">
            <button type="button" onClick={() => nav("/live/freeflight")}>Start Free Flight</button>
            <button type="button" className="secondary" onClick={() => nav("/live")}>Open Basic Flight</button>
          </div>
        </div>
      )}

      <div className="lsp-foot">Checks every few seconds for a running MAVIS session</div>
    </div>
  );
}
