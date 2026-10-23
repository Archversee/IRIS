import { useEffect, useRef, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import LessonTree from "./LessonTree.jsx";
import SettingsPanel from "./SettingsPanel.jsx";
import { RoleTag, ROLE_LABELS, useAuth } from "../auth/AuthContext.jsx";
import { HomeIcon, PlaneIcon, BookIcon, PulseIcon, ListIcon, GearIcon, UsersIcon, UploadIcon, ChecklistIcon, PodiumIcon, TrendIcon } from "./SidebarIcons.jsx";

// The app's one main menu: MAVIS (live -- flying and coaching, the only
// thing that ever starts a session or logs anything) grouped separately
// from IRIS (view/debrief -- everything that only reads sessions MAVIS
// already created, whether in progress or finished). Live Monitor is a
// pure viewer with no start/stop control of its own now, so it lives under
// IRIS alongside Sessions even though what it shows is live.
// Curriculum's lesson tree nests directly under that one link, not the
// whole section, so it doesn't read as if it belongs under Free Flight.
// Curriculum defaults to Preflight & Cockpit (the walk-around) rather than
// the bare mode-selection screen -- that's the natural first stop before
// "Basic Flight", matching how the curriculum is meant to be entered.
// Scoring Guide, Upload and AOI Zones are reached from the Settings drawer;
// the Leaderboard is the podium icon on the Settings row, flush right.
// SOP Upload / SOP Rules (turning an aircraft SOP or training handbook into
// flight rules) are instructor tools: they're reached from the Settings
// drawer, which shows them only to SOP_ROLES; App.jsx guards the routes too.
export const SOP_ROLES = ["instructor", "admin"];
const SECTIONS = [
  {
    label: "MAVIS",
    sub: "Live",
    links: [
      { to: "/live?reading=lesson00_preflight", path: "/live", label: "Curriculum", icon: <BookIcon />, nested: <LessonTree />, also: ["/controls"] },
      { to: "/live/freeflight", path: "/live/freeflight", label: "Free Flight", icon: <PlaneIcon /> },
    ],
  },
  {
    label: "IRIS",
    sub: "Debrief & Analysis",
    links: [
      { to: "/monitor", path: "/monitor", label: "Live Monitor", icon: <PulseIcon /> },
      { to: "/sessions", path: "/sessions", label: "Sessions", icon: <ListIcon /> },
      { to: "/progress", path: "/progress", label: "Progress", icon: <TrendIcon /> },
    ],
  },
];

// Pages opened from the Settings drawer -- the gear lights up on these.
const SETTINGS_PATHS = ["/leaderboard", "/scoring", "/upload", "/aoi-zones", "/sop"];

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
  const { user } = useAuth();
  const displayName = user.full_name || user.username;
  const initials = (displayName.match(/\b\w/g) || ["?"]).slice(0, 2).join("").toUpperCase();
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
        const links = section.links.filter((l) => !l.roles || l.roles.includes(user.role));
        const isActive = links.some((l) =>
          (l.path && pathname.startsWith(l.path)) || (l.also || []).some((p) => pathname.startsWith(p)));
        return (
          <div className={"sidebar__section" + (isActive ? " is-active" : "")} key={section.label}>
            <div className="sidebar__section-head">
              <span className="sidebar__section-label">{section.label}</span>
              <span className="sidebar__section-sub sidebar__text">{section.sub}</span>
            </div>
            {links.map((link) => (
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
        {user.role === "admin" && (
          <NavLink to="/accounts" className="sidebar__link" title={collapsed ? "Accounts" : undefined}>
            <UsersIcon />
            <span className="sidebar__text">Accounts</span>
          </NavLink>
        )}
        {/* Signed-in user + role tag -> Profile */}
        <NavLink to="/profile" className="sidebar__user"
          title={collapsed ? `${displayName} · ${ROLE_LABELS[user.role]}` : "Your profile"}>
          <span className={`sidebar__avatar sidebar__avatar--${user.role}`}>{initials}</span>
          <span className="sidebar__text sidebar__user-text">
            <span className="sidebar__user-name">{displayName}</span>
            <RoleTag role={user.role} small />
          </span>
        </NavLink>
        <div className="sidebar__settings-row">
          <button type="button" className={"sidebar__link sidebar__settings"
            + (settingsOpen || SETTINGS_PATHS.some((p) => pathname.startsWith(p)) ? " active" : "")}
            title={collapsed ? "Settings" : undefined} onClick={() => setSettingsOpen(true)}>
            <GearIcon />
            <span className="sidebar__text">Settings</span>
          </button>
          <NavLink to="/leaderboard" className="sidebar__link sidebar__lb" title="Leaderboard" aria-label="Leaderboard">
            <PodiumIcon />
          </NavLink>
        </div>
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
