import { useEffect, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";

// Review's Debrief + SOP rule checks column, popped out into its own browser
// window so it can sit on another monitor (e.g. the one on the left) while the
// videos and timeline fill the whole Review page.
//
//  - Pop out: a real window (window.open), whose content is still rendered by
//    this page through a React portal -- same data, same clicks: "▶ Watch"
//    there plays the video here.
//  - Which monitor: Chrome/Edge can place it on another screen once you allow
//    "window management" (asked once, the first time). It goes to the screen
//    left of this one (else any other screen) and fills it. If you move or
//    resize it yourself, that spot is remembered for next time.
//  - Close that window (or "Put back") and the column returns to the page.
//  - The window stays open while you move between sessions: open the next
//    session's Review and it fills with that session (it's emptied when you
//    leave Review, and closed when this page is closed or reloaded).

const NAME = "iris-review-dock";
const POS_KEY = "reviewDockPopoutPos";

let win = null;                       // the popped-out window, if open
const listeners = new Set();
const emit = () => listeners.forEach((l) => l());
function subscribe(l) { listeners.add(l); return () => listeners.delete(l); }
const current = () => (win && !win.closed ? win : null);

// Opened from this page (not a reload of it): close the pop-out with it.
if (typeof window !== "undefined") {
  window.addEventListener("pagehide", () => { try { current()?.close(); } catch { /* gone */ } });
}

// Screens, if the browser can tell us about them and we're allowed to ask without a prompt.
let screens = null;
async function loadScreens(prompt) {
  if (!("getScreenDetails" in window)) return null;
  try {
    if (!prompt) {
      const st = await navigator.permissions.query({ name: "window-management" });
      if (st.state !== "granted") return null;
    }
    screens = await window.getScreenDetails();
    return screens;
  } catch {
    return null;                      // refused, or not supported
  }
}
loadScreens(false);

function loadPos() {
  try {
    const p = JSON.parse(localStorage.getItem(POS_KEY));
    return p && [p.left, p.top, p.width, p.height].every(Number.isFinite) ? p : null;
  } catch { return null; }
}
function savePos(w) {
  try {
    localStorage.setItem(POS_KEY, JSON.stringify({ left: w.screenX, top: w.screenY, width: w.outerWidth, height: w.outerHeight }));
  } catch { /* private mode */ }
}

// The screen to the left of this one, else any other screen; filled (its available area).
function targetScreen(details) {
  if (!details?.screens?.length) return null;
  const here = details.currentScreen;
  const others = details.screens.filter((s) => s !== here);
  if (!others.length) return null;
  const left = others.filter((s) => s.availLeft < here.availLeft).sort((a, b) => b.availLeft - a.availLeft)[0];
  const s = left || others[0];
  return { left: s.availLeft, top: s.availTop, width: s.availWidth, height: s.availHeight };
}

const features = (r) => `popup,left=${Math.round(r.left)},top=${Math.round(r.top)},width=${Math.round(r.width)},height=${Math.round(r.height)}`;

// First open: copy this page's styles into the (blank) window.
function setupDoc(w, title) {
  const doc = w.document;
  doc.title = title;
  if (!doc.getElementById("popout-root")) {
    doc.head.innerHTML = '<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">';
    for (const el of document.querySelectorAll('link[rel="stylesheet"], style')) {
      const copy = el.cloneNode(true);
      if (copy.tagName === "LINK") copy.href = el.href;          // absolute: the window has no base URL
      doc.head.appendChild(copy);
    }
    doc.body.innerHTML = '<div id="popout-root"></div>';
    doc.body.className = "popout-body";
  }
  doc.documentElement.dataset.theme = document.documentElement.dataset.theme || "dark";
}

// While it shows a session: keep its theme (and, while developing, styles) in step with this page.
function follow(w) {
  const doc = w.document;
  const sync = () => { doc.documentElement.dataset.theme = document.documentElement.dataset.theme || "dark"; };
  sync();
  const mo = new MutationObserver(sync);
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  // Vite injects/updates <style> tags while developing: mirror new ones too
  const ho = new MutationObserver((recs) => {
    for (const r of recs) for (const n of r.addedNodes) {
      if (n.tagName === "STYLE" || (n.tagName === "LINK" && n.rel === "stylesheet")) doc.head.appendChild(n.cloneNode(true));
    }
  });
  ho.observe(document.head, { childList: true });
  return () => { mo.disconnect(); ho.disconnect(); };
}

/** Open (or bring to the front) the pop-out window. Call it from a click. */
export function popOut(title) {
  if (current()) { current().focus(); return; }
  const saved = loadPos();
  const target = saved || targetScreen(screens) || {
    left: window.screenX - 480 - 8, top: window.screenY, width: 480, height: window.outerHeight,
  };
  const w = window.open("", NAME, features(target));
  if (!w) return false;               // blocked
  win = w;
  setupDoc(w, title);
  // first time, and we may place it on another screen: ask, then move it there
  if (!saved && !screens && "getScreenDetails" in window) {
    loadScreens(true).then((d) => {
      const t = targetScreen(d);
      if (t && current() === w) { try { w.moveTo(t.left, t.top); w.resizeTo(t.width, t.height); } catch { /* not allowed */ } }
    });
  }
  // closed by its own X (or put back): remember where it was, return the column
  w.addEventListener("pagehide", () => {
    if (win !== w) return;
    savePos(w);
    win = null;
    emit();
  });
  emit();
  return true;
}

/** Close the pop-out; the column goes back into the page. */
export function putBack() {
  const w = current();
  if (!w) return;
  savePos(w);
  win = null;
  emit();
  w.close();
}

/** The open pop-out window, or null. Re-renders when it opens or closes. */
export function usePopout() {
  return useSyncExternalStore(subscribe, current, () => null);
}

/** Renders children into the pop-out window while it's open. */
export function PopoutPortal({ title, children }) {
  const w = usePopout();
  const [root, setRoot] = useState(null);
  useEffect(() => {
    if (!w) { setRoot(null); return undefined; }
    setupDoc(w, title);
    const undo = follow(w);
    setRoot(w.document.getElementById("popout-root"));
    return () => {
      undo();
      setRoot(null);
      try { w.document.title = "Review (open a session)"; } catch { /* closed */ }
    };
  }, [w, title]);
  return root ? createPortal(children, root) : null;
}
