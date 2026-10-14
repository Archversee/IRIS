import { useEffect, useState } from "react";
import { mavisApi } from "../api/mavisApi.js";

// Shown while MAVIS flies the whole lesson as a demonstration before the
// trainee's turn (state.demo = {n, of, title}, set by MAVIS main.py
// _fam_demo_all). Skip demo ends it: the aircraft goes back to the start and
// the trainee takes over. The button never locks: a slow or failed request
// just lets you press it again.
export default function DemoBanner({ demo }) {
  const [skipping, setSkipping] = useState(false);
  const active = !!demo;
  useEffect(() => {
    if (!active) setSkipping(false);    // next demo starts fresh
  }, [active]);
  if (!demo) return null;

  function skip() {
    setSkipping(true);
    mavisApi.skipDemo().catch(() => setSkipping(false));
  }

  const step = demo.n ? `Step ${demo.n} of ${demo.of}: ${demo.title}` : demo.title;
  return (
    <div className="mavis-demo" role="status">
      <i className="mavis-demo__dot" aria-hidden="true" />
      <div className="mavis-demo__text">
        <b>{skipping ? "Skipping the demo…" : demo.describe ? "MAVIS explains: demo" : "MAVIS is flying: demo"}</b>
        <span>{step}. {demo.describe
          ? "The autopilot can't fly this one, so listen. You'll fly it on your turn."
          : "Hands off and watch, then it's your turn."}</span>
      </div>
      <div className="mavis-demo__actions">
        <button type="button" className="mavis-demo__skip" onClick={skip}>Skip demo</button>
      </div>
    </div>
  );
}
