import { Routes, Route, NavLink, Navigate } from "react-router-dom";
import Sessions from "./pages/Sessions.jsx";
import Upload from "./pages/Upload.jsx";
import Review from "./pages/Review.jsx";
import Analytics from "./pages/Analytics.jsx";

export default function App() {
  return (
    <>
      <nav className="nav">
        <span className="brand">✈ Flight Review System</span>
        <NavLink to="/sessions">Sessions</NavLink>
        <NavLink to="/upload">Upload</NavLink>
      </nav>
      <div className="container">
        <Routes>
          <Route path="/" element={<Navigate to="/sessions" replace />} />
          <Route path="/sessions" element={<Sessions />} />
          <Route path="/upload" element={<Upload />} />
          <Route path="/sessions/:id/review" element={<Review />} />
          <Route path="/sessions/:id/analytics" element={<Analytics />} />
        </Routes>
      </div>
    </>
  );
}
