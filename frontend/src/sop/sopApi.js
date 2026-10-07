// Client for MAVIS's /sop API (sop/routes.py), reached through the same
// /mavis proxy prefix as the rest of the MAVIS calls (vite.config.js in dev,
// proxy/app.py in prod).
const BASE = "/mavis/sop";
const EDITOR_KEY = "sop.editor";

export function getEditor() {
  try { return localStorage.getItem(EDITOR_KEY) || ""; } catch { return ""; }
}
export function setEditor(name) {
  try { localStorage.setItem(EDITOR_KEY, name); } catch { /* private mode */ }
}

export class ApiError extends Error {
  constructor(status, detail) {
    const msg = typeof detail === "string" ? detail : detail?.message || `Request failed (${status})`;
    super(msg);
    this.status = status;
    this.detail = detail;
  }
}

async function req(path, { method = "GET", body, form } = {}) {
  const headers = {};
  const who = getEditor();
  if (who) headers["X-SOP-Editor"] = who;
  let payload;
  if (form) payload = form;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(BASE + path, { method, headers, body: payload });
  } catch {
    throw new ApiError(0, "MAVIS isn't reachable — is it running?");
  }
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) {
    if (res.status === 502) throw new ApiError(502, "MAVIS isn't running (proxy returned 502)");
    throw new ApiError(res.status, data?.detail ?? res.statusText);
  }
  return data;
}

export const sopApi = {
  status: () => req("/status"),
  meta: () => req("/meta"),
  // switch the local model that drafts rules (kept on the MAVIS machine); "" = the one in config.yaml
  setModel: (model) => req("/model", { method: "PUT", body: { model } }),
  // drafts: local to MAVIS, nothing in the database until save()
  listDrafts: () => req("/drafts"),
  // docType: "poh" (aircraft POH/SOP) | "handbook" (training handbook); handbook: afh | ifh | phak | school | other | "" (detect)
  upload: (file, docType = "poh", handbook = "") => {
    const f = new FormData(); f.append("file", file); f.append("doc_type", docType);
    if (handbook) f.append("handbook", handbook);
    return req("/drafts", { method: "POST", form: f });
  },
  // a rules file made with Export > JSON -> a draft to review (no PDF, no LLM run)
  importRules: (file) => {
    const f = new FormData(); f.append("file", file);
    return req("/drafts/import", { method: "POST", form: f });
  },
  getDraft: (id) => req(`/drafts/${id}`),
  putDraft: (id, body) => req(`/drafts/${id}`, { method: "PUT", body }),
  reparse: (id) => req(`/drafts/${id}/reparse`, { method: "POST" }),
  // run OCR + LLM over the chosen pages ('13-20, 34-63'); also used to re-run with different pages.
  // body.model: an installed Ollama model for this run ("" = the configured one)
  run: (id, body) => req(`/drafts/${id}/run`, { method: "POST", body: body || {} }),
  saveDraft: (id, body) => req(`/drafts/${id}/save`, { method: "POST", body }),
  discardDraft: (id) => req(`/drafts/${id}`, { method: "DELETE" }),
  draftPage: (id, n) => req(`/drafts/${id}/pages/${n}`),
  draftPageImage: (id, n, q = "") => `${BASE}/drafts/${id}/pages/${n}/image?q=${encodeURIComponent(q.slice(0, 200))}`,
  // saved rules (aircraft "_general" = training-handbook sets, which apply to every aircraft)
  listSets: (aircraft) => req(`/rulesets${aircraft ? `?aircraft=${encodeURIComponent(aircraft)}` : ""}`),
  getSet: (id) => req(`/rulesets/${id}`),
  renameSet: (id, name) => req(`/rulesets/${id}`, { method: "PATCH", body: { name } }),
  activateSet: (id) => req(`/rulesets/${id}/activate`, { method: "POST" }),
  deactivateSet: (id) => req(`/rulesets/${id}/deactivate`, { method: "POST" }),
  deleteSet: (id) => req(`/rulesets/${id}`, { method: "DELETE" }),
  addRule: (setId, rule) => req(`/rulesets/${setId}/rules`, { method: "POST", body: rule }),
  updateRule: (id, rule) => req(`/rules/${id}`, { method: "PATCH", body: rule }),
  deleteRule: (id) => req(`/rules/${id}`, { method: "DELETE" }),
  history: (setId, ruleId) => req(`/rulesets/${setId}/history${ruleId ? `?rule_id=${ruleId}` : ""}`),
  docPage: (doc, n) => req(`/documents/${doc}/pages/${n}`),
  docPageImage: (doc, n, q = "") => `${BASE}/documents/${doc}/pages/${n}/image?q=${encodeURIComponent(q.slice(0, 200))}`,
};
