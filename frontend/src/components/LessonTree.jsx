import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
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
  const activeLessonId = state?.lesson_id;
  const [openModule, setOpenModule] = useState(null);
  const nav = useNavigate();
  const { pathname } = useLocation();

  if (!catalog?.modules?.length) {
    return <div className="sidebar__lesson-tree sidebar__lesson-tree--empty">Waiting for MAVIS…</div>;
  }

  async function pick(lesson) {
    if (lesson.kind === "sop") {          // Aircraft SOP pages, not a flight
      nav(lesson.route || "/sop/rules");
      return;
    }
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
        // SOP pages highlight by route; flying lessons only while on /live
        const isOn = (l) => (l.kind === "sop" ? pathname === l.route
          : pathname.startsWith("/live") && l.kind !== "reading" && l.id === activeLessonId);
        const open = openModule === i || (openModule === null && mod.lessons.some(isOn));
        return (
          <div key={mod.title ?? i} className="sidebar__module">
            <button
              type="button"
              className="sidebar__module-head"
              onClick={() => setOpenModule(open ? -1 : i)}
            >
              {mod.title}
            </button>
            {open && (
              <div className="sidebar__module-lessons">
                {mod.lessons.map((les) => (
                  <button
                    type="button"
                    key={les.id}
                    className={"sidebar__lesson" + (isOn(les) ? " is-active" : "")}
                    disabled={les.available === false}
                    onClick={() => pick(les)}
                  >
                    {les.title}
                  </button>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
