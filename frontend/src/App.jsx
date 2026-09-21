import { Routes, Route, NavLink, Navigate, useLocation } from "react-router-dom";
import Sessions from "./pages/Sessions.jsx";
import Upload from "./pages/Upload.jsx";
import Review from "./pages/Review.jsx";
import LiveSession from "./pages/LiveSession.jsx";
import Analytics from "./pages/Analytics.jsx";
import AoiEditor from "./pages/AoiEditor.jsx";

export default function App() {
  const { pathname } = useLocation();
  const isFullscreenPage = /^\/(sessions\/[^/]+\/review|live)$/.test(pathname);

  return (
    <>
      {!isFullscreenPage && (
        <nav className="nav">
          <span className="brand">✈ Flight Review System</span>
          <NavLink to="/sessions">Sessions</NavLink>
          <NavLink to="/upload">Upload</NavLink>
          <NavLink to="/live">Live</NavLink>
          <NavLink to="/aoi-zones">AOI Zones</NavLink>
        </nav>
      )}
      <Routes>
        <Route path="/" element={<Navigate to="/sessions" replace />} />
        <Route path="/sessions" element={<div className="container"><Sessions /></div>} />
        <Route path="/upload" element={<div className="container"><Upload /></div>} />
        <Route path="/sessions/:id/review" element={<Review />} />
        <Route path="/live" element={<LiveSession />} />
        <Route path="/sessions/:id/analytics" element={<div className="container"><Analytics /></div>} />
        <Route path="/aoi-zones" element={<div className="container"><AoiEditor /></div>} />
      </Routes>
    </>
  );
}
