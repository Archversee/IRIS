import { Fragment, useEffect, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useMavisState } from "../live/hooks/useMavisSocket.js";
import { mavisApi } from "../live/api/mavisApi.js";

// Expandable module -> lesson tree over MAVIS's live state.lesson_catalog,
// living in the persistent sidebar so any lesson/module is reachable from
// anywhere in the app. "reading"-kind lessons (preflight/cockpit) are
// never selected server-side (see /live's own comment on this) -- they're
// shown entirely client-side via /live?reading=<id>, since MAVIS's
// state.reading_lessons has every reading lesson's data preloaded
// regardless of which flying lesson is currently active. Flying lessons
// still go through POST /lesson/select as normal.
export default function LessonTree() {
  const state = useMavisState();
  const catalog = state?.lesson_catalog;
  const [searchParams] = useSearchParams();
  const { pathname } = useLocation(); // must come before readingId/activeLessonId below
  // Highlight what's actually on screen: a reading lesson is only in the
  // URL (?reading=<id>), never MAVIS's lesson_id, so check that first; a
  // flying lesson is MAVIS's selected lesson, shown on plain /live.
  // Elsewhere in the app nothing in the tree is "open", so nothing is lit.
  const readingId = pathname === "/live" ? searchParams.get("reading") : null;
  const activeLessonId = readingId || (pathname === "/live" ? state?.lesson_id : null);
  const [openModule, setOpenModule] = useState(null);
  const nav = useNavigate();

  // Jumping to a lesson (sidebar, landing page, Curriculum link) re-opens
  // whichever module holds it, so its highlight is always visible.
  useEffect(() => { setOpenModule(null); }, [activeLessonId]);

  if (!catalog?.modules?.length) {
    return <div className="sidebar__lesson-tree sidebar__lesson-tree--empty">Waiting for MAVIS…</div>;
  }

  async function pick(lesson) {
    if (lesson.kind === "reading") {
      nav(`/live?reading=${encodeURIComponent(lesson.id)}`);
      return;
    }
    try {
      await mavisApi.selectLesson(lesson.id);
    } catch {
      // best-effort -- /live's own menu will show the real error state if this failed
    }
    nav("/live");
  }

  return (
    <div className="sidebar__lesson-tree">
      {catalog.modules.map((mod, i) => {
        // flying lessons highlight only while on /live
        const isOn = (l) => pathname.startsWith("/live") && l.kind !== "reading" && l.id === activeLessonId;
        const open = openModule === i || (openModule === null && mod.lessons.some(isOn));
        return (
          <Fragment key={mod.title ?? i}>
          <div className="sidebar__module">
            <button
              type="button"
              className="sidebar__module-head"
              onClick={() => setOpenModule(open ? -1 : i)}
            >
              {mod.title}
            </button>
            {open && (
              <div className="sidebar__module-lessons">
                {/* Lessons are numbered within their module (1, 2, 3...). */}
                {mod.lessons.map((les, n) => (
                  <button
                    type="button"
                    key={les.id}
                    className={"sidebar__lesson" + (isOn(les) ? " is-active" : "")}
                    disabled={les.available === false}
                    onClick={() => pick(les)}
                  >
                    <span className="sidebar__lesson-num">{n + 1}.</span>
                    <span>{les.title}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
          {/* Controls guide page (not a MAVIS lesson), between Preflight &
              Cockpit and Basic Flight. */}
          {mod.id === "preflight" && (
            <div className="sidebar__module">
              <button type="button"
                className={"sidebar__module-head" + (pathname === "/controls" ? " is-active" : "")}
                onClick={() => nav("/controls")}>
                Control Guide
              </button>
            </div>
          )}
          </Fragment>
        );
      })}
    </div>
  );
}
