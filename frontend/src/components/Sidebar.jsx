import { NavLink, useLocation } from "react-router-dom";
import LessonTree from "./LessonTree.jsx";

// The app's one main menu: MAVIS (live -- flying and coaching, the only
// thing that ever starts a session or logs anything) grouped separately
// from IRIS (view/debrief -- everything that only reads sessions MAVIS
// already created, whether in progress or finished). Live Monitor is a
// pure viewer with no start/stop control of its own now, so it lives under
// IRIS alongside Sessions/Upload even though what it shows is live.
// Curriculum's lesson tree nests directly under that one link, not the
// whole section, so it doesn't read as if it belongs under Free Flight.
// Curriculum defaults to Preflight & Cockpit (the walk-around) rather than
// the bare mode-selection screen -- that's the natural first stop before
// "Basic Flight", matching how the curriculum is meant to be entered.
const SECTIONS = [
  {
    label: "MAVIS",
    sub: "Live",
    links: [
      { to: "/live?reading=lesson00_preflight", path: "/live", label: "Curriculum", nested: <LessonTree /> },
      { to: "/live/freeflight", path: "/live/freeflight", label: "Free Flight" },
    ],
  },
  {
    label: "IRIS",
    sub: "Debrief & Analysis",
    links: [
      { to: "/monitor", path: "/monitor", label: "Live Monitor" },
      { to: "/sessions", path: "/sessions", label: "Sessions" },
      { to: "/upload", path: "/upload", label: "Upload" },
      { to: "/aoi-zones", path: "/aoi-zones", label: "AOI Zones" },
    ],
  },
];

export default function Sidebar() {
  const { pathname } = useLocation();

  return (
    <aside className="sidebar">
      <NavLink to="/" className="sidebar__brand">
        ✈ Flight Review System
      </NavLink>
      {SECTIONS.map((section) => {
        const isActive = section.links.some((l) => pathname.startsWith(l.path));
        return (
          <div className={"sidebar__section" + (isActive ? " is-active" : "")} key={section.label}>
            <div className="sidebar__section-head">
              <span className="sidebar__section-label">{section.label}</span>
              <span className="sidebar__section-sub">{section.sub}</span>
            </div>
            {section.links.map((link) => (
              <div key={link.to}>
                <NavLink to={link.to} className="sidebar__link">
                  {link.label}
                </NavLink>
                {link.nested}
              </div>
            ))}
          </div>
        );
      })}
    </aside>
  );
}
