import { useEffect, useRef, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import LessonTree from "./LessonTree.jsx";
import SettingsPanel from "./SettingsPanel.jsx";
import { HomeIcon, PlaneIcon, BookIcon, PulseIcon, ListIcon, UploadIcon, TargetIcon, GearIcon } from "./SidebarIcons.jsx";

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
      { to: "/live?reading=lesson00_preflight", path: "/live", label: "Curriculum", icon: <BookIcon />, nested: <LessonTree /> },
      { to: "/live/freeflight", path: "/live/freeflight", label: "Free Flight", icon: <PlaneIcon /> },
    ],
  },
  {
    label: "IRIS",
    sub: "Debrief & Analysis",
    links: [
      { to: "/monitor", path: "/monitor", label: "Live Monitor", icon: <PulseIcon /> },
      { to: "/sessions", path: "/sessions", label: "Sessions", icon: <ListIcon /> },
      { to: "/upload", path: "/upload", label: "Upload", icon: <UploadIcon /> },
      { to: "/aoi-zones", path: "/aoi-zones", label: "AOI Zones", icon: <TargetIcon /> },
    ],
  },
];

// Drag the right edge to resize. Released below COLLAPSE_BELOW it snaps to
// icon-only; otherwise it stays at the dragged width and shows full names.
const COLLAPSED_W = 56;
const COLLAPSE_BELOW = 130;
const DEFAULT_W = 190;
const MAX_W = 240;
const STORAGE_KEY = "sidebarWidth";

function loadWidth() {
  try {
    const w = Number(localStorage.getItem(STORAGE_KEY));
    return w >= COLLAPSED_W && w <= MAX_W ? w : DEFAULT_W;
  } catch {
    return DEFAULT_W;
  }
}

export default function Sidebar() {
  const { pathname } = useLocation();
  const [width, setWidth] = useState(loadWidth);
  const [dragging, setDragging] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const lastExpanded = useRef(width > COLLAPSED_W ? width : DEFAULT_W);
  const collapsed = width < COLLAPSE_BELOW;

  useEffect(() => {
    if (dragging) return;
    try { localStorage.setItem(STORAGE_KEY, String(width)); } catch { /* storage unavailable -- fine */ }
  }, [width, dragging]);

  function startDrag(e) {
    e.preventDefault();
    setDragging(true);
    const onMove = (ev) => setWidth(Math.min(MAX_W, Math.max(COLLAPSED_W, ev.clientX)));
    const onUp = (ev) => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      const w = Math.min(MAX_W, Math.max(COLLAPSED_W, ev.clientX));
      if (w < COLLAPSE_BELOW) {
        setWidth(COLLAPSED_W);
      } else {
        setWidth(w);
        lastExpanded.current = w;
      }
      setDragging(false);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }

  // Double-clicking the edge flips between icon-only and the last full width.
  function toggle() {
    setWidth(collapsed ? lastExpanded.current : COLLAPSED_W);
  }

  return (
    <aside
      className={"sidebar" + (collapsed ? " is-collapsed" : "") + (dragging ? " is-dragging" : "")}
      style={{ width }}
    >
      <NavLink to="/" className="sidebar__brand" title="Flight Review System">
        <HomeIcon />
        <span className="sidebar__text">Flight Review System</span>
      </NavLink>
      {SECTIONS.map((section) => {
        const isActive = section.links.some((l) => l.path && pathname.startsWith(l.path));
        return (
          <div className={"sidebar__section" + (isActive ? " is-active" : "")} key={section.label}>
            <div className="sidebar__section-head">
              <span className="sidebar__section-label">{section.label}</span>
              <span className="sidebar__section-sub sidebar__text">{section.sub}</span>
            </div>
            {section.links.map((link) => (
              <div key={link.to}>
                {link.disabled ? (
                  <span className="sidebar__link is-disabled" aria-disabled="true"
                    title={`${link.label} (coming soon)`}>
                    {link.icon}
                    <span className="sidebar__text">{link.label}</span>
                  </span>
                ) : (
                  <NavLink to={link.to} className="sidebar__link" title={collapsed ? link.label : undefined}>
                    {link.icon}
                    <span className="sidebar__text">{link.label}</span>
                  </NavLink>
                )}
                {!collapsed && link.nested}
              </div>
            ))}
          </div>
        );
      })}
      <div className="sidebar__footer">
        <button type="button" className={"sidebar__link sidebar__settings" + (settingsOpen ? " active" : "")}
          title={collapsed ? "Settings" : undefined} onClick={() => setSettingsOpen(true)}>
          <GearIcon />
          <span className="sidebar__text">Settings</span>
        </button>
      </div>
      {settingsOpen && <SettingsPanel onClose={() => setSettingsOpen(false)} />}
      <div
        className="sidebar__resizer"
        role="separator"
        aria-orientation="vertical"
        title="Drag to resize · double-click to collapse/expand"
        onPointerDown={startDrag}
        onDoubleClick={toggle}
      />
    </aside>
  );
}
