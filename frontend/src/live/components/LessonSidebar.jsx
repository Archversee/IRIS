import { useState } from "react";

// Accordion over MAVIS's state.lesson_catalog: {title, modules:[{title,
// subtitle, lessons:[{id,title,subtitle,duration_min,available,kind}]}]}.
// "reading"-kind lessons (preflight/cockpit walkthroughs, no telemetry)
// are selectable here but don't get their own narration view yet -- that's
// a later slice (see docs/LIVE_REBUILD_PLAN.md, Slice I).
export default function LessonSidebar({ catalog, activeLessonId, onSelect }) {
  const [openModule, setOpenModule] = useState(0);

  if (!catalog?.modules?.length) {
    return <div className="mavis-sidebar mavis-sidebar--empty">Loading lessons…</div>;
  }

  return (
    <nav className="mavis-sidebar">
      <div className="mavis-sidebar__title">{catalog.title}</div>
      {catalog.modules.map((mod, i) => (
        <div className={"mavis-mod" + (openModule === i ? " is-open" : "")} key={mod.title ?? i}>
          <button
            type="button"
            className="mavis-mod__head"
            onClick={() => setOpenModule(openModule === i ? -1 : i)}
          >
            <span>{mod.title}</span>
            {mod.subtitle && <span className="mavis-mod__sub">{mod.subtitle}</span>}
          </button>
          {openModule === i && (
            <ul className="mavis-mod__lessons">
              {mod.lessons.map((les) => (
                <li key={les.id}>
                  <button
                    type="button"
                    className={
                      "mavis-lesson" +
                      (les.id === activeLessonId ? " is-active" : "") +
                      (les.available === false ? " is-locked" : "")
                    }
                    disabled={les.available === false}
                    onClick={() => onSelect(les)}
                  >
                    <span>{les.title}</span>
                    {les.kind === "reading" && <span className="mavis-lesson__tag">reading</span>}
                    {les.duration_min ? <span className="mavis-lesson__dur">{les.duration_min}m</span> : null}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </nav>
  );
}
