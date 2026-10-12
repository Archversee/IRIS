// Flight familiarization to-do list, top right of the pilot view: every
// exercise with a tick when done, the current one expanded into short
// checklist lines + a live "now / target" readout (how far to turn, how
// much bank, how high to climb...), so the trainee doesn't have to pick the
// task out of MAVIS's spoken explanation. Driven by state.fam_todo
// (FamiliarizationEngine.todo()).
function Meter({ m }) {
  const hasBar = typeof m.pct === "number";
  const state = hasBar ? (m.pct >= 1 ? " is-ok" : "") : m.ok === true ? " is-ok" : m.ok === false ? " is-off" : "";
  return (
    <div className={"mavis-todo__meter" + state}>
      <span className="mavis-todo__meter-label">{m.label}</span>
      <span className="mavis-todo__meter-val">
        <b>{m.now}</b>{m.target ? <span className="mavis-todo__meter-of"> / {m.target}</span> : null}
      </span>
      {hasBar ? (
        <span className="mavis-todo__bar"><i style={{ width: `${Math.round(m.pct * 100)}%` }} /></span>
      ) : (
        <span className="mavis-todo__dot" aria-label={m.ok ? "in range" : "out of range"} />
      )}
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
