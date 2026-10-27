import { useEffect, useRef, useState } from "react";

// "Service unavailable" banner across the top of every page. Polls the IRIS
// backend and MAVIS through the same /api and /mavis paths the pages use;
// a service only counts as down after FAILS_TO_SHOW failed checks in a row,
// so one slow answer doesn't flash it. When everything is back it says so
// briefly. If the proxy itself is down, both checks fail and so does
// everything else on the page -- the banner says that too.
const POLL_MS = 5000;
const TIMEOUT_MS = 4000;
const FAILS_TO_SHOW = 2;
const BACK_ONLINE_MS = 4000;

const SERVICES = [
  { key: "iris", url: "/api/health",
    down: "IRIS backend is unavailable — sessions, review and the leaderboard won't load or save." },
  { key: "mavis", url: "/mavis/health",
    down: "MAVIS is unavailable — live flying, coaching and scoring are paused." },
];

async function isUp(url) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(url, { cache: "no-store", signal: ctl.signal });
    return res.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(t);
  }
}

export default function ServiceBanner() {
  const [down, setDown] = useState([]);          // keys of services counted as down
  const [backOnline, setBackOnline] = useState(false);
  const fails = useRef({});

  useEffect(() => {
    let cancelled = false;
    let hideTimer = null;
    async function check() {
      const results = await Promise.all(SERVICES.map((s) => isUp(s.url)));
      if (cancelled) return;
      SERVICES.forEach((s, i) => { fails.current[s.key] = results[i] ? 0 : (fails.current[s.key] || 0) + 1; });
      const nowDown = SERVICES.filter((s) => fails.current[s.key] >= FAILS_TO_SHOW).map((s) => s.key);
      setDown((prev) => {
        if (prev.length && !nowDown.length) {
          setBackOnline(true);
          clearTimeout(hideTimer);
          hideTimer = setTimeout(() => setBackOnline(false), BACK_ONLINE_MS);
        }
        if (nowDown.length) setBackOnline(false);
        return prev.join() === nowDown.join() ? prev : nowDown;
      });
    }
    check();
    const t = setInterval(check, POLL_MS);
    return () => { cancelled = true; clearInterval(t); clearTimeout(hideTimer); };
  }, []);

  if (!down.length && !backOnline) return null;
  if (!down.length) {
    return <div className="svc-banner svc-banner--ok" role="status">✓ Back online — everything is reachable again.</div>;
  }
  const messages = down.length === SERVICES.length
    ? ["Can't reach the IRIS server — check that the services are running (the launcher restarts them automatically)."]
    : SERVICES.filter((s) => down.includes(s.key)).map((s) => s.down);
  return (
    <div className="svc-banner" role="alert">
      {messages.map((m) => <div key={m}>⚠ {m} Retrying…</div>)}
    </div>
  );
}
