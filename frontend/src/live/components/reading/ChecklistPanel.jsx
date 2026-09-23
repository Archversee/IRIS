export default function ChecklistPanel({ checkpoints, selectedId, playingId, onSelect, onPlay }) {
  return (
    <div className="mavis-pf-list">
      {(checkpoints || []).map((cp, i) => (
        <div
          key={cp.id}
          className={"mavis-pf-item" + (cp.id === selectedId ? " is-selected" : "")}
          onClick={() => onSelect(cp.id)}
        >
          <div className="mavis-pf-item__head">
            <span className="mavis-pf-item__num">{i + 1}</span>
            <h4>{cp.title}</h4>
            <button
              type="button"
              className="secondary"
              onClick={(e) => { e.stopPropagation(); onPlay(cp.id); }}
            >
              {playingId === cp.id ? "🔊 Playing…" : "🔊 Play"}
            </button>
          </div>
          <p className="muted">{cp.say}</p>
          {cp.details?.length > 0 && (
            <ul>
              {cp.details.map((d, j) => <li key={j}>{d}</li>)}
            </ul>
          )}
        </div>
      ))}
    </div>
  );
}
