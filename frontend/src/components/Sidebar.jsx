import { useEffect, useRef, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import LessonTree from "./LessonTree.jsx";
import SettingsPanel from "./SettingsPanel.jsx";
import { RoleTag, ROLE_LABELS, useAuth } from "../auth/AuthContext.jsx";
import { HomeIcon, PlaneIcon, BookIcon, PulseIcon, ListIcon, GearIcon, UsersIcon, UploadIcon, ChecklistIcon, PodiumIcon, TrendIcon, TargetIcon, ToolsIcon, ChevronIcon, HelpIcon } from "./SidebarIcons.jsx";
import { useTour } from "../tour/TourProvider.jsx";

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
// Scoring Guide, Upload, AOI Zones and the SOP pages live in the collapsible
// Tools group under IRIS (open/closed is remembered per browser; it opens by
// itself when you're on one of its pages). The Leaderboard is the podium icon
// on the Settings row, flush right.
// SOP Upload / SOP Rules (turning an aircraft SOP or training handbook into
// flight rules) are instructor tools: Tools lists them only for SOP_ROLES;
// App.jsx guards the routes too.
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

const TOOLS = [
  { to: "/scoring", label: "Scoring Guide", icon: <ChecklistIcon /> },
  { to: "/upload", label: "Upload", icon: <UploadIcon /> },
  { to: "/aoi-zones", label: "AOI Zones", icon: <TargetIcon /> },
  { to: "/sop/new", label: "SOP Upload", icon: <UploadIcon />, roles: SOP_ROLES },
  { to: "/sop/rules", label: "SOP Rules", icon: <ChecklistIcon />, roles: SOP_ROLES },
];
const TOOLS_KEY = "sidebarToolsOpen";

function loadToolsOpen() {
  try { return localStorage.getItem(TOOLS_KEY) === "1"; } catch { return false; }
}

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
  const [toolsOpen, setToolsOpen] = useState(loadToolsOpen);
  const tour = useTour();
  const { user } = useAuth();
  const tools = TOOLS.filter((t) => !t.roles || t.roles.includes(user.role));
  const onToolPage = tools.some((t) => pathname.startsWith(t.to));
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

  // Landing on a tool page (link, back button) opens the group so you can see where you are.
  useEffect(() => { if (onToolPage) setToolsOpen(true); }, [onToolPage]);
  useEffect(() => {
    try { localStorage.setItem(TOOLS_KEY, toolsOpen ? "1" : "0"); } catch { /* storage unavailable -- fine */ }
  }, [toolsOpen]);

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
      <div className={"sidebar__section sidebar__tools" + (onToolPage ? " is-active" : "") + (toolsOpen ? " is-open" : "")}>
        <button type="button" className="sidebar__tools-toggle" aria-expanded={toolsOpen}
          aria-controls="sidebar-tools" title={collapsed ? (toolsOpen ? "Hide tools" : "Show tools") : undefined}
          onClick={() => setToolsOpen((o) => !o)}>
          <ToolsIcon />
          <span className="sidebar__section-label sidebar__text">TOOLS</span>
          <span className="sidebar__tools-chev"><ChevronIcon /></span>
        </button>
        {toolsOpen && (
          <div id="sidebar-tools" className="sidebar__tools-list">
            {tools.map((t) => (
              <NavLink key={t.to} to={t.to} className="sidebar__link" title={collapsed ? t.label : undefined}>
                {t.icon}
                <span className="sidebar__text">{t.label}</span>
              </NavLink>
            ))}
          </div>
        )}
      </div>
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
            + (settingsOpen ? " active" : "")}
            title={collapsed ? "Settings" : undefined} onClick={() => setSettingsOpen(true)}>
            <GearIcon />
            <span className="sidebar__text">Settings</span>
          </button>
          {/* "?" -- replay this page's tour (only on pages that have one) */}
          {tour.available && (
            <button type="button" className="sidebar__link sidebar__help" onClick={tour.start}
              title="Show the tour for this page" aria-label="Show the tour for this page">
              <HelpIcon />
            </button>
          )}
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
