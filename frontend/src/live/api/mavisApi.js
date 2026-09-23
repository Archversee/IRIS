// Thin REST client for MAVIS's own API (proxied at /mavis in both dev and
// prod -- see vite.config.js / proxy/app.py). Same request-helper pattern
// as IRIS's own api/client.js.
const BASE = "/mavis";

async function req(path, opts = {}) {
  const res = await fetch(BASE + path, opts);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
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
  chat: (text) => postJson("/chat", { text }),
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
