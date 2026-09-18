// `progress`: 0..1 for a determinate bar, null/undefined for an
// indeterminate sweep (size unknown, e.g. no Content-Length header).
export default function LoadingBar({ label, progress }) {
  const known = typeof progress === "number";
  return (
    <div className="loading-box">
      <div>{label}</div>
      <div className={"loading-bar" + (known ? "" : " indeterminate")}>
        <div className="loading-bar-fill" style={known ? { width: `${Math.round(progress * 100)}%` } : undefined} />
      </div>
      {known && <div className="loading-pct">{Math.round(progress * 100)}%</div>}
    </div>
  );
}
