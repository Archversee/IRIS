import { useEffect, useState } from "react";

// A tab left open across a rebuild keeps running the old app (it's a single
// page: moving between pages never reloads it). Every minute, compare the
// build this tab is running with the one the server now serves, and offer a
// reload when they differ. Only the built app (frontend/dist via the proxy)
// has a hashed bundle to compare -- in `npm run dev` this never shows.
const CHECK_MS = 60_000;
const BUNDLE = /\/assets\/index-[\w-]+\.js/;

function runningBundle() {
  const el = document.querySelector('script[type="module"][src*="/assets/index-"]');
  return el ? (el.getAttribute("src").match(BUNDLE) || [])[0] : null;
}

export default function UpdateNotice() {
  const [stale, setStale] = useState(false);

  useEffect(() => {
    const mine = runningBundle();
    if (!mine) return undefined;
    let cancelled = false;
    async function check() {
      try {
        const html = await (await fetch("/", { cache: "no-store" })).text();
        const served = (html.match(BUNDLE) || [])[0];
        if (!cancelled && served && served !== mine) setStale(true);
      } catch {
        // offline / proxy down: the service banner covers that
      }
    }
    const t = setInterval(check, CHECK_MS);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  if (!stale) return null;
  return (
    <div className="svc-banner svc-banner--update" role="status">
      A newer version of IRIS is available.{" "}
      <button type="button" onClick={() => window.location.reload()}>Reload</button>
    </div>
  );
}
