const BASE = import.meta.env.VITE_API_BASE || "/api";

// Sign-in token (see auth/AuthContext.jsx) -- sent on every request; only
// the /auth and /users routes require it.
const TOKEN_KEY = "authToken";
export function getToken() {
  try { return localStorage.getItem(TOKEN_KEY); } catch { return null; }
}
export function setToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch { /* storage unavailable -- token lives for this page only */ }
}
function withAuth(opts = {}) {
  const token = getToken();
  return token ? { ...opts, headers: { ...(opts.headers || {}), Authorization: `Bearer ${token}` } } : opts;
}

// Errors carry .status and .detail (FastAPI's {"detail": ...} message) so
// forms can show the server's own wording ("Incorrect username or password").
async function httpError(res) {
  const text = await res.text();
  let detail = text;
  try { detail = JSON.parse(text).detail ?? text; } catch { /* not JSON */ }
  if (typeof detail !== "string") detail = JSON.stringify(detail);
  const err = new Error(`${res.status} ${res.statusText}: ${text}`);
  err.status = res.status;
  err.detail = detail;
  return err;
}

async function req(path, opts = {}) {
  const res = await fetch(BASE + path, withAuth(opts));
  if (!res.ok) throw await httpError(res);
  if (res.status === 204) return null;
  return res.json();
}

const json = (method, body) => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

// Same as req(), but reports download progress via onProgress(loadedBytes,
// totalBytes) as the body streams in -- for the large flight/eye payloads,
// where a plain fetch() gives no feedback until the whole thing lands.
// Falls back to a single 100% callback if the size is unknown (no
// Content-Length) or the browser can't stream the body.
async function reqWithProgress(path, onProgress) {
  const res = await fetch(BASE + path, withAuth());
  if (!res.ok) throw await httpError(res);
  const total = Number(res.headers.get("content-length")) || 0;
  if (!res.body || !total) {
    const data = await res.json();
    onProgress?.(1, 1);
    return data;
  }
  const reader = res.body.getReader();
  const chunks = [];
  let loaded = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    loaded += value.length;
    onProgress?.(loaded, total);
  }
  return JSON.parse(await new Blob(chunks).text());
}

export const api = {
  // ---- accounts (backend/app/routers/accounts.py) ----
  login: (username, password) => req("/auth/login", json("POST", { username, password })),
  logout: () => req("/auth/logout", { method: "POST" }),
  me: () => req("/auth/me"),
  updateMe: (body) => req("/auth/me", json("PATCH", body)),
  changePassword: (current_password, new_password) =>
    req("/auth/me/password", json("POST", { current_password, new_password })),
  listUsers: () => req("/users"),
  listPilots: () => req("/users/pilots"),
  // MAVIS preset routes (planned circuits) for the ground track overlay
  listRoutes: () => req("/routes"),
  createUser: (body) => req("/users", json("POST", body)),
  updateUser: (id, body) => req(`/users/${id}`, json("PATCH", body)),
  deleteUser: (id) => req(`/users/${id}`, { method: "DELETE" }),

  listSessions: () => req("/sessions"),
  getSession: (id) => req(`/sessions/${id}`),
  createSession: (body) =>
    req("/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  deleteSession: (id) => req(`/sessions/${id}`, { method: "DELETE" }),
  updateSession: (id, body) =>
    req(`/sessions/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  // MAVIS-sourced sessions still in progress -- Live Monitor's picker.
  // MAVIS mints session_id and owns starting/stopping everything (OBS,
  // Smart Eye, flight logging); IRIS only ever views here.
  listRunningSessions: () => req(`/sessions/running`),
  // All-time leaderboard (Postgres, routers/leaderboard.py): {rows, total, page, page_size}
  leaderboard: ({ q = "", field = "all", mode = "", page = 1, pageSize = 20 } = {}) =>
    req(`/leaderboard?${new URLSearchParams({ q, field, mode, page, page_size: pageSize })}`),
  // Live Monitor's screens, grabbed by window title on the sim PC
  // (backend routers/capture.py). `nonce` forces a fresh connection.
  captureStatus: () => req(`/capture/status`),
  captureFeedUrl: (screen, nonce) => `${BASE}/capture/${screen}.mjpg?n=${nonce}`,

  ingest: (id, kind, file) => {
    const fd = new FormData();
    fd.append("file", file);
    return req(`/sessions/${id}/ingest/${kind}`, { method: "POST", body: fd });
  },

  summary: (id) => req(`/sessions/${id}/summary`),
  // start/end of each graded event (to-do step / phase) as logged by MAVIS
  events: (id) => req(`/sessions/${id}/events`),
  // the session's flights (which curriculum each came from, or free flight)
  flights: (id) => req(`/sessions/${id}/flights`),
  // 200000 is the backend's own ceiling (see max_points in data.py).
  // onProgress(loadedBytes, totalBytes) is optional -- lets callers show a
  // real download progress bar for these (often large) payloads.
  flight: (id, maxPoints = 200000, onProgress) =>
    reqWithProgress(`/sessions/${id}/flight?max_points=${maxPoints}`, onProgress),
  eye: (id, maxPoints = 200000, onProgress) =>
    reqWithProgress(`/sessions/${id}/eye?max_points=${maxPoints}`, onProgress),
  analytics: (id) => req(`/sessions/${id}/analytics`),

  videosAvailable: () => req("/videos-available"),
  linkVideo: (id, screen, body) =>
    req(`/sessions/${id}/video/${screen}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  // Live telemetry never goes through REST -- see stream.py. This builds
  // the matching ws(s):// URL for the same origin/base the REST calls use.
  liveSocketUrl: (id) => {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${window.location.host}${BASE}/sessions/${id}/stream/subscribe`;
  },

  listAoiZones: () => req("/aoi-zones"),
  createAoiZone: (body) =>
    req("/aoi-zones", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  deleteAoiZone: (id) => req(`/aoi-zones/${id}`, { method: "DELETE" }),

  // SOP / handbook rule checks of a session (backend/app/routers/rule_results.py).
  // MAVIS computes them (sopApi.evaluate); the instructor's verdicts and sign-off go here.
  ruleResults: (id) => req(`/sessions/${id}/rule-results`),
  setRuleVerdict: (resultId, body) => req(`/rule-results/${resultId}`, json("PATCH", body)),
  signOffRules: (id, body) => req(`/sessions/${id}/rule-results/sign-off`, json("POST", body)),
  getRuleStrictness: (userId) => req(`/users/${userId}/rule-strictness`),
  setRuleStrictness: (userId, strictness) => req(`/users/${userId}/rule-strictness`, json("PUT", { strictness })),
};
