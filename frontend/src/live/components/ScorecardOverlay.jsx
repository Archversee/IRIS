import { useState } from "react";
import { mavisApi } from "../api/mavisApi.js";

const GRADE_LABEL = { S: "S", A: "A", B: "B", C: "C", D: "D", F: "F" };
// Scorer keys -> what the bar is about (the familiarization lesson's keys
// aren't self-explanatory; circuit keys read fine as they are)
const METRIC_LABEL = { altitude: "height hold", bank: "turns", climb_speed: "climb speed", speed: "speed hold", level_off: "level-off",
  sequence: "recovery order", recovery: "recovery time", heading: "heading accuracy" };

// Ported to match the original app's #scoreView styling (grade-pill
// gradients, metric bars, debrief text). The scoring guide lives in the
// sidebar (Scoring Guide page) rather than as a button here.
// "Compare against": the flight just finished, re-scored against an expert's
// recorded circuit (MAVIS core/compare.py) -- matched by position around the
// circuit, not by time. Same flight, no re-flying.
function ComparePanel({ references }) {
  const [refId, setRefId] = useState("");
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  async function pick(id) {
    setRefId(id);
    setRes(null);
    setErr(null);
    if (!id) return;
    setBusy(true);
    try {
      const r = await mavisApi.compareReference(id);
      if (r?.ok) setRes(r);
      else setErr(r?.error || "Couldn't compare.");
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mavis-compare">
      <label className="mavis-compare__pick">
        Compare against{" "}
        <select value={refId} onChange={(e) => pick(e.target.value)} disabled={busy}>
          <option value="">Planned circuit (the scores above)</option>
          {references.map((r) => (
            <option key={r.id} value={r.id}>{r.name}{r.demo ? " (demo)" : ""}</option>
          ))}
        </select>
      </label>
      {busy && <div className="muted">Comparing…</div>}
      {err && <div className="mavis-compare__err">{err}</div>}
      {res && (
        <div className="mavis-compare__result">
          <div className="mavis-compare__headline">
            <b>{res.similarity ?? "–"}%</b> like {res.name}
          </div>
          <div className="mavis-score-metrics">
            {Object.entries(res.scores || {}).map(([k, v]) => (
              <div className="mavis-score-metric" key={k}>
                <div className="mavis-score-metric__label">{k === "altitude" ? "height" : k}</div>
                <div className="mavis-score-metric__bar">
                  <div className="mavis-score-metric__fill" style={{ width: `${Math.max(0, Math.min(100, v ?? 0))}%` }} />
                </div>
                <div className="mavis-score-metric__val">{v ?? "–"}</div>
              </div>
            ))}
          </div>
          <ul className="mavis-compare__notes">
            {(res.notes || []).map((n) => <li key={n}>{n}</li>)}
          </ul>
          {!res.detailed && (
            <div className="muted">Demo run: its path is compared point by point, speed and height against its target numbers.</div>
          )}
        </div>
      )}
    </div>
  );
}

// Instructor only (PIN): keep the flight just finished as an expert
// reference for this lesson -- it then appears under "Compare against".
function SaveReference() {
  const [open, setOpen] = useState(false);
  const [pin, setPin] = useState("");
  const [name, setName] = useState("");
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);

  async function save(e) {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      const r = await mavisApi.saveReference(pin, name);
      setMsg(r?.ok ? { ok: true, text: `Saved as "${r.name}".` } : { ok: false, text: r?.error || "Couldn't save." });
      if (r?.ok) setPin("");
    } catch (err) {
      setMsg({ ok: false, text: err.message });
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return <button type="button" className="mavis-compare__save-link" onClick={() => setOpen(true)}>Instructor: save this flight as a reference</button>;
  }
  return (
    <form className="mavis-compare__save" onSubmit={save}>
      <input type="password" inputMode="numeric" placeholder="Instructor PIN" value={pin}
        onChange={(e) => setPin(e.target.value)} autoComplete="off" />
      <input type="text" placeholder="Name (optional)" value={name} maxLength={64}
        onChange={(e) => setName(e.target.value)} />
      <button type="submit" disabled={busy || !pin}>Save</button>
      {msg && <span className={msg.ok ? "mavis-compare__ok" : "mavis-compare__err"}>{msg.text}</span>}
    </form>
  );
}

export default function ScorecardOverlay({ scorecard, references, onMainMenu, onDiscard, onFlyAgain }) {
  const scores = scorecard.scores || {};

  // MAVIS clears state.scorecard on relaunch, so this overlay just
  // disappears once the new attempt begins. relaunch (not restart) also puts
  // the aircraft back at the start and begins the lesson again. LiveCoach's
  // onFlyAgain also re-opens the session so OBS records the new flight.
  async function flyAgain() {
    if (onFlyAgain) return onFlyAgain();
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
          {scorecard.level && <span className="muted"> · judged at {scorecard.level} standard</span>}
        </div>
        {/* the same flight judged at the strictest level (Scoring Guide: levels) */}
        {scorecard.checkride && scorecard.level !== "Check-ride" && (
          <div className="mavis-score-checkride">
            Against check-ride standard: <b>{scorecard.checkride.grade}</b> ({scorecard.checkride.total})
          </div>
        )}

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

        {/* expert comparison: circuit lessons with a finished flight */}
        {scorecard.can_compare && !scorecard.incomplete && (
          <>
            {references?.length > 0 && <ComparePanel references={references} />}
            <SaveReference />
          </>
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
