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
  flight: (id, maxPoints = 3000) =>
    req(`/sessions/${id}/flight?max_points=${maxPoints}`),
  eye: (id, maxPoints = 3000) => req(`/sessions/${id}/eye?max_points=${maxPoints}`),
  events: (id) => req(`/sessions/${id}/events`),
  analytics: (id) => req(`/sessions/${id}/analytics`),
};
