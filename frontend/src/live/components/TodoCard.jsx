// Flight familiarization to-do list, top right of the pilot view: every
// exercise with a tick when done, the current one expanded into short
// checklist lines + live progress, so the trainee doesn't have to pick the
// task out of MAVIS's spoken explanation. Driven by state.fam_todo
// (FamiliarizationEngine.todo()).
export default function TodoCard({ todo }) {
  if (!todo?.steps?.length) return null;
  return (
    <div className="mavis-todo" aria-label="Lesson to-do list">
      <div className="mavis-todo__title">{todo.complete ? "Exercise complete" : "To do"}</div>
      <ol className="mavis-todo__list">
        {todo.steps.map((s) => (
          <li key={s.id} className={"mavis-todo__step is-" + s.status}>
            <span className="mavis-todo__mark" aria-hidden="true">
              {s.status === "done" ? "✓" : s.status === "active" ? "▶" : ""}
            </span>
            <div className="mavis-todo__body">
              <div className="mavis-todo__name">{s.title}</div>
              {s.status === "active" && s.lines?.length > 0 && (
                <ul className="mavis-todo__lines">
                  {s.lines.map((l) => <li key={l}>{l}</li>)}
                </ul>
              )}
              {s.status === "active" && s.progress && (
                <div className="mavis-todo__progress">{s.progress}</div>
              )}
            </div>
          </li>
        ))}
      </ol>
      {todo.complete && (
        <div className="mavis-todo__done">You found Paya Lebar. The aircraft is held; see your scorecard.</div>
      )}
    </div>
  );
}
