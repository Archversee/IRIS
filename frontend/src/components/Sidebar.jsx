import { NavLink, useLocation } from "react-router-dom";
import LessonTree from "./LessonTree.jsx";

// The app's one main menu: MAVIS (live -- flying, coaching, real-time
// monitoring) grouped separately from IRIS (debrief -- everything that
// only makes sense once a flight is over and its data is in Postgres).
// MAVIS is the only SimConnect reader in this system, so "live" is always
// MAVIS's section regardless of which backend actually serves a given
// screen (Live Monitor is IRIS-backed, but it's a live-flight experience,
// so it belongs here, not under IRIS). Curriculum's lesson tree nests
// directly under that one link, not the whole section, so it doesn't read
// as if it belongs under Live Monitor.
// Curriculum defaults to Preflight & Cockpit (the walk-around) rather than
// the bare mode-selection screen -- that's the natural first stop before
// "Basic Flight", matching how the curriculum is meant to be entered.
const SECTIONS = [
  {
    label: "MAVIS",
    sub: "Live",
    links: [
      { to: "/live?reading=lesson00_preflight", path: "/live", label: "Curriculum", nested: <LessonTree /> },
      { to: "/monitor", path: "/monitor", label: "Live Monitor" },
    ],
  },
  {
    label: "IRIS",
    sub: "Debrief & Analysis",
    links: [
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
