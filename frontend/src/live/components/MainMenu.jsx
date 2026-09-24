import { useEffect, useState } from "react";
import ModeCards from "./ModeCards.jsx";
import { mavisApi, mediaUrl } from "../api/mavisApi.js";

// Mirrors the original app's proceed(): configure the session, then start
// flying immediately (the original also plays an intro video first --
// that's a later slice; skipping straight to Start keeps this usable now).
// Lesson browsing lives in the persistent sidebar (LessonTree) now, not
// duplicated here -- this screen is just "who's flying, at what level".
export default function MainMenu({ state, onStarting }) {
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
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
        trainee_name: [firstName, lastName].filter(Boolean).join(" "),
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
        <div className="mavis-menu__names">
          <input placeholder="First name" value={firstName} onChange={(e) => setFirstName(e.target.value)} />
          <input placeholder="Last name" value={lastName} onChange={(e) => setLastName(e.target.value)} />
        </div>
        <ModeCards onChoose={chooseMode} busy={busy} media={media} />
        {err && <div className="mavis-menu__error">{err}</div>}
      </div>
    </div>
  );
}
