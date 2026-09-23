function VBar({ label, pct }) {
  return (
    <div className="mavis-vbar">
      <div className="mavis-vbar__track">
        <div className="mavis-vbar__fill" style={{ height: `${Math.max(0, Math.min(1, pct)) * 100}%` }} />
      </div>
      <div className="mavis-vbar__label">{label}</div>
    </div>
  );
}

export default function ControlBars({ tel }) {
  return (
    <div className="mavis-controls">
      <VBar label="Throttle" pct={tel?.throttle ?? 0} />
      <VBar label="Rudder" pct={((tel?.rudder ?? 0) + 1) / 2} />
      <VBar label="Flaps" pct={Math.min(1, (tel?.flaps_deg ?? 0) / 40)} />
    </div>
  );
}
