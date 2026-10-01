import { useState } from "react";
import { useMavisState, useMavisStatus } from "../live/hooks/useMavisSocket.js";
import { ScoringGuideContent } from "../live/components/ScoringGuide.jsx";
import "../live/live.css";

// MAVIS reference pages in the sidebar -- Leaderboard, Scoring Guide and
// the Controls guide -- ported from the original MAVIS page's pop-ups
// (#boardView, #guideMenuView, #infoModal in web/static/index.html).

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

// ---- Leaderboard: MAVIS's own board (state.leaderboard[mode][scope]) ----
const MODES = [["visitor", "Visitor"], ["trainee", "Trainee"], ["pilot", "Pilot"]];
const SCOPES = [["session", "This session"], ["all", "All-time"]];

function lbName(e) {
  const giv = (e.given || "").trim(), fam = (e.family || "").trim();
  if (giv || fam) return [giv, fam].filter(Boolean).join(" ");
  return e.name || "-";
}

export function LeaderboardPage() {
  const state = useMavisState();
  const status = useMavisStatus();
  const [mode, setMode] = useState("visitor");
  const [scope, setScope] = useState("all");
  const rows = state?.leaderboard?.[mode]?.[scope] || [];

  return (
    <GuidePage title="Leaderboard" sub="Best circuits flown with MAVIS, per experience level." wide>
      <div className="mg-tabs">
        <div className="mg-seg">
          {MODES.map(([v, l]) => (
            <button key={v} type="button" className={mode === v ? "is-on" : ""} onClick={() => setMode(v)}>{l}</button>
          ))}
        </div>
        <div className="mg-seg">
          {SCOPES.map(([v, l]) => (
            <button key={v} type="button" className={scope === v ? "is-on" : ""} onClick={() => setScope(v)}>{l}</button>
          ))}
        </div>
      </div>

      {!state ? (
        <div className="mg-empty">{status === "open" ? "Loading…" : "Waiting for MAVIS… the leaderboard comes from the MAVIS server."}</div>
      ) : rows.length === 0 ? (
        <div className="mg-empty">No scores yet — be the first!</div>
      ) : (
        <div className="mg-table-wrap">
          <table className="mg-lb">
            <thead>
              <tr><th>#</th><th>Name</th><th>Track</th><th>Speed</th><th>Alt</th><th>Landing</th><th>Smooth</th><th>Retries</th><th>Total</th></tr>
            </thead>
            <tbody>
              {rows.map((e, i) => {
                const s = e.scores || {};
                return (
                  <tr key={i} className={i < 3 ? `is-top is-top-${i + 1}` : ""}>
                    <td className="mg-rank">{i + 1}</td>
                    <td>{lbName(e)}</td>
                    <td>{s.track ?? "–"}</td>
                    <td>{s.speed ?? "–"}</td>
                    <td>{s.altitude ?? "–"}</td>
                    <td>{s.landing ?? "–"}</td>
                    <td>{s.smoothness ?? "–"}</td>
                    <td>{e.retries || 0}</td>
                    <td className="mg-tot">{e.total}{e.grade ? ` · ${e.grade}` : ""}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
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
