import { Routes, Route, NavLink, Navigate, useLocation } from "react-router-dom";
import Sessions from "./pages/Sessions.jsx";
import Upload from "./pages/Upload.jsx";
import Review from "./pages/Review.jsx";
import Analytics from "./pages/Analytics.jsx";

export default function App() {
  const { pathname } = useLocation();
  const isReview = /^\/sessions\/[^/]+\/review$/.test(pathname);

  return (
    <>
      {!isReview && (
        <nav className="nav">
          <span className="brand">✈ Flight Review System</span>
          <NavLink to="/sessions">Sessions</NavLink>
          <NavLink to="/upload">Upload</NavLink>
        </nav>
      )}
      <Routes>
        <Route path="/" element={<Navigate to="/sessions" replace />} />
        <Route path="/sessions" element={<div className="container"><Sessions /></div>} />
        <Route path="/upload" element={<div className="container"><Upload /></div>} />
        <Route path="/sessions/:id/review" element={<Review />} />
        <Route path="/sessions/:id/analytics" element={<div className="container"><Analytics /></div>} />
      </Routes>
    </>
  );
}
