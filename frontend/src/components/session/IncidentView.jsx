import { useEffect, useMemo, useState } from "react";
import {
  ComposedChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine, ReferenceArea, CartesianGrid,
} from "recharts";
import { api } from "../../api/client.js";
import { useAuth } from "../../auth/AuthContext.jsx";

// The 30 s before and after one broken rule (backend app/incidents.py):
// the value against its limits, where the pilot was looking, what they did
// with the controls, and what it adds up to. t = 0 is the moment the limit
// was crossed. Opened from a broken-rule moment in Review's SOP drawer.

const ROLE_COLOR = {
  outside: "#14b8a6", instruments: "#38bdf8", airspeed: "#4ade80", altimeter: "#f5a623",
  attitude: "#a78bfa", vsi: "#60a5fa", heading: "#fb7185", turn: "#f472b6", engine: "#facc15",
};
const OTHER = ["#94a3b8", "#cbd5e1", "#64748b", "#a3a3a3"];
const KIND = {
  perception: { label: "Seeing it", cls: "bad" }, action: { label: "Acting on it", cls: "bad" },
  handling: { label: "Handling", cls: "bad" }, workload: { label: "Workload", cls: "warn" },
  context: { label: "Context", cls: "info" }, positive: { label: "Good", cls: "good" }, data: { label: "Data", cls: "muted" },
};
const CONTROLS = [
  { key: "throttle", label: "Throttle", color: "#facc15", scale: 0.01 },
  { key: "elevator", label: "Stick fore/aft", color: "#38bdf8", scale: 1 },
  { key: "aileron", label: "Stick left/right", color: "#a78bfa", scale: 1 },
  { key: "rudder", label: "Rudder", color: "#fb7185", scale: 1 },
];
const s1 = (v) => (v == null ? "–" : `${v > 0 ? "+" : ""}${v.toFixed(1)} s`);
// relative to the moment the limit was crossed, in words
const rel = (v) => (v == null ? "–" : Math.abs(v) < 0.05 ? "as the limit was crossed"
  : `${Math.abs(v).toFixed(1)} s ${v < 0 ? "before" : "after"} the limit`);

export default function IncidentView({ resultId, index, onClose, onWatch }) {
  const [a, setA] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    setA(null); setErr("");
    api.incident(resultId, index).then(setA).catch((e) => setErr(e.detail || e.message));
  }, [resultId, index]);
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const colorOf = useMemo(() => {
    const m = new Map();
    let i = 0;
    return (seg) => {
      if (seg.role && ROLE_COLOR[seg.role]) return ROLE_COLOR[seg.role];
      if (!m.has(seg.name)) m.set(seg.name, OTHER[i++ % OTHER.length]);
      return m.get(seg.name);
    };
  }, [a]);

  const series = useMemo(() => (a?.series || []).map((p) => ({
    t: p.t, v: p.v,
    ...Object.fromEntries(CONTROLS.map((c) => [c.key, p[c.key] == null ? null : p[c.key] * c.scale])),
  })), [a]);

  return (
    <div className="inc-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="inc" role="dialog" aria-modal="true" aria-label="What happened around this broken rule">
        <header className="inc-head">
          <div>
            <div className="inc-crumb">30 s before and after</div>
            <h2>{a?.rule?.title || "Loading…"}</h2>
            {a && (
              <div className="inc-sub">
                {a.rule.severity && <span className={"rc-sev " + a.rule.severity}>{a.rule.severity}</span>}{" "}
                Limit {a.bounds.lo != null ? a.bounds.lo.toFixed(0) : "–"} – {a.bounds.hi != null ? a.bounds.hi.toFixed(0) : "–"} {a.bounds.unit}
                {" · "}outside for {a.breach.dur_s.toFixed(1)} s{a.breach.peak_text ? ` · worst ${a.breach.peak_text}` : ""}
                {a.breach.phase ? ` · ${a.breach.phase.toLowerCase().replace(/_/g, " ")}` : ""}
              </div>
            )}
          </div>
          <span className="spacer" />
          {a && onWatch && (
            <div className="inc-watch">
              <button className="rc-btn" onClick={() => onWatch(a.breach.t0 + a.onset.t)}>▶ Watch from the build-up</button>
              <button className="rc-btn" onClick={() => onWatch(a.breach.t0)}>▶ Watch the breach</button>
            </div>
          )}
          <button className="rc-x" onClick={onClose} aria-label="Close">×</button>
        </header>

        {err && <div className="rc-err">{err}</div>}
        {!a && !err && <div className="inc-loading">Analysing…</div>}

        {a && (
          <div className="inc-body">
            <Explanation a={a} onChange={(explanation) => setA((x) => ({ ...x, explanation }))} />

            <section className="inc-findings">
              {a.findings.length === 0 && <div className="rc-muted">Nothing stood out.</div>}
              {a.findings.map((f) => (
                <div key={f.code} className={"inc-finding " + (KIND[f.kind]?.cls || "info")}>
                  <span className="inc-kind">{KIND[f.kind]?.label || f.kind}</span>
                  <span>{f.text}</span>
                </div>
              ))}
            </section>

            <section className="inc-chart">
              <h3>{a.rule.metric ? labelOf(a.rule.metric) : "Value"} <span className="rc-muted">· allowed range shaded</span></h3>
              <ResponsiveContainer width="100%" height={190}>
                <ComposedChart data={series} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
                  <CartesianGrid stroke="var(--rev-line)" strokeDasharray="3 3" />
                  <XAxis dataKey="t" type="number" domain={[a.window.from_s, a.window.to_s]} unit="s"
                    stroke="var(--rev-muted)" tick={{ fontSize: 11 }} />
                  <YAxis stroke="var(--rev-muted)" tick={{ fontSize: 11 }} width={44} domain={["auto", "auto"]} />
                  <Tooltip content={<ValueTip unit={a.bounds.unit} />} />
                  {(a.bounds.lo != null || a.bounds.hi != null) && (
                    <ReferenceArea y1={a.bounds.lo ?? undefined} y2={a.bounds.hi ?? undefined} ifOverflow="extendDomain"
                      fill="#4ade80" fillOpacity={0.08} stroke="#4ade80" strokeOpacity={0.35} strokeDasharray="4 3" />
                  )}
                  <ReferenceArea x1={0} x2={a.breach.dur_s} fill="#ff5c5c" fillOpacity={0.1} />
                  <ReferenceLine x={a.onset.t} stroke="#f5a623" strokeDasharray="5 4" />
                  <ReferenceLine x={0} stroke="#ff5c5c" />
                  <ReferenceLine x={a.breach.dur_s} stroke="#4ade80" strokeDasharray="5 4" />
                  {a.response.first_input && <ReferenceLine x={a.response.first_input.t} stroke="#facc15" />}
                  {a.context.callouts.filter((c) => c.who !== "trainee").map((c, i) => (
                    <ReferenceLine key={i} x={c.t} stroke="#a78bfa" strokeDasharray="2 3" />
                  ))}
                  <Line dataKey="v" stroke="#e8eef8" dot={false} strokeWidth={1.8} isAnimationActive={false} connectNulls />
                </ComposedChart>
              </ResponsiveContainer>
              {/* line key under the chart: labels on the chart collide when moments are close */}
              <div className="inc-legend inc-legend--marks">
                <span><i className="dash" style={{ borderColor: "#f5a623" }} />build-up starts ({s1(a.onset.t)})</span>
                <span><i style={{ background: "#ff5c5c" }} />limit crossed (0 s)</span>
                {a.response.first_input && <span><i style={{ background: "#facc15" }} />first input ({s1(a.response.first_input.t)})</span>}
                <span><i className="dash" style={{ borderColor: "#4ade80" }} />back within limits ({s1(a.breach.dur_s)})</span>
                {a.context.callouts.some((c) => c.who !== "trainee") && <span><i className="dash" style={{ borderColor: "#a78bfa" }} />MAVIS callout</span>}
              </div>
            </section>

            <section className="inc-chart">
              <h3>Where they were looking</h3>
              <GazeStrip a={a} colorOf={colorOf} />
            </section>

            <section className="inc-chart">
              <h3>Controls <span className="rc-muted">· throttle 0–100 %, stick and rudder −1 … +1</span></h3>
              <ResponsiveContainer width="100%" height={110}>
                <ComposedChart data={series} margin={{ top: 4, right: 16, bottom: 0, left: 0 }}>
                  <CartesianGrid stroke="var(--rev-line)" strokeDasharray="3 3" />
                  <XAxis dataKey="t" type="number" domain={[a.window.from_s, a.window.to_s]} unit="s"
                    stroke="var(--rev-muted)" tick={{ fontSize: 11 }} />
                  <YAxis stroke="var(--rev-muted)" tick={{ fontSize: 11 }} width={44} domain={[-1, 1]} />
                  <ReferenceLine x={0} stroke="#ff5c5c" />
                  {CONTROLS.map((c) => (
                    <Line key={c.key} dataKey={c.key} name={c.label} stroke={c.color} dot={false} strokeWidth={1.4}
                      isAnimationActive={false} connectNulls />
                  ))}
                </ComposedChart>
              </ResponsiveContainer>
              <div className="inc-legend">
                {CONTROLS.map((c) => <span key={c.key}><i style={{ background: c.color }} />{c.label}</span>)}
              </div>
            </section>

            <section className="inc-numbers">
              <Num label="Build-up before the limit" value={`${a.onset.lead_s.toFixed(1)} s`} />
              <Num label={`Last look at ${(a.gaze.primary_label || "the instrument").toLowerCase()}`}
                value={a.gaze.last_look_before_s == null ? "not in the 30 s" : `${a.gaze.last_look_before_s.toFixed(1)} s before`} />
              <Num label="First look once it started" value={rel(a.gaze.first_look_after_onset_s)} />
              <Num label="First control input" value={a.response.first_input ? `${rel(a.response.first_input.t)} · ${a.response.first_input.label}` : "none found"} />
              <Num label="Back within limits" value={`${a.response.recovered_s.toFixed(1)} s`} />
              <Num label="Pupil vs usual" value={a.workload.vs_baseline == null ? "–" : `${(a.workload.vs_baseline * 100).toFixed(0)} %`} />
              <Num label="Eye ↔ flight clock" value={a.clock?.checked_at
                ? `${a.clock.corrected ? "corrected" : "off by"} ${Math.abs(a.clock.offset_s || 0).toFixed(2)} s` : "not measured"} />
            </section>

            {a.context.callouts.length > 0 && (
              <section className="inc-calls">
                <h3>MAVIS in this window</h3>
                {a.context.callouts.map((c, i) => (
                  <div key={i}><span className="rc-muted">{s1(c.t)}</span> {c.who === "trainee" ? "Trainee: " : ""}{c.message}</div>
                ))}
              </section>
            )}
            {a.rule.source_quote && (
              <blockquote className="inc-quote">“{a.rule.source_quote}”{a.rule.source_page ? <span className="rc-muted"> p. {a.rule.source_page}</span> : null}</blockquote>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// Plain-words debrief from the local reasoning model (backend app/incident_explain.py),
// built only from the measured facts above; numbers it couldn't back up are flagged.
function Explanation({ a, onChange }) {
  const { user } = useAuth() || {};
  const isInstructor = user && (user.role === "instructor" || user.role === "admin");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const e = a.explanation;

  async function generate() {
    setBusy(true); setErr("");
    try { onChange(await api.explainIncident(a.result_id, a.index)); }
    catch (x) { setErr(x.detail || x.message); }
    finally { setBusy(false); }
  }
  async function rate(rating) {
    try { onChange(await api.rateExplanation(e.id, { rating: e.rating === rating ? null : rating })); }
    catch (x) { setErr(x.detail || x.message); }
  }
  if (!e && !isInstructor) return null;
  return (
    <section className="inc-explain">
      <div className="inc-explain__head">
        <h3>Debrief note</h3>
        {e && <span className="rc-muted">{e.model}{e.reasoned ? " · reasoned" : ""} · {new Date(e.created_at).toLocaleString()}</span>}
        <span className="spacer" />
        {isInstructor && (
          <button className="rc-btn" disabled={busy} onClick={generate}>
            {busy ? "Thinking… (can take a minute)" : e ? "Explain again" : "Explain with AI"}
          </button>
        )}
      </div>
      {err && <div className="rc-err">{err}</div>}
      {e && <p className="inc-explain__text">{e.text}</p>}
      {e?.unsupported_numbers?.length > 0 && (
        <div className="inc-explain__warn">
          Check these numbers, they aren't in the measured facts: {e.unsupported_numbers.join(", ")}
        </div>
      )}
      {e && isInstructor && (
        <div className="inc-explain__rate">
          <span className="rc-muted">Was this right?</span>
          <button className={"rc-btn" + (e.rating === "helpful" ? " is-on" : "")} onClick={() => rate("helpful")}>👍 Helpful</button>
          <button className={"rc-btn" + (e.rating === "not_helpful" ? " is-on" : "")} onClick={() => rate("not_helpful")}>👎 Not helpful</button>
          {e.rated_by && <span className="rc-muted">rated by {e.rated_by}</span>}
        </div>
      )}
      {!e && isInstructor && !busy && (
        <div className="rc-muted">Writes a short note for the student from the facts below, with a local reasoning model.</div>
      )}
    </section>
  );
}

function labelOf(metric) {
  return { ias: "Airspeed", alt_msl: "Altitude", alt_agl: "Height above ground", vs: "Vertical speed", bank: "Bank",
    pitch: "Pitch", hdg: "Heading", flaps_deg: "Flaps", rpm: "Engine RPM", throttle: "Throttle" }[metric] || metric;
}

function ValueTip({ active, payload, unit }) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div className="inc-tip">
      <div>{s1(d.t)}</div>
      <b>{d.v == null ? "–" : `${d.v.toFixed(1)} ${unit}`}</b>
    </div>
  );
}

function Num({ label, value }) {
  return <div className="inc-num"><span>{label}</span><b>{value}</b></div>;
}

// One bar per look, coloured by instrument; red line at the breach.
function GazeStrip({ a, colorOf }) {
  const from = a.window.from_s, to = a.window.to_s, span = to - from || 1;
  const segs = a.gaze_segments.filter((s) => s.t1 >= from && s.t0 <= to);
  const names = [...new Map(segs.map((s) => [s.name, s])).values()];
  if (!segs.length) return <div className="rc-muted">No eye data in this window.</div>;
  return (
    <>
      <div className="inc-strip">
        {segs.map((s, i) => (
          <span key={i} title={`${s.name} · ${s1(s.t0)} → ${s1(s.t1)}`}
            style={{ left: `${((Math.max(s.t0, from) - from) / span) * 100}%`,
              width: `${Math.max(0.3, ((Math.min(s.t1, to) - Math.max(s.t0, from)) / span) * 100)}%`,
              background: colorOf(s) }} />
        ))}
        <i className="inc-strip__breach" style={{ left: `${((0 - from) / span) * 100}%` }} />
        <i className="inc-strip__onset" style={{ left: `${((a.onset.t - from) / span) * 100}%` }} />
      </div>
      <div className="inc-legend">
        {names.map((s) => <span key={s.name}><i style={{ background: colorOf(s) }} />{s.name}</span>)}
      </div>
    </>
  );
}
