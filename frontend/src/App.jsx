import { Routes, Route, NavLink, Navigate, useLocation } from "react-router-dom";
import Sessions from "./pages/Sessions.jsx";
import Upload from "./pages/Upload.jsx";
import Review from "./pages/Review.jsx";
import LiveSession from "./pages/LiveSession.jsx";
import Analytics from "./pages/Analytics.jsx";
import AoiEditor from "./pages/AoiEditor.jsx";
import LiveCoach from "./live/LiveCoach.jsx";

export default function App() {
  const { pathname } = useLocation();
  // /live (MAVIS coaching) and /monitor (IRIS eye+flight spectator) are
  // both fullscreen, nav-less screens -- same as /review always was.
  const isFullscreenPage = /^\/(sessions\/[^/]+\/review|live|monitor)$/.test(pathname);

  return (
    <>
      {!isFullscreenPage && (
        <nav className="nav">
          <span className="brand">✈ Flight Review System</span>
          <NavLink to="/sessions">Sessions</NavLink>
          <NavLink to="/upload">Upload</NavLink>
          <NavLink to="/live">Live Coach</NavLink>
          <NavLink to="/monitor">Monitor</NavLink>
          <NavLink to="/aoi-zones">AOI Zones</NavLink>
        </nav>
      )}
      <Routes>
        <Route path="/" element={<Navigate to="/sessions" replace />} />
        <Route path="/sessions" element={<div className="container"><Sessions /></div>} />
        <Route path="/upload" element={<div className="container"><Upload /></div>} />
        <Route path="/sessions/:id/review" element={<Review />} />
        {/* MAVIS live coaching HUD -- talks to MAVIS (:6945), not IRIS's backend. */}
        <Route path="/live" element={<LiveCoach />} />
        {/* IRIS's own eye+flight spectator view (was /live before the MAVIS rebuild). */}
        <Route path="/monitor" element={<LiveSession />} />
        <Route path="/sessions/:id/analytics" element={<div className="container"><Analytics /></div>} />
        <Route path="/aoi-zones" element={<div className="container"><AoiEditor /></div>} />
      </Routes>
    </>
  );
}
