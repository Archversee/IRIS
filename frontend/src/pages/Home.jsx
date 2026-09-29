import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { mavisApi } from "../live/api/mavisApi.js";

// Full-screen landing: splash-bg.jpeg split down the middle, left half is
// MAVIS, right half is IRIS. The sidebar is hidden on this route (see
// App.jsx). The image lives in frontend/public/media, served by the
// frontend itself, so it shows even when MAVIS isn't running.
//
// MAVIS goes straight into Basic Flight -> Your First Circuit
// (lesson01_pattern); IRIS goes to the Sessions list.
const FIRST_CIRCUIT = "lesson01_pattern";

export default function Home() {
  const nav = useNavigate();
  const [busy, setBusy] = useState(false);

  async function openMavis() {
    setBusy(true);
    try {
      await mavisApi.selectLesson(FIRST_CIRCUIT);
    } catch {
      // best-effort -- /live's own menu shows MAVIS's real status if it's down
    }
    nav("/live");
  }

  return (
    <div className="landing">
      <button type="button" className="landing__half landing__half--mavis" onClick={openMavis} disabled={busy}>
        <span className="landing__label">
          <span className="landing__title">MAVIS</span>
          <span className="landing__sub">Fly · Basic Flight: Your First Circuit</span>
        </span>
      </button>
      <button type="button" className="landing__half landing__half--iris" onClick={() => nav("/sessions")}>
        <span className="landing__label">
          <span className="landing__title">IRIS</span>
          <span className="landing__sub">Review · Sessions &amp; Debrief</span>
        </span>
      </button>
    </div>
  );
}
