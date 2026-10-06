import { mavisApi } from "../api/mavisApi.js";

const GRADE_LABEL = { S: "S", A: "A", B: "B", C: "C", D: "D", F: "F" };
// Scorer keys -> what the bar is about (the familiarization lesson's keys
// aren't self-explanatory; circuit keys read fine as they are)
const METRIC_LABEL = { altitude: "height hold", bank: "turns", climb_speed: "climb speed" };

// Ported to match the original app's #scoreView styling (grade-pill
// gradients, metric bars, debrief text). The scoring guide lives in the
// sidebar (Scoring Guide page) rather than as a button here.
export default function ScorecardOverlay({ scorecard, onMainMenu, onDiscard }) {
  const scores = scorecard.scores || {};

  // No parent callback needed here -- MAVIS clears state.scorecard on
  // restart, so this overlay just disappears once the new attempt begins.
  // restart alone only reset MAVIS and left the HUD idle; relaunch also puts
  // the aircraft back at the start and begins the lesson again
  async function flyAgain() {
    try {
      await mavisApi.relaunch();
    } catch {
      // best-effort -- the HUD's own Restart button shows errors
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
              <div className="mavis-score-metric__label">{METRIC_LABEL[k] || k.replace(/_/g, " ")}</div>
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
          <button type="button" className="secondary" onClick={onMainMenu}>Finish &amp; save ›</button>
          {onDiscard && (
            <button type="button" className="secondary" onClick={onDiscard}>Finish &amp; discard ›</button>
          )}
        </div>
      </div>
    </div>
  );
}
