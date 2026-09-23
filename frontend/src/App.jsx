import { Routes, Route, Navigate, useLocation } from "react-router-dom";
import Sidebar from "./components/Sidebar.jsx";
import Home from "./pages/Home.jsx";
import Sessions from "./pages/Sessions.jsx";
import Upload from "./pages/Upload.jsx";
import Review from "./pages/Review.jsx";
import LiveSession from "./pages/LiveSession.jsx";
import Analytics from "./pages/Analytics.jsx";
import AoiEditor from "./pages/AoiEditor.jsx";
import LiveCoach from "./live/LiveCoach.jsx";

export default function App() {
  const { pathname } = useLocation();
  // Only the per-session Review page stays fullscreen/sidebar-less, for an
  // immersive watch. /live and /monitor both keep the sidebar now.
  const isFullscreenPage = /^\/sessions\/[^/]+\/review$/.test(pathname);

  if (isFullscreenPage) {
    return (
      <Routes>
        <Route path="/sessions/:id/review" element={<Review />} />
      </Routes>
    );
  }

  return (
    <div className="shell">
      <Sidebar />
      <main className="shell__main">
        <Routes>
          <Route path="/" element={<div className="container"><Home /></div>} />
          <Route path="/live" element={<LiveCoach />} />
          <Route path="/monitor" element={<LiveSession />} />
          <Route path="/sessions" element={<div className="container"><Sessions /></div>} />
          <Route path="/upload" element={<div className="container"><Upload /></div>} />
          <Route path="/sessions/:id/analytics" element={<div className="container"><Analytics /></div>} />
          <Route path="/aoi-zones" element={<div className="container"><AoiEditor /></div>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
