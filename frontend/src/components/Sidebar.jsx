import { NavLink } from "react-router-dom";

// The app's one main menu: MAVIS (live -- flying, coaching, real-time
// monitoring) grouped separately from IRIS (debrief -- everything that
// only makes sense once a flight is over and its data is in Postgres).
// MAVIS is the only SimConnect reader in this system, so "live" is always
// MAVIS's section regardless of which backend actually serves a given
// screen (Live Monitor is IRIS-backed, but it's a live-flight experience,
// so it belongs here, not under IRIS).
const SECTIONS = [
  {
    label: "MAVIS",
    sub: "Live",
    links: [
      { to: "/live", label: "Live Coach" },
      { to: "/monitor", label: "Live Monitor" },
    ],
  },
  {
    label: "IRIS",
    sub: "Debrief & Analysis",
    links: [
      { to: "/sessions", label: "Sessions" },
      { to: "/upload", label: "Upload" },
      { to: "/aoi-zones", label: "AOI Zones" },
    ],
  },
];

export default function Sidebar() {
  return (
    <aside className="sidebar">
      <NavLink to="/" className="sidebar__brand">
        ✈ Flight Review System
      </NavLink>
      {SECTIONS.map((section) => (
        <div className="sidebar__section" key={section.label}>
          <div className="sidebar__section-head">
            <span className="sidebar__section-label">{section.label}</span>
            <span className="sidebar__section-sub">{section.sub}</span>
          </div>
          {section.links.map((link) => (
            <NavLink key={link.to} to={link.to} className="sidebar__link">
              {link.label}
            </NavLink>
          ))}
        </div>
      ))}
    </aside>
  );
}
