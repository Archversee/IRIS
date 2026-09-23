import { useState } from "react";
import LessonPicker from "./LessonPicker.jsx";
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
    <div>
      <h1>{state?.active_lesson?.title || "MAVIS"}</h1>
      <p className="muted">Live AI flight instructor.</p>

      <div className="card">
        <LessonPicker
          lessonList={state?.lesson_list}
          activeLessonId={state?.lesson_id}
          onSelect={selectLesson}
        />
        <label htmlFor="mavis-name">Your name (optional)</label>
        <input
          id="mavis-name"
          placeholder="Trainee name"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </div>

      <h3 style={{ marginTop: 24 }}>Choose your level</h3>
      <ModeCards onChoose={chooseMode} busy={busy} />
      {err && <p className="err">{err}</p>}
    </div>
  );
}
