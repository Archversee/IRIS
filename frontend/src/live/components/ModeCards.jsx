import { mediaUrl } from "../api/mavisApi.js";

const MODES = [
  { id: "visitor", label: "Visitor", desc: "Just here to see what it's like — relaxed, no pressure.", mediaKey: "mode-visitor" },
  { id: "trainee", label: "Trainee", desc: "Learning the pattern — more guidance, more repeats.", mediaKey: "mode-trainee" },
  { id: "pilot", label: "Pilot", desc: "You know the basics — direct coaching, fewer prompts.", mediaKey: "mode-pilot" },
];

export default function ModeCards({ onChoose, busy, media }) {
  return (
    <div className="mavis-modes">
      {MODES.map((m) => {
        const bg = media?.[m.mediaKey] ? mediaUrl(media[m.mediaKey]) : null;
        return (
          <button
            type="button"
            key={m.id}
            className="mavis-mode-card"
            disabled={busy}
            onClick={() => onChoose(m.id)}
            style={bg ? { backgroundImage: `linear-gradient(180deg, rgba(19,25,34,0.55), rgba(19,25,34,0.92)), url(${bg})` } : undefined}
          >
            <div className="mavis-mode-card__label">{m.label}</div>
            <div className="mavis-mode-card__desc">{m.desc}</div>
          </button>
        );
      })}
    </div>
  );
}
