import { useState } from "react";
import LessonSidebar from "./LessonSidebar.jsx";
import ModeCards from "./ModeCards.jsx";
import { mavisApi } from "../api/mavisApi.js";

// Mirrors the original app's proceed(): configure the session, then start
// flying immediately (the original also plays an intro video first --
// that's a later slice; skipping straight to Start keeps this usable now).
export default function MainMenu({ state, onStarting }) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  async function chooseMode(mode) {
    setBusy(true);
    setErr(null);
    try {
      await mavisApi.configureSession({
        mode,
        jargon: "layman",
        tone: "warm",
        reference: "planned",
        trainee_name: name,
      });
      await mavisApi.start();
      onStarting();
    } catch (e) {
      setErr(e.message);
      setBusy(false);
    }
  }

  async function selectLesson(lesson) {
    try {
      await mavisApi.selectLesson(lesson.id);
    } catch (e) {
      setErr(e.message);
    }
  }

  return (
    <div className="mavis-menu">
      <LessonSidebar
        catalog={state?.lesson_catalog}
        activeLessonId={state?.lesson_id}
        onSelect={selectLesson}
      />
      <div className="mavis-menu__main">
        <div className="mavis-menu__greeting">{state?.active_lesson?.title || "Ready to fly"}</div>
        <input
          className="mavis-menu__name"
          placeholder="Your name (optional)"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <ModeCards onChoose={chooseMode} busy={busy} />
        {err && <div className="mavis-menu__error">{err}</div>}
      </div>
    </div>
  );
}
