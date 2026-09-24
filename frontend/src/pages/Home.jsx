import { NavLink } from "react-router-dom";

export default function Home() {
  return (
    <div>
      <h1>Flight Review System</h1>
      <p className="muted">
        Fly with MAVIS's live AI coaching, then review the flight -- telemetry, coaching
        callouts, eye-tracking and video -- in IRIS once it's over.
      </p>
      <div className="row" style={{ marginTop: 24 }}>
        <div className="col card">
          <h3>MAVIS -- fly now</h3>
          <p className="muted">Live AI coaching and real-time flight monitoring.</p>
          <NavLink to="/live?reading=lesson00_preflight"><button>Live Coach</button></NavLink>
        </div>
        <div className="col card">
          <h3>IRIS -- review</h3>
          <p className="muted">Past sessions, manual uploads, and AOI zone setup.</p>
          <NavLink to="/sessions"><button className="secondary">Sessions</button></NavLink>
        </div>
      </div>
    </div>
  );
}
