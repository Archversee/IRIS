import { useEffect, useMemo, useState } from "react";
import {
  ComposedChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine, ReferenceArea, CartesianGrid,
} from "recharts";
import { api } from "../../api/client.js";
import { useAuth } from "../../auth/AuthContext.jsx";

// The 30 s before and after one broken rule (backend app/incidents.py):
// the value against its limits, and what it adds up to. t = 0 is the moment
// the limit was crossed. Opened from a broken-rule moment in Review's SOP
// panel. One chart: hovering it shows, for that moment, where the pilot was
// looking (a pie of the last few seconds) and what the controls were doing.

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
const GAZE_LOOKBACK_S = 5;    // the hover pie: where the eyes went in the seconds up to that moment
const s1 = (v) => (v == null ? "–" : `${v > 0 ? "+" : ""}${v.toFixed(1)} s`);
// relative to the moment the limit was crossed, in words
const rel = (v) => (v == null ? "–" : Math.abs(v) < 0.05 ? "as the limit was crossed"
  : `${Math.abs(v).toFixed(1)} s ${v < 0 ? "before" : "after"} the limit`);

// The window the viewer last chose (seconds before / after), kept for the next incident.
const WIN_KEY = "incidentWindow";
function loadWin() {
  try { return JSON.parse(localStorage.getItem(WIN_KEY)) || null; } catch { return null; }
}
function saveWin(w) {
  try { w ? localStorage.setItem(WIN_KEY, JSON.stringify(w)) : localStorage.removeItem(WIN_KEY); } catch { /* private mode */ }
}

export default function IncidentView({ resultId, index, onClose, onWatch, debriefJob }) {
  const [a, setA] = useState(null);
  const [err, setErr] = useState("");
  const [win, setWin] = useState(loadWin);           // null = the backend's default

  useEffect(() => {
    setA(null); setErr("");
    api.incident(resultId, index, win).then(setA).catch((e) => setErr(e.detail || e.message));
  }, [resultId, index, win]);
  // the background job may write this moment's note while it's open: pick it up
  const hasNote = !!a?.explanation;
  useEffect(() => {
    if (!debriefJob?.done || hasNote) return;
    api.incident(resultId, index, win).then((x) => x.explanation && setA((p) => p && ({ ...p, explanation: x.explanation })))
      .catch(() => {});
  }, [debriefJob?.done, debriefJob?.state]); // eslint-disable-line react-hooks/exhaustive-deps
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
            <WindowPicker win={a?.window_s} onApply={(w) => { saveWin(w); setWin(w); }} />
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
            <Explanation a={a} win={win} job={debriefJob} onChange={(explanation) => setA((x) => ({ ...x, explanation }))} />

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
              <h3>
                {a.rule.metric ? labelOf(a.rule.metric) : "Value"} <span className="rc-muted">· allowed range shaded</span>
                <span className="inc-hint">Hover the graph: where they were looking and the controls at that moment</span>
              </h3>
              <ResponsiveContainer width="100%" height={300}>
                <ComposedChart data={series} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
                  <CartesianGrid stroke="var(--rev-line)" strokeDasharray="3 3" />
                  <XAxis dataKey="t" type="number" domain={[a.window.from_s, a.window.to_s]} unit="s"
                    stroke="var(--rev-muted)" tick={{ fontSize: 11 }} />
                  <YAxis stroke="var(--rev-muted)" tick={{ fontSize: 11 }} width={44} domain={["auto", "auto"]} />
                  <Tooltip content={<MomentTip a={a} colorOf={colorOf} />} isAnimationActive={false}
                    cursor={{ stroke: "var(--rev-cyan)", strokeWidth: 1 }} wrapperStyle={{ zIndex: 5, pointerEvents: "none" }}
                    allowEscapeViewBox={{ x: false, y: true }} />
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
                  <Line dataKey="v" stroke="#e8eef8" dot={false} strokeWidth={1.8} isAnimationActive={false} connectNulls
                    activeDot={{ r: 4, fill: "var(--rev-cyan)", stroke: "#fff", strokeWidth: 1 }} />
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

// How far before the breach and after the recovery to look. Typing -15 / +20 works too.
function WindowPicker({ win, onApply }) {
  const [before, setBefore] = useState("");
  const [after, setAfter] = useState("");
  useEffect(() => {
    if (win) { setBefore(String(win.before)); setAfter(String(win.after)); }
  }, [win?.before, win?.after]); // eslint-disable-line react-hooks/exhaustive-deps
  const parse = (v) => {
    const n = Math.abs(parseFloat(String(v).replace(/[^\d.\-+]/g, "")));
    return isFinite(n) ? Math.min(120, Math.max(10, n)) : null;
  };
  const apply = () => {
    const b = parse(before), f = parse(after);
    if (b != null && f != null) onApply({ before: b, after: f });
  };
  return (
    <div className="inc-crumb inc-win">
      <input aria-label="Seconds before" value={before} onChange={(e) => setBefore(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && apply()} />
      <span>s before ·</span>
      <input aria-label="Seconds after" value={after} onChange={(e) => setAfter(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && apply()} />
      <span>s after</span>
      <button className="rc-btn inc-win__go" onClick={apply} title="Look at this window (10-120 s each side)">Apply</button>
      <button className="rc-btn inc-win__go" onClick={() => onApply(null)} title="Back to the default window">Default</button>
    </div>
  );
}

// Plain-words debrief from the local reasoning model (backend app/incident_explain.py),
// built only from the measured facts above; numbers it couldn't back up are flagged.
function Explanation({ a, win, job, onChange }) {
  const { user } = useAuth() || {};
  const isInstructor = user && (user.role === "instructor" || user.role === "admin");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const e = a.explanation;

  async function generate() {
    setBusy(true); setErr("");
    try { onChange(await api.explainIncident(a.result_id, a.index, win)); }
    catch (x) { setErr(x.detail || x.message); }
    finally { setBusy(false); }
  }
  const writing = !e && job && job.state !== "done" && job.state !== "error";
  if (!e && !isInstructor && !writing) return null;
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
      {e && e.pre_s != null && (Math.abs(e.pre_s - a.window_s.before) > 0.5 || Math.abs(e.post_s - a.window_s.after) > 0.5) && (
        <div className="rc-muted">Written for {e.pre_s} s before / {e.post_s} s after (you're looking at {a.window_s.before} / {a.window_s.after}).</div>
      )}
      {writing && !busy && <div className="rc-muted">Being written in the background ({job.state})…</div>}
      {e?.requested_by === "auto" && <div className="rc-muted">Written automatically after the flight.</div>}
      {e?.unsupported_numbers?.length > 0 && (
        <div className="inc-explain__warn">
          Check these numbers, they aren't in the measured facts: {e.unsupported_numbers.join(", ")}
        </div>
      )}
      {!e && isInstructor && !busy && !writing && (
        <div className="rc-muted">Writes a short note for the student from the facts below, with a local reasoning model.</div>
      )}
    </section>
  );
}

function labelOf(metric) {
  return { ias: "Airspeed", alt_msl: "Altitude", alt_agl: "Height above ground", vs: "Vertical speed", bank: "Bank",
    pitch: "Pitch", hdg: "Heading", flaps_deg: "Flaps", rpm: "Engine RPM", throttle: "Throttle" }[metric] || metric;
}

function Num({ label, value }) {
  return <div className="inc-num"><span>{label}</span><b>{value}</b></div>;
}

// Hovering the chart: the value at that moment, where the pilot was looking
// (now, and a pie of the last GAZE_LOOKBACK_S seconds), and the controls.
function MomentTip({ active, payload, a, colorOf }) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  const unit = a.bounds.unit || "";
  const out = d.v != null && ((a.bounds.lo != null && d.v < a.bounds.lo) || (a.bounds.hi != null && d.v > a.bounds.hi));
  return (
    <div className="inc-moment">
      <div className="inc-moment__head">
        <span className="rc-muted">{rel(d.t)}</span>
        <b className={out ? "is-out" : "is-in"}>{d.v == null ? "–" : `${d.v.toFixed(1)} ${unit}`}</b>
        <span className={"inc-moment__flag " + (out ? "is-out" : "is-in")}>{out ? "outside the limit" : "within limits"}</span>
      </div>
      <div className="inc-moment__body">
        <GazePie a={a} t={d.t} colorOf={colorOf} />
        <Controls d={d} />
      </div>
    </div>
  );
}

function GazePie({ a, t, colorOf }) {
  const from = t - GAZE_LOOKBACK_S;
  const segs = (a.gaze_segments || []).filter((s) => s.t1 > from && s.t0 <= t);
  const share = new Map();
  for (const s of segs) {
    const dur = Math.min(s.t1, t) - Math.max(s.t0, from);
    if (dur <= 0) continue;
    const cur = share.get(s.name) || { seg: s, dur: 0 };
    cur.dur += dur;
    share.set(s.name, cur);
  }
  const parts = [...share.values()].sort((x, y) => y.dur - x.dur);
  const total = parts.reduce((n, p) => n + p.dur, 0);
  const now = (a.gaze_segments || []).find((s) => s.t0 <= t && s.t1 >= t);
  return (
    <div className="inc-moment__gaze">
      <h4>Looking at</h4>
      <div className="inc-moment__now">
        {now ? <><i style={{ background: colorOf(now) }} />{now.name}</> : <span className="rc-muted">no eye data</span>}
      </div>
      {total > 0 ? (
        <div className="inc-pie">
          <Pie parts={parts.map((p) => ({ value: p.dur, color: colorOf(p.seg) }))} />
          <ul>
            {parts.slice(0, 5).map((p) => (
              <li key={p.seg.name}><i style={{ background: colorOf(p.seg) }} />{p.seg.name}<b>{Math.round((p.dur / total) * 100)}%</b></li>
            ))}
          </ul>
        </div>
      ) : <div className="rc-muted inc-pie__none">No eye data in the last {GAZE_LOOKBACK_S} s.</div>}
      {total > 0 && <div className="rc-muted inc-pie__cap">last {GAZE_LOOKBACK_S} s</div>}
    </div>
  );
}

// A small donut from {value, color} parts.
function Pie({ parts }) {
  const total = parts.reduce((n, p) => n + p.value, 0);
  const R = 30, r = 17;
  if (parts.length === 1) {
    return (
      <svg viewBox="-32 -32 64 64" className="inc-pie__svg">
        <circle r={(R + r) / 2} fill="none" stroke={parts[0].color} strokeWidth={R - r} />
      </svg>
    );
  }
  let ang = -Math.PI / 2;
  const pt = (rad, a) => `${(rad * Math.cos(a)).toFixed(2)} ${(rad * Math.sin(a)).toFixed(2)}`;
  return (
    <svg viewBox="-32 -32 64 64" className="inc-pie__svg">
      {parts.map((p, i) => {
        const a0 = ang, a1 = ang + (p.value / total) * Math.PI * 2;
        ang = a1;
        const big = a1 - a0 > Math.PI ? 1 : 0;
        return <path key={i} fill={p.color} stroke="var(--rev-panel)" strokeWidth="0.8"
          d={`M ${pt(R, a0)} A ${R} ${R} 0 ${big} 1 ${pt(R, a1)} L ${pt(r, a1)} A ${r} ${r} 0 ${big} 0 ${pt(r, a0)} Z`} />;
      })}
    </svg>
  );
}

// The controls at that moment: throttle fills from the left (0-100 %), the
// stick and rudder from the middle (-1 ... +1), each with it in words.
function Controls({ d }) {
  const words = {
    elevator: (x) => (Math.abs(x) < 0.05 ? "centred" : x > 0 ? `back ${pct(x)}` : `forward ${pct(x)}`),
    aileron: (x) => (Math.abs(x) < 0.05 ? "centred" : x < 0 ? `left ${pct(x)}` : `right ${pct(x)}`),
    rudder: (x) => (Math.abs(x) < 0.05 ? "centred" : x < 0 ? `left ${pct(x)}` : `right ${pct(x)}`),
  };
  return (
    <div className="inc-moment__ctl">
      <h4>Controls</h4>
      {CONTROLS.map((c) => {
        const x = d[c.key];
        const isThr = c.key === "throttle";
        return (
          <div key={c.key} className="inc-ctl">
            <span className="inc-ctl__label">{c.label}</span>
            <span className={"inc-ctl__bar" + (isThr ? "" : " is-centred")}>
              {x != null && (isThr
                ? <i style={{ left: 0, width: `${Math.max(0, Math.min(1, x)) * 100}%`, background: c.color }} />
                : <i style={{ left: `${50 + Math.min(0, Math.max(-1, x)) * 50}%`, width: `${Math.abs(Math.max(-1, Math.min(1, x))) * 50}%`, background: c.color }} />)}
            </span>
            <span className="inc-ctl__val">{x == null ? "–" : isThr ? `${Math.round(x * 100)}%` : words[c.key](x)}</span>
          </div>
        );
      })}
    </div>
  );
}
const pct = (x) => `${Math.round(Math.abs(x) * 100)}%`;
