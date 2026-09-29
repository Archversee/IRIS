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

// "Multimodal Aviation ..." with each word's first letter bolded and
// underlined, so the acronym reads out of the full name.
function FullName({ text }) {
  return (
    <span className="landing__full">
      {text.split(" ").map((word, i) => (
        <span key={i}>
          {i > 0 && " "}
          <b>{word[0]}</b>{word.slice(1)}
        </span>
      ))}
    </span>
  );
}

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
          <FullName text="Multimodal Aviation Voice Instructor System" />
          <span className="landing__sub">Learn · Fly · Practice</span>
        </span>
      </button>
      <button type="button" className="landing__half landing__half--iris" onClick={() => nav("/sessions")}>
        <span className="landing__label">
          <span className="landing__title">IRIS</span>
          <FullName text="Integrated Review Insight System" />
          <span className="landing__sub">Review · Analyse · Debrief</span>
        </span>
      </button>
    </div>
  );
}
