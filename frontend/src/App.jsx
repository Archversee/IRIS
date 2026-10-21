import { Routes, Route, Navigate, useLocation } from "react-router-dom";
import Sidebar, { SOP_ROLES } from "./components/Sidebar.jsx";
import Home from "./pages/Home.jsx";
import Sessions from "./pages/Sessions.jsx";
import Upload from "./pages/Upload.jsx";
import Review from "./pages/Review.jsx";
import LiveSession from "./pages/LiveSession.jsx";
import Analytics from "./pages/Analytics.jsx";
import AoiEditor from "./pages/AoiEditor.jsx";
import LiveCoach from "./live/LiveCoach.jsx";
import { LeaderboardPage, ScoringGuidePage, ControlsGuidePage } from "./pages/MavisGuides.jsx";
import Login from "./pages/Login.jsx";
import Profile from "./pages/Profile.jsx";
import Accounts from "./pages/Accounts.jsx";
import SaveSession from "./pages/SaveSession.jsx";
import { useAuth } from "./auth/AuthContext.jsx";
import SopNew from "./sop/SopNew.jsx";
import SopRules from "./sop/SopRules.jsx";
import ServiceBanner from "./components/ServiceBanner.jsx";

export default function App() {
  const { user, checking } = useAuth();
  const location = useLocation();
  // The landing page is full-screen -- no sidebar there.
  const isLanding = location.pathname === "/";

  // Signed-out: only the login page. Anything else bounces there and comes
  // back to the page you asked for after signing in.
  if (checking) return <div className="login"><div className="login__checking">Loading…</div></div>;
  if (!user) {
    return location.pathname === "/login"
      ? <Login />
      : <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }

  return (
    <div className="shell">
      {!isLanding && <Sidebar />}
      <main className="shell__main">
        <ServiceBanner />
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/login" element={<Navigate to="/" replace />} />
          <Route path="/profile" element={<Profile />} />
          <Route path="/accounts" element={user.role === "admin" ? <Accounts /> : <Navigate to="/" replace />} />
          <Route path="/live" element={<LiveCoach />} />
          <Route path="/live/freeflight" element={<LiveCoach freeFlight />} />
          <Route path="/leaderboard" element={<LeaderboardPage />} />
          <Route path="/scoring" element={<ScoringGuidePage />} />
          <Route path="/controls" element={<ControlsGuidePage />} />
          {/* instructor tools (Sidebar SOP_ROLES): a trainee is sent home */}
          <Route path="/sop/new" element={SOP_ROLES.includes(user.role) ? <SopNew /> : <Navigate to="/" replace />} />
          <Route path="/sop/rules" element={SOP_ROLES.includes(user.role) ? <SopRules /> : <Navigate to="/" replace />} />
          <Route path="/monitor" element={<LiveSession />} />
          <Route path="/sessions" element={<div className="container"><Sessions /></div>} />
          <Route path="/upload" element={<div className="container"><Upload /></div>} />
          <Route path="/sessions/:id/save" element={<SaveSession />} />
          <Route path="/sessions/:id/review" element={<Review />} />
          <Route path="/sessions/:id/analytics" element={<div className="container"><Analytics /></div>} />
          <Route path="/aoi-zones" element={<div className="container"><AoiEditor /></div>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
