import { useSyncExternalStore } from "react";

// Same-origin path -- rewritten to MAVIS's own /ws by the dev proxy
// (vite.config.js) and by the production reverse proxy alike, so this
// never needs an env-specific base URL the way api/client.js's BASE does.
const WS_PATH = "/mavis/ws";

// MAVIS pushes its *entire* app state over this socket ~5x/sec (see
// web/server.py) -- no delta/diffing on the wire. This store just holds
// the latest full object; per-field selector hooks with their own
// memoization are a later-slice concern once real UI reads from this
// (see docs/LIVE_REBUILD_PLAN.md), not needed for the raw-dump proof here.
function createMavisStore() {
  let state = null;
  let status = "connecting"; // connecting | open | closed
  const listeners = new Set();
  let reconnectTimer = null;

  function notify() {
    for (const l of listeners) l();
  }

  function connect() {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${window.location.host}${WS_PATH}`);
    ws.onopen = () => {
      status = "open";
      notify();
    };
    ws.onmessage = (e) => {
      try {
        state = JSON.parse(e.data);
      } catch {
        return;
      }
      notify();
    };
    ws.onerror = () => ws.close();
    ws.onclose = () => {
      status = "closed";
      notify();
      // Matches MAVIS's own client reconnect cadence (web/static/index.html).
      reconnectTimer = setTimeout(connect, 1500);
    };
  }

  connect();

  return {
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    getState() {
      return state;
    },
    getStatus() {
      return status;
    },
  };
}

// Module-level singleton: one live socket shared by every component that
// reads MAVIS state, regardless of how many are mounted.
let store = null;
function getStore() {
  if (!store) store = createMavisStore();
  return store;
}

export function useMavisState() {
  const s = getStore();
  return useSyncExternalStore(s.subscribe, s.getState);
}

export function useMavisStatus() {
  const s = getStore();
  return useSyncExternalStore(s.subscribe, s.getStatus);
}
