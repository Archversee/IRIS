// Thin REST client for MAVIS's own API (proxied at /mavis in both dev and
// prod -- see vite.config.js / proxy/app.py). Same request-helper pattern
// as IRIS's own api/client.js.
const BASE = "/mavis";

async function req(path, opts = {}) {
  const res = await fetch(BASE + path, opts);
  if (!res.ok) {
    const text = await res.text();
    // MAVIS's /demo/* failures carry {"error": "..."} -- show that sentence
    let reason = null;
    try { reason = JSON.parse(text)?.error || null; } catch { /* not JSON */ }
    throw new Error(reason || `${res.status} ${res.statusText}: ${text}`);
  }
  if (res.status === 204) return null;
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : null;
}

function postJson(path, body) {
  return req(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
}

export const mavisApi = {
  // mode: "visitor" | "trainee" | "pilot"
  configureSession: (body) => postJson("/session", body),
  selectLesson: (id) => postJson("/lesson/select", { id }),
  start: () => postJson("/demo/start"),
  quit: () => postJson("/demo/quit"),
  restart: () => postJson("/demo/restart"),
  // Restart button: reset, aircraft back to the lesson's start position (airborne
  // for familiarization, runway start otherwise), go again -- same session
  relaunch: () => postJson("/demo/relaunch"),
  // Fly again (scorecard): the same, but as a brand-new session
  relaunchNew: () => postJson("/demo/relaunch_new"),
  // the up-front demonstration of an airborne lesson (MAVIS flying it first)
  skipDemo: () => postJson("/demo/skip_demo"),
  chat: (text) => postJson("/chat", { text }),
  // Scorecard: the finished flight compared with an expert reference
  // -> {ok, name, similarity, scores, notes, demo} or {ok: false, error}
  compareReference: (reference) => postJson("/score/compare", { reference }),
  // Scorecard (instructor): save the finished flight as an expert reference
  saveReference: (pin, display_name = "") => postJson("/score/save_reference", { pin, display_name }),
  // Push-to-talk: WAV blob -> Whisper on MAVIS -> same handler as typed chat.
  // -> {ok, text} or {ok: false, reason}
  voicePtt: (blob, name = "", signal) => {
    const fd = new FormData();
    fd.append("audio", blob, "ptt.wav");
    if (name) fd.append("name", name);
    return req("/voice/ptt", { method: "POST", body: fd, signal });
  },
  // On-demand TTS narration for one reading-lesson checkpoint -> {ok, url}
  readingSay: (id, lesson) => postJson("/reading/say", { id, lesson }),
  // {stem: "/media/filename.ext"} for whichever of splash-bg/menu-bg/
  // mode-{visitor,trainee,pilot} exist on disk -- see web/server.py's
  // /media/manifest. Paths are root-relative from MAVIS's own origin, so
  // mediaUrl() below re-bases them through the /mavis proxy prefix.
  mediaManifest: () => req("/media/manifest"),
};

// A manifest path like "/media/splash-bg.jpg" is relative to MAVIS's own
// origin, not this app's -- re-base it through the same /mavis prefix
// every other MAVIS request uses, so it resolves correctly through both
// the dev proxy and the production reverse proxy.
export function mediaUrl(path) {
  return path ? BASE + path : null;
}
