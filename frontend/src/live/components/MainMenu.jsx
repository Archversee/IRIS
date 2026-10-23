import { useEffect, useState } from "react";
import ModeCards from "./ModeCards.jsx";
import { mavisApi, mediaUrl } from "../api/mavisApi.js";
import { useAuth, pilotIdentity } from "../../auth/AuthContext.jsx";

// Mirrors the original app's proceed(): configure the session, then start
// flying immediately (the original also plays an intro video first --
// that's a later slice; skipping straight to Start keeps this usable now).
// Lesson browsing lives in the persistent sidebar (LessonTree) now, not
// duplicated here -- this screen is just "who's flying, at what level".
export default function MainMenu({ state, onStarting }) {
  const { user } = useAuth();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [media, setMedia] = useState(null);

  useEffect(() => {
    let cancelled = false;
    mavisApi
      .mediaManifest()
      .then((m) => { if (!cancelled) setMedia(m); })
      .catch(() => { if (!cancelled) setMedia({}); }); // no media dir configured -- fine, just no backgrounds
    return () => { cancelled = true; };
  }, []);

  async function chooseMode(mode) {
    setBusy(true);
    setErr(null);
    try {
      await mavisApi.configureSession({
        mode,
        jargon: "layman",
        tone: "warm",
        reference: "planned",
        ...pilotIdentity(user),
      });
      await mavisApi.start();
      onStarting();
    } catch (e) {
      setErr(e.message);
      setBusy(false);
    }
  }

  const menuBg = media?.["menu-bg"] ? mediaUrl(media["menu-bg"]) : null;

  return (
    <div className="mavis-menu mavis-menu--solo" style={menuBg ? { backgroundImage: `url(${menuBg})` } : undefined}>
      <div className="mavis-menu__main">
        <div className="mavis-menu__greeting">{state?.active_lesson?.title || "Ready to fly"}</div>
        {state?.active_lesson?.start_note && (
          <div className="mavis-menu__note">{state.active_lesson.start_note}</div>
        )}
        <ModeCards onChoose={chooseMode} busy={busy} media={media} />
        {err && <div className="mavis-menu__error">{err}</div>}
      </div>
    </div>
  );
}
