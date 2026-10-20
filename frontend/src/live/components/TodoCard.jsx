// Lesson to-do list beside the map in the pilot view (airborne lessons,
// Take-off, Landing): every
// exercise with a tick when done, the current one expanded into short
// checklist lines + a live "now / target" readout (how far to turn, how
// much bank, how high to climb...), so the trainee doesn't have to pick the
// task out of MAVIS's spoken explanation. Driven by state.fam_todo
// (FamiliarizationEngine.todo(), or LessonEngine.todo() for lessons with
// todo_steps).
// Every readout row is drawn the same way (MAVIS core/meters.py): label,
// "now / target", and one status mark -- ✓ target met, • not yet, nothing
// for a reference reading. A hint ("falling", "watch it rise") is shown muted.
function Meter({ m }) {
  const state = m.ok === true ? " is-ok" : m.ok === false ? " is-off" : "";
  return (
    <div className={"mavis-todo__meter" + state}>
      <span className="mavis-todo__meter-label">{m.label}</span>
      <span className="mavis-todo__meter-val">
        <b>{m.now}</b>
        {m.target ? <span className="mavis-todo__meter-of"> / {m.target}</span> : null}
        {m.hint ? <span className="mavis-todo__meter-hint"> · {m.hint}</span> : null}
      </span>
      <span className="mavis-todo__status"
        aria-label={m.ok === true ? "target met" : m.ok === false ? "not yet" : undefined}>
        {m.ok === true ? "✓" : m.ok === false ? "•" : ""}
      </span>
    </div>
  );
}

export default function TodoCard({ todo }) {
  if (!todo?.steps?.length) return null;
  return (
    <div className="mavis-todo" aria-label="Lesson to-do list">
      <div className="mavis-todo__title">{todo.complete ? "Exercise complete" : "To do"}</div>
      <ol className="mavis-todo__list">
        {todo.steps.map((s) => (
          <li key={s.id} className={"mavis-todo__step is-" + s.status}>
            <span className="mavis-todo__mark" aria-hidden="true">
              {s.status === "done" ? "✓" : s.status === "skipped" ? "–" : s.status === "active" ? "▶" : ""}
            </span>
            <div className="mavis-todo__body">
              <div className="mavis-todo__name">
                {s.title}
                {s.status === "skipped" && <span className="mavis-todo__skipped"> (ran out of time)</span>}
              </div>
              {s.status === "active" && s.lines?.length > 0 && (
                <ul className="mavis-todo__lines">
                  {s.lines.map((l) => <li key={l}>{l}</li>)}
                </ul>
              )}
              {s.status === "active" && s.meters?.length > 0 && (
                <div className="mavis-todo__meters">
                  {s.meters.map((m) => <Meter key={m.label} m={m} />)}
                </div>
              )}
              {s.status === "active" && s.progress && (
                <div className="mavis-todo__progress">{s.progress}</div>
              )}
            </div>
          </li>
        ))}
      </ol>
      {todo.complete && (
        <div className="mavis-todo__done">{todo.done_text || "All done. After your score you're put back at the start of the runway."}</div>
      )}
    </div>
  );
}
