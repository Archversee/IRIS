import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { api } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import { TOURS } from "./tours.jsx";
import TourOverlay from "./TourOverlay.jsx";

// Page tours: a short guided walk through the page you're on.
//
//  - Each page's tour shows by itself the first time a user opens that page
//    (tours with `auto: false`, e.g. the in-flight screen, never do).
//  - The "?" button in the sidebar replays the current page's tour anytime,
//    even with tours switched off.
//  - Settings > Help & Guidance: tours On/Off and "Reset tours".
//  - What's been seen is saved on the user's account (users.ui_prefs via
//    PATCH /auth/me/prefs), not in the browser, because trainees share the
//    sim PC. Until the server answers, the change is kept in memory so the
//    same tour doesn't pop up twice.
//
// Tours live in tours.js. A tour applies when its `match` accepts the URL
// and its `when` element is on screen (one URL can show different screens,
// e.g. Live Monitor's picker vs a live session).

const TourContext = createContext(null);
const FIND_FOR_MS = 6000; // pages load data first; keep looking for the tour's `when` this long
const AUTO_DELAY_MS = 700; // let the page settle before the tour dims it

// A tour only counts if at least one of its steps can show right now
// (otherwise it would open and close at once, and be marked as seen).
function canShow(t, role) {
  return t.steps.some((s) => (!s.roles || s.roles.includes(role)) && (!s.target || document.querySelector(s.target)));
}

function findTour(loc, role) {
  return TOURS.find((t) =>
    t.match(loc) && (!t.roles || t.roles.includes(role)) && (!t.when || document.querySelector(t.when))
    && canShow(t, role));
}

export function TourProvider({ children }) {
  const { user, setUser } = useAuth();
  const loc = useLocation();
  const [available, setAvailable] = useState(null); // tour for what's on screen now
  const [running, setRunning] = useState(null); // tour being shown
  const seenLocal = useRef(new Set()); // seen this visit, before the server confirms

  const prefs = user?.ui_prefs || {};
  const enabled = prefs.tours_enabled !== false;
  const seen = useMemo(() => new Set([...(prefs.tours_seen || []), ...seenLocal.current]), [prefs.tours_seen]); // eslint-disable-line react-hooks/exhaustive-deps

  const savePrefs = useCallback(async (body) => {
    try {
      setUser(await api.updatePrefs(body));
    } catch {
      // backend down / older backend without /auth/me/prefs: keep the local copy
      setUser((u) => {
        if (!u) return u;
        const p = { ...(u.ui_prefs || {}) };
        if (body.tours_enabled != null) p.tours_enabled = body.tours_enabled;
        if (body.tours_reset) p.tours_seen = [];
        if (body.tours_seen_add) p.tours_seen = [...new Set([...(p.tours_seen || []), ...body.tours_seen_add])];
        return { ...u, ui_prefs: p };
      });
    }
  }, [setUser]);

  // Which tour fits the screen? Pages render after their data arrives, so
  // look a few times; screens that change without a URL change (Live
  // Monitor picking up a session, a lesson starting) are caught by the poll.
  const key = loc.pathname + loc.search;
  useEffect(() => {
    if (!user) return undefined;
    let stop = false;
    const started = Date.now();
    let last = null;
    setAvailable(null); // new page: forget the last page's tour until this one's is found
    const look = () => {
      if (stop) return;
      const t = findTour(loc, user.role) || null;
      if (t?.id !== last?.id) { last = t; setAvailable(t); }
      if (!t && Date.now() - started < FIND_FOR_MS) timer = setTimeout(look, 300);
      else timer = setTimeout(look, 2000); // keep an eye out for screen changes
    };
    let timer = setTimeout(look, 150);
    return () => { stop = true; clearTimeout(timer); };
  }, [key, user?.role]); // eslint-disable-line react-hooks/exhaustive-deps

  // First visit: start by itself.
  useEffect(() => {
    const already = () => seen.has(available?.id) || seenLocal.current.has(available?.id);
    if (!available || running || !enabled || available.auto === false || already()) return undefined;
    const t = setTimeout(() => {
      // a dialog already open (confirm, settings drawer) wins -- try next visit
      if (already() || document.querySelector('[aria-modal="true"]')) return;
      setRunning(available);
    }, AUTO_DELAY_MS);
    return () => clearTimeout(t);
  }, [available, running, enabled, seen]);

  // Leaving the page ends its tour.
  useEffect(() => { setRunning(null); }, [loc.pathname]);

  const finish = useCallback(({ disableAll = false } = {}) => {
    const t = running;
    setRunning(null);
    if (!t) return;
    seenLocal.current.add(t.id);
    savePrefs({ tours_seen_add: [t.id], ...(disableAll ? { tours_enabled: false } : {}) });
  }, [running, savePrefs]);

  const value = useMemo(() => ({
    available,
    running,
    enabled,
    start: () => available && setRunning(available),
    setEnabled: (on) => savePrefs({ tours_enabled: on }),
    resetSeen: () => { seenLocal.current.clear(); return savePrefs({ tours_reset: true }); },
  }), [available, running, enabled, savePrefs]);

  return (
    <TourContext.Provider value={value}>
      {children}
      {running && <TourOverlay key={running.id} tour={running} role={user?.role} onDone={finish} />}
    </TourContext.Provider>
  );
}

export function useTour() {
  return useContext(TourContext) || { available: null, running: null, enabled: false, start() {}, setEnabled() {}, resetSeen() {} };
}
