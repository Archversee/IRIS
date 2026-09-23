const MODES = [
  { id: "visitor", label: "Visitor", desc: "Just here to see what it's like — relaxed, no pressure." },
  { id: "trainee", label: "Trainee", desc: "Learning the pattern — more guidance, more repeats." },
  { id: "pilot", label: "Pilot", desc: "You know the basics — direct coaching, fewer prompts." },
];

export default function ModeCards({ onChoose, busy }) {
  return (
    <div className="mavis-modes">
      {MODES.map((m) => (
        <button
          type="button"
          key={m.id}
          className="mavis-mode-card"
          disabled={busy}
          onClick={() => onChoose(m.id)}
        >
          <div className="mavis-mode-card__label">{m.label}</div>
          <div className="mavis-mode-card__desc">{m.desc}</div>
        </button>
      ))}
    </div>
  );
}
