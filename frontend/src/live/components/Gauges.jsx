function clamp01(v) {
  return Math.max(0, Math.min(1, v));
}

function Tape({ label, value, min, max, unit }) {
  const pct = clamp01(((value ?? min) - min) / (max - min)) * 100;
  return (
    <div className="mavis-tape">
      <div className="mavis-tape__bar">
        <div className="mavis-tape__ptr" style={{ bottom: `${pct}%` }} />
      </div>
      <div className="mavis-tape__val">{value != null ? Math.round(value) : "--"}{unit}</div>
      <div className="mavis-tape__label">{label}</div>
    </div>
  );
}

// Simplified vertical-tape gauges -- same three readouts as the original
// (speed/altitude/vertical-speed), without the target-band overlay.
export default function Gauges({ tel, scales }) {
  const speedMax = scales?.speed_max ?? 160;
  const altMax = scales?.alt_max ?? 3000;
  return (
    <div className="mavis-gauges">
      <Tape label="Speed" value={tel?.ias} min={0} max={speedMax} unit="kt" />
      <Tape label="Altitude" value={tel?.alt_msl} min={0} max={altMax} unit="ft" />
      <Tape label="V/S" value={tel?.vs} min={-1500} max={1500} unit="fpm" />
    </div>
  );
}
