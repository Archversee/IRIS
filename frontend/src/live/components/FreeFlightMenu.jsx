import { useEffect, useState } from "react";
import { mavisApi, mediaUrl } from "../api/mavisApi.js";

// Same shell as MainMenu (Basic Flight) -- placeholder UI reusing the same
// styling/background -- but no visitor/trainee/pilot cards: Free Flight
// doesn't ask for an experience level, it just starts on a single explicit
// button (Basic Flight auto-starts the instant a card is picked; Free
// Flight waits for you to press Start). Mode is fixed to "pilot" (least
// hand-holding) since there's no card to choose it from.
export default function FreeFlightMenu({ onStarting }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [media, setMedia] = useState(null);

  useEffect(() => {
    let cancelled = false;
    mavisApi
      .mediaManifest()
      .then((m) => { if (!cancelled) setMedia(m); })
      .catch(() => { if (!cancelled) setMedia({}); });
    return () => { cancelled = true; };
  }, []);

  async function startSession() {
    setBusy(true);
    setErr(null);
    try {
      await mavisApi.configureSession({
        mode: "pilot",
        jargon: "layman",
        tone: "warm",
        reference: "planned",
        free_flight: true, // logged as Free Flight, not as the lesson MAVIS has loaded underneath
      });
      await mavisApi.start();
      onStarting();
    } catch (e) {
      setErr(e.message);
      setBusy(false);
    }
  }

  const menuBg = media?.["menu-bg"] ? mediaUrl(media["menu-bg"]) : null;
  const splashBg = media?.["splash-bg"] ? mediaUrl(media["splash-bg"]) : null;

  return (
    <div className="mavis-menu mavis-menu--solo" style={menuBg ? { backgroundImage: `url(${menuBg})` } : undefined}>
      <div className="mavis-menu__main">
        <div className="mavis-modes">
          <button
            type="button"
            className="mavis-mode-card"
            disabled={busy}
            onClick={startSession}
            style={splashBg ? { backgroundImage: `linear-gradient(180deg, rgba(19,25,34,0.55), rgba(19,25,34,0.92)), url(${splashBg})` } : undefined}
          >
            <div className="mavis-mode-card__label">{busy ? "Starting…" : "▶ Start Session"}</div>
            <div className="mavis-mode-card__desc">Fly freely -- logged and recorded the same as any other flight.</div>
          </button>
        </div>
        {err && <div className="mavis-menu__error">{err}</div>}
      </div>
    </div>
  );
}
