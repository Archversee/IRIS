import { useState } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
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
  const [sidebarOpen, setSidebarOpen] = useState(true);

  return (
    <div className="shell">
      {sidebarOpen && <Sidebar />}
      <button
        type="button"
        className="shell__sidebar-toggle"
        style={{ left: sidebarOpen ? 220 : 0 }}
        title={sidebarOpen ? "Hide sidebar" : "Show sidebar"}
        onClick={() => setSidebarOpen((v) => !v)}
      >
        {sidebarOpen ? "‹" : "›"}
      </button>
      <main className="shell__main">
        <Routes>
          <Route path="/" element={<div className="container"><Home /></div>} />
          <Route path="/live" element={<LiveCoach />} />
          <Route path="/live/freeflight" element={<LiveCoach freeFlight />} />
          <Route path="/monitor" element={<LiveSession />} />
          <Route path="/sessions" element={<div className="container"><Sessions /></div>} />
          <Route path="/upload" element={<div className="container"><Upload /></div>} />
          <Route path="/sessions/:id/review" element={<Review />} />
          <Route path="/sessions/:id/analytics" element={<div className="container"><Analytics /></div>} />
          <Route path="/aoi-zones" element={<div className="container"><AoiEditor /></div>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
