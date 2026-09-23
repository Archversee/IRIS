const MODES = [
  { id: "visitor", label: "Visitor", desc: "Just here to see what it's like — relaxed, no pressure." },
  { id: "trainee", label: "Trainee", desc: "Learning the pattern — more guidance, more repeats." },
  { id: "pilot", label: "Pilot", desc: "You know the basics — direct coaching, fewer prompts." },
];

export default function ModeCards({ onChoose, busy }) {
  return (
    <div className="row">
      {MODES.map((m) => (
        <div className="col card" key={m.id}>
          <h3>{m.label}</h3>
          <p className="muted">{m.desc}</p>
          <button type="button" disabled={busy} onClick={() => onChoose(m.id)}>
            {busy ? "Starting…" : `Fly as ${m.label}`}
          </button>
        </div>
      ))}
    </div>
  );
}
