import { useState } from "react";
import { mavisApi } from "../api/mavisApi.js";

// Shown while MAVIS is flying a step as a demonstration (state.demo, set by
// MAVIS main.py _fam_demo). Skip demo ends this one -- the aircraft goes back
// to where it began and the explanation is given held still; Skip all demos
// does that and turns them off for the rest of the run.
export default function DemoBanner({ demo }) {
  const [busy, setBusy] = useState(false);
  if (!demo) return null;

  async function send(fn) {
    setBusy(true);
    try {
      await fn();
    } catch {
      // best-effort: the demo simply runs to its end
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mavis-demo" role="status">
      <i className="mavis-demo__dot" aria-hidden="true" />
      <div className="mavis-demo__text">
        <b>MAVIS has control: demo</b>
        <span>{demo.title}. Hands off, watch. Your turn next.</span>
      </div>
      <div className="mavis-demo__actions">
        <button type="button" disabled={busy} onClick={() => send(mavisApi.skipDemo)}>Skip demo</button>
        <button type="button" className="secondary" disabled={busy}
          onClick={() => send(mavisApi.skipAllDemos)}>Skip all demos</button>
      </div>
    </div>
  );
}
