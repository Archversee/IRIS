const BASE = import.meta.env.VITE_API_BASE || "/api";

async function req(path, opts = {}) {
  const res = await fetch(BASE + path, opts);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

// Same as req(), but reports download progress via onProgress(loadedBytes,
// totalBytes) as the body streams in -- for the large flight/eye payloads,
// where a plain fetch() gives no feedback until the whole thing lands.
// Falls back to a single 100% callback if the size is unknown (no
// Content-Length) or the browser can't stream the body.
async function reqWithProgress(path, onProgress) {
  const res = await fetch(BASE + path);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
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
  listSessions: () => req("/sessions"),
  getSession: (id) => req(`/sessions/${id}`),
  createSession: (body) =>
    req("/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  deleteSession: (id) => req(`/sessions/${id}`, { method: "DELETE" }),

  ingest: (id, kind, file) => {
    const fd = new FormData();
    fd.append("file", file);
    return req(`/sessions/${id}/ingest/${kind}`, { method: "POST", body: fd });
  },

  summary: (id) => req(`/sessions/${id}/summary`),
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
};
