import { useState } from "react";
import { mavisApi } from "../api/mavisApi.js";
import ScoringGuide from "./ScoringGuide.jsx";

const GRADE_LABEL = { S: "S", A: "A", B: "B", C: "C", D: "D", F: "F" };

// Ported to match the original app's #scoreView styling (grade-pill
// gradients, metric bars, debrief text, scoring guide) -- not the
// leaderboard-post fields / leaderboard button yet, since that screen
// doesn't exist in this rebuild.
export default function ScorecardOverlay({ scorecard, onMainMenu }) {
  const scores = scorecard.scores || {};
  const [showGuide, setShowGuide] = useState(false);

  // No parent callback needed here -- MAVIS clears state.scorecard on
  // restart, so this overlay just disappears once the new attempt begins.
  async function flyAgain() {
    try {
      await mavisApi.restart();
    } catch {
      // best-effort
    }
  }

  return (
    <div className="mavis-score-overlay">
      <div className="mavis-score-card">
        <div className="mavis-score-head">
          <h2>{scorecard.incomplete ? "Lesson ended early" : "Lesson complete"}</h2>
          <div className={"mavis-grade-pill mavis-grade-pill--" + (scorecard.grade || "")}>
            {GRADE_LABEL[scorecard.grade] || scorecard.grade || "–"}
          </div>
        </div>
        <div className="mavis-score-total">
          Total score <b>{scorecard.total ?? "–"}</b> / 100
        </div>

        <div className="mavis-score-metrics">
          {Object.entries(scores).map(([k, v]) => (
            <div className="mavis-score-metric" key={k}>
              <div className="mavis-score-metric__label">{k}</div>
              <div className="mavis-score-metric__bar">
                <div className="mavis-score-metric__fill" style={{ width: `${Math.max(0, Math.min(100, v ?? 0))}%` }} />
              </div>
              <div className="mavis-score-metric__val">{v ?? "–"}</div>
            </div>
          ))}
        </div>

        {scorecard.retries > 0 && (
          <div className="muted" style={{ marginBottom: 8 }}>
            {scorecard.retries} {scorecard.retries === 1 ? "retry" : "retries"}
            {scorecard.capped ? " (score capped)" : ""}
          </div>
        )}
        {scorecard.practice && (
          <div className="muted" style={{ marginBottom: 8 }}>This result will not be posted to the leaderboard.</div>
        )}

        {scorecard.debrief_pending ? (
          <p className="muted">Writing your debrief…</p>
        ) : (
          scorecard.debrief && <p>{scorecard.debrief}</p>
        )}

        <div className="mavis-score-btns">
          <button type="button" onClick={flyAgain}>↻ Fly again</button>
          <button type="button" className="secondary" onClick={() => setShowGuide(true)}>Scoring guide</button>
          <button type="button" className="secondary" onClick={onMainMenu}>Finish &amp; save ›</button>
        </div>
      </div>
      {showGuide && <ScoringGuide onClose={() => setShowGuide(false)} />}
    </div>
  );
}
