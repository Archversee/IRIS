function fmt(n, digits = 0) {
  return typeof n === "number" ? n.toFixed(digits) : "—";
}

// Minimal live telemetry + coaching readout -- deliberately not a port of
// the original app's full gauge/map HUD (that's a later slice if wanted),
// just enough to see the flight and coaching are actually live.
export default function InFlightPanel({ state }) {
  const tel = state?.tel || {};
  const transcript = state?.transcript || [];

  return (
    <div>
      <div className="row" style={{ alignItems: "center", marginBottom: 16 }}>
        <h1 style={{ margin: 0 }}>{state?.active_lesson?.title || "In flight"}</h1>
        <span className="pill">{state?.phase || "—"}</span>
        <span className="muted">Circuit {state?.circuit ?? "—"} / {state?.total_circuits ?? "—"}</span>
      </div>

      <div className="readout">
        <div className="metric"><div className="k">Altitude (ft)</div><div className="v">{fmt(tel.alt_msl)}</div></div>
        <div className="metric"><div className="k">Airspeed (kt)</div><div className="v">{fmt(tel.ias)}</div></div>
        <div className="metric"><div className="k">Heading</div><div className="v">{fmt(tel.hdg)}°</div></div>
        <div className="metric"><div className="k">Vertical speed</div><div className="v">{fmt(tel.vs)}</div></div>
        <div className="metric"><div className="k">Bank</div><div className="v">{fmt(tel.bank)}°</div></div>
        <div className="metric"><div className="k">Pitch</div><div className="v">{fmt(tel.pitch)}°</div></div>
      </div>

      <h3 style={{ marginTop: 24 }}>Coaching</h3>
      <div className="card" style={{ maxHeight: 260, overflowY: "auto" }}>
        {transcript.length === 0 && <p className="muted">No coaching messages yet.</p>}
        {transcript.map((t, i) => (
          <p key={i} style={{ margin: "6px 0" }}>
            <strong>{t.who === "coach" ? "MAVIS" : "You"}:</strong> {t.text}
          </p>
        ))}
      </div>

      {state?.scorecard && (
        <>
          <h3 style={{ marginTop: 24 }}>Scorecard</h3>
          <div className="readout">
            {Object.entries(state.scorecard.scores || {}).map(([k, v]) => (
              <div className="metric" key={k}>
                <div className="k">{k}</div>
                <div className="v">{v ?? "—"}</div>
              </div>
            ))}
            <div className="metric"><div className="k">Total</div><div className="v">{state.scorecard.total ?? "—"}</div></div>
            <div className="metric"><div className="k">Grade</div><div className="v">{state.scorecard.grade ?? "—"}</div></div>
          </div>
        </>
      )}
    </div>
  );
}
