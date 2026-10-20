// How trainees are getting on across flights (post-flight only): learning
// curves, scan pattern vs the expert flights, whether MAVIS's coaching works,
// handling quality, and (instructors) the whole group.
// Backend: backend/app/routers/progress.py, logic in backend/app/progress.py.
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Bar, CartesianGrid, ComposedChart, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import "./progress.css";

const INSIGHT_TITLE = {
  improving: "Improving", persistent: "Keeps happening", regression: "Slipped back", forgetting: "Between lessons",
  cause: "Why it happens", scan: "Scan", handling: "Handling", coaching: "Coaching", info: "",
};
const KIND = {
  perception: { label: "Seeing it (scan / attention)", color: "#60a5fa" },
  action: { label: "Acting on it", color: "#f5a623" },
  handling: { label: "Handling (over-correcting)", color: "#a78bfa" },
  workload: { label: "Workload", color: "#94a3b8" },
};
const PHASES = ["CLIMB_OUT", "CROSSWIND", "DOWNWIND", "BASE", "FINAL"];
const phaseName = (p) => p.replace("_", " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
const AXIS = { stroke: "var(--muted)", tick: { fontSize: 11 } };
const XAXIS = { ...AXIS, interval: 0, padding: { left: 24, right: 24 } };
const TIP = { contentStyle: { background: "var(--panel)", border: "1px solid var(--border)" } };
const pct = (v) => (v == null ? "–" : `${Math.round(v * 100)}%`);
const num = (v, d = 1, unit = "") => (v == null ? "–" : `${v.toFixed(d)}${unit}`);
const shortLabel = (f) => f.label.split(" · ")[0];

export default function Progress() {
  const { user } = useAuth();
  const isInstructor = user.role === "instructor" || user.role === "admin";
  const [tab, setTab] = useState("pilot");
  const [pilots, setPilots] = useState(null);
  const [name, setName] = useState("");
  const [err, setErr] = useState(null);

  useEffect(() => {
    api.progressPilots()
      .then((p) => {
        setPilots(p);
        if (p.length === 1) setName(p[0].pilot);
      })
      .catch((e) => setErr(e.message));
  }, []);

  return (
    <>
      <div className="card">
        <div className="pg-head">
          <h2>Progress</h2>
          {isInstructor && (
            <div className="pg-tabs">
              <button className={tab === "pilot" ? "" : "secondary"} onClick={() => setTab("pilot")}>Trainee</button>
              <button className={tab === "group" ? "" : "secondary"} onClick={() => setTab("group")}>Instructor dashboard</button>
            </div>
          )}
        </div>
        <p className="muted pg-sub">
          Across flights, after they're flown: SOP rule checks, the analysis of each broken rule, the eye tracker,
          MAVIS's callouts and the flight data.
        </p>
        {err && <p className="err">{err}</p>}
        {tab === "pilot" && pilots && (
          pilots.length === 0
            ? <p className="muted">No flights with a pilot name yet.</p>
            : pilots.length > 1 && (
              <label className="pg-pick">
                Trainee{" "}
                <select value={name} onChange={(e) => setName(e.target.value)}>
                  <option value="">Choose…</option>
                  {pilots.map((p) => (
                    <option key={p.pilot} value={p.pilot}>{p.pilot} ({p.flights} flight{p.flights === 1 ? "" : "s"})</option>
                  ))}
                </select>
              </label>
            )
        )}
      </div>
      {tab === "pilot" && name && <PilotView name={name} />}
      {tab === "group" && isInstructor && <GroupView />}
    </>
  );
}

function usePending(load, deps) {
  // Fetch; while the backend reports work still pending, fetch again shortly.
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true, timer;
    setErr(null);
    const go = () => load()
      .then((d) => {
        if (!alive) return;
        setData(d);
        if (d.pending > 0) timer = setTimeout(go, 1000);
      })
      .catch((e) => alive && setErr(e.message));
    go();
    return () => { alive = false; clearTimeout(timer); };
  }, [...deps, tick]); // eslint-disable-line react-hooks/exhaustive-deps
  return [data, err, () => setTick((t) => t + 1)];
}

function Pending({ n }) {
  return n > 0 ? <p className="pg-pending">Still working through {n} older item{n === 1 ? "" : "s"}…</p> : null;
}

function Section({ title, sub, children }) {
  return (
    <div className="card">
      <h3>{title}</h3>
      {sub && <p className="muted pg-note pg-section-sub">{sub}</p>}
      {children}
    </div>
  );
}

// ------------------------------------------------------------------ one trainee
function PilotView({ name }) {
  const [d, err] = usePending(() => api.progressPilot(name), [name]);
  if (err) return <div className="card"><p className="err">{err}</p></div>;
  if (!d || d.pilot.toLowerCase() !== name.toLowerCase()) return <div className="card"><p className="muted">Loading…</p></div>;
  const checked = d.flights.filter((f) => f.checked);
  return (
    <>
      <Section title={`${d.pilot}: what stands out`}>
        <Pending n={d.pending} />
        <ul className="pg-insights">
          {d.insights.map((i, k) => (
            <li key={k} className={`pg-ins pg-ins--${i.kind}`}>
              {INSIGHT_TITLE[i.kind] && <span className="pg-ins__kind">{INSIGHT_TITLE[i.kind]}</span>}
              {i.text}
            </li>
          ))}
        </ul>
        <p className="muted pg-note">
          {d.flights.length} flight{d.flights.length === 1 ? "" : "s"}
          {d.flights.length > checked.length && `, ${d.flights.length - checked.length} without SOP rule checks (left out of the rule trends)`}.
        </p>
      </Section>
      {checked.length > 0 && <LearningCurves flights={checked} curve={d.learning_curve} />}
      {checked.length > 0 && <CauseChart flights={checked} causes={d.causes} kinds={d.cause_kind} />}
      <ScanSection flights={d.flights} table={d.phase_table} />
      <CoachingSection flights={d.flights} coaching={d.coaching} />
      <HandlingSection flights={d.flights} table={d.phase_table} />
    </>
  );
}

function gapDays(a, b) {
  if (!a || !b) return null;
  return (new Date(b) - new Date(a)) / 86400000;
}

function LearningCurves({ flights, curve }) {
  const data = flights.map((f) => ({ name: shortLabel(f), breaches: f.breaches }));
  const top = curve.filter((r) => r.broken > 0).slice(0, 4);
  const colors = ["#f87171", "#f5a623", "#60a5fa", "#a78bfa"];
  top.forEach((r, k) => r.series.forEach((v, i) => { data[i][`r${k}`] = v; }));
  return (
    <Section title="Learning curves"
      sub="How often each rule was broken, flight by flight. A long run of ✗ = the mistake repeats; met then broken after a break = forgotten between lessons.">
      <ResponsiveContainer width="100%" height={210}>
        <ComposedChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
          <XAxis dataKey="name" {...XAXIS} />
          <YAxis allowDecimals={false} {...AXIS} width={30} />
          <Tooltip {...TIP} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Bar dataKey="breaches" name="All rules: times broken" fill="var(--border-strong)" />
          {top.map((r, k) => (
            <Line key={r.title} dataKey={`r${k}`} name={r.title} stroke={colors[k]} strokeWidth={2} connectNulls isAnimationActive={false} />
          ))}
        </ComposedChart>
      </ResponsiveContainer>
      <div className="pg-grid-wrap">
        <table className="pg-grid">
          <thead>
            <tr>
              <th className="pg-grid__rule">Rule</th>
              {flights.map((f, i) => {
                const gap = i ? gapDays(flights[i - 1].at, f.at) : null;
                return (
                  <th key={f.id} title={f.label}>
                    {gap != null && gap >= 7 && <span className="pg-gap" title={`${Math.round(gap)} days since the flight before`}>+{Math.round(gap)} d</span>}
                    <Link to={`/sessions/${f.id}/review`}>{shortLabel(f)}</Link>
                  </th>
                );
              })}
              <th>Broken</th><th title="Longest run of flights in a row it was broken">Repeats</th>
              <th title="Met, then broken again on the next flight after a break of 7+ days">Forgot</th>
            </tr>
          </thead>
          <tbody>
            {curve.map((r) => (
              <tr key={r.title}>
                <td className="pg-grid__rule">{r.title}</td>
                {r.series.map((v, i) => (
                  <td key={i} className={v == null ? "pg-v pg-v--na" : v > 0 ? "pg-v pg-v--fail" : "pg-v pg-v--pass"}
                    title={v == null ? "Didn't apply / not judged" : v > 0 ? `Broken ${v}×` : "Met"}>
                    {v == null ? "–" : v > 0 ? (v > 1 ? `✗${v}` : "✗") : "✓"}
                  </td>
                ))}
                <td>{r.broken} of {r.applied}</td>
                <td>{r.repeats > 1 ? `${r.repeats} in a row` : "–"}</td>
                <td>{r.relapses.after_break_of ? `${r.relapses.after_break} of ${r.relapses.after_break_of}` : "–"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Section>
  );
}

function CauseChart({ flights, causes, kinds }) {
  if (!flights.some((f) => f.breaches)) return null;
  const data = flights.map((f) => {
    const row = { name: shortLabel(f), breaches: f.breaches };
    for (const [code, n] of Object.entries(f.causes)) {
      const k = kinds[code];
      if (k) row[k] = (row[k] || 0) + n;
    }
    return row;
  });
  const used = Object.keys(KIND).filter((k) => data.some((r) => r[k]));
  const totals = {};
  for (const f of flights.slice(-5)) for (const [c, n] of Object.entries(f.causes)) if (causes[c]) totals[c] = (totals[c] || 0) + n;
  const top = Object.entries(totals).sort((a, b) => b[1] - a[1]);
  return (
    <Section title="Why rules were broken" sub="What the analysis of each broken rule found (one can have more than one cause).">
      <ResponsiveContainer width="100%" height={200}>
        <ComposedChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
          <XAxis dataKey="name" {...XAXIS} />
          <YAxis allowDecimals={false} {...AXIS} width={30} />
          <Tooltip {...TIP} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          {used.map((k) => <Bar key={k} dataKey={k} name={KIND[k].label} stackId="c" fill={KIND[k].color} />)}
          <Line dataKey="breaches" name="Times a rule was broken" stroke="var(--danger)" strokeWidth={2} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
      {top.length > 0 && (
        <ul className="pg-causes">
          {top.map(([c, n]) => (
            <li key={c}><span className="pg-dot" style={{ background: KIND[kinds[c]]?.color }} />{causes[c]} <b>{n}</b></li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function Compare({ mine, expert, fmt, worseIfHigher = true, tol = 0.25, flag = true }) {
  // "you" vs the expert flights, flagged when clearly worse
  const bad = flag && mine != null && expert != null && (worseIfHigher ? mine > expert * (1 + tol) + 1e-9 : mine < expert * (1 - tol) - 1e-9);
  return (
    <td className={bad ? "pg-cmp pg-cmp--bad" : "pg-cmp"}>
      {fmt(mine)}{expert != null && <span className="pg-cmp__ex"> / {fmt(expert)}</span>}
    </td>
  );
}

function phasesOf(table) {
  const all = new Set([...Object.keys(table.mine || {}), ...Object.keys(table.expert || {})]);
  return [...PHASES.filter((p) => all.has(p)), ...[...all].filter((p) => !PHASES.includes(p))];
}

function ScanSection({ flights, table }) {
  const phases = phasesOf(table);
  const [phase, setPhase] = useState("FINAL");
  const ph = phases.includes(phase) ? phase : phases[0];
  const series = flights.map((f) => {
    const s = f.stats?.phases?.[ph]?.scan;
    return { name: shortLabel(f), asi: s?.check_every_s?.airspeed ?? null, alt: s?.check_every_s?.altimeter ?? null,
      outside: s?.share?.outside != null ? Math.round(s.share.outside * 100) : null,
      spread: s?.spread != null ? Math.round(s.spread * 100) : null };
  });
  const ex = table.expert?.[ph];
  if (!flights.some((f) => f.stats && Object.values(f.stats.phases || {}).some((p) => p.scan))) {
    return <Section title="Scan pattern"><p className="muted">No eye-tracking data in these flights yet.</p></Section>;
  }
  return (
    <Section title="Scan pattern"
      sub={`Per phase, the last 3 flights against the expert flights (${table.expert_flights || "none marked yet"}). Red = clearly behind the experts.`}>
      <table className="pg-table">
        <thead>
          <tr><th>Phase</th><th>Looking outside</th><th>Airspeed checked every</th><th>Altimeter checked every</th>
            <th title="How spread out attention was: 0% all on one thing, 100% evenly over everything looked at">Attention spread</th></tr>
        </thead>
        <tbody>
          {phases.map((p) => {
            const m = table.mine?.[p] || {}, e = table.expert?.[p] || {};
            return (
              <tr key={p}>
                <td>{phaseName(p)}</td>
                <Compare mine={m.outside} expert={e.outside} fmt={pct} worseIfHigher={false} tol={0.15} />
                <Compare mine={m.airspeed_every_s} expert={e.airspeed_every_s} fmt={(v) => num(v, 1, " s")} />
                <Compare mine={m.altimeter_every_s} expert={e.altimeter_every_s} fmt={(v) => num(v, 1, " s")} />
                <Compare mine={m.spread} expert={e.spread} fmt={pct} flag={false} />
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="muted pg-note">You / <span className="pg-cmp__ex">expert</span>. Instruments are recognised from the AOI zone names.</p>
      <div className="pg-phase-pick">
        Flight by flight in{" "}
        <select value={ph} onChange={(e) => setPhase(e.target.value)}>
          {phases.map((p) => <option key={p} value={p}>{phaseName(p)}</option>)}
        </select>
      </div>
      <div className="pg-two">
        <div>
          <p className="pg-chart-title">Seconds between looks (lower = checked more often)</p>
          <ResponsiveContainer width="100%" height={190}>
            <LineChart data={series} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
              <XAxis dataKey="name" {...XAXIS} />
              <YAxis {...AXIS} width={30} />
              <Tooltip {...TIP} formatter={(v) => `${v} s`} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              {ex?.airspeed_every_s != null && <ReferenceLine y={ex.airspeed_every_s} stroke="#60a5fa" strokeDasharray="4 3" />}
              <Line dataKey="asi" name="Airspeed" stroke="#60a5fa" strokeWidth={2} connectNulls isAnimationActive={false} />
              <Line dataKey="alt" name="Altimeter" stroke="#4ade80" strokeWidth={2} connectNulls isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div>
          <p className="pg-chart-title">% of the time looking outside, and how spread out attention was</p>
          <ResponsiveContainer width="100%" height={190}>
            <LineChart data={series} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
              <XAxis dataKey="name" {...XAXIS} />
              <YAxis domain={[0, 100]} {...AXIS} width={30} />
              <Tooltip {...TIP} formatter={(v) => `${v}%`} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              {ex?.outside != null && <ReferenceLine y={Math.round(ex.outside * 100)} stroke="#f5a623" strokeDasharray="4 3" />}
              <Line dataKey="outside" name="Outside" stroke="#f5a623" strokeWidth={2} connectNulls isAnimationActive={false} />
              <Line dataKey="spread" name="Spread" stroke="#a78bfa" strokeWidth={2} connectNulls isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
      <p className="muted pg-note">Dashed lines: the expert flights.</p>
    </Section>
  );
}

function CoachingTable({ rows }) {
  return (
    <table className="pg-table">
      <thead>
        <tr><th>Callout</th><th>Times</th><th title="A control input within 10 s">Responded</th><th>Typical response</th>
          <th title="The value it was about moved the right way within 15 s">Fixed it</th><th title="No input and no fix">Ignored</th></tr>
      </thead>
      <tbody>
        {rows.map((c) => (
          <tr key={c.code}>
            <td>{c.label}</td>
            <td>{c.count}</td>
            <td>{c.responded} of {c.count}</td>
            <td>{num(c.median_reaction_s, 1, " s")}</td>
            <td>{c.judged ? <Rate n={c.helped} of={c.judged} good /> : <span className="muted">–</span>}</td>
            <td>{c.ignored ? <Rate n={c.ignored} of={c.count} /> : "0"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Rate({ n, of, good }) {
  const r = of ? n / of : 0;
  return (
    <span className="pg-rate">
      <span className="pg-bar pg-bar--sm"><span style={{ width: `${r * 100}%`, background: good ? "var(--good)" : "var(--danger)" }} /></span>
      {n} of {of}
    </span>
  );
}

function CoachingSection({ flights, coaching }) {
  if (!coaching.length) {
    return (
      <Section title="Does the coaching work?">
        <p className="muted">No MAVIS correction callouts recorded yet. (Flights recorded before this update don't carry the callout type.)</p>
      </Section>
    );
  }
  const data = flights.map((f) => ({
    name: shortLabel(f), react: f.stats?.callouts?.median_reaction_s ?? null,
    fixed: f.stats?.callouts?.judged ? Math.round((f.stats.callouts.helped / f.stats.callouts.judged) * 100) : null,
  }));
  return (
    <Section title="Does the coaching work?"
      sub="Each MAVIS correction callout: how long until a control input, and whether what it was about got better.">
      <CoachingTable rows={coaching} />
      <ResponsiveContainer width="100%" height={180}>
        <ComposedChart data={data} margin={{ top: 12, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
          <XAxis dataKey="name" {...XAXIS} />
          <YAxis yAxisId="s" {...AXIS} width={30} />
          <YAxis yAxisId="p" orientation="right" domain={[0, 100]} {...AXIS} width={34} />
          <Tooltip {...TIP} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Line yAxisId="s" dataKey="react" name="Typical response (s)" stroke="#60a5fa" strokeWidth={2} connectNulls isAnimationActive={false} />
          <Line yAxisId="p" dataKey="fixed" name="Fixed it (%)" stroke="var(--good)" strokeWidth={2} connectNulls isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </Section>
  );
}

function HandlingSection({ flights, table }) {
  const phases = phasesOf(table);
  const data = flights.map((f) => ({
    name: shortLabel(f), ias_sd: f.stats?.approach?.ias_sd ?? null,
    td: f.stats?.landing?.vs_fpm != null ? Math.abs(f.stats.landing.vs_fpm) : null,
  }));
  const tds = data.map((r) => r.td).filter((v) => v != null);
  const tdSpread = tds.length >= 3
    ? Math.sqrt(tds.reduce((s, v) => s + (v - tds.reduce((a, b) => a + b, 0) / tds.length) ** 2, 0) / tds.length) : null;
  if (!phases.length) return null;
  return (
    <Section title="Handling" sub="How steadily speed, height and sink rate were held in each phase, and how busy the hands were (last 3 flights / expert).">
      <table className="pg-table">
        <thead>
          <tr><th>Phase</th><th>Speed held within ±</th><th>Height held within ±</th><th>Sink rate within ±</th>
            <th title="Times the stick or pedals changed direction per minute">Control reversals / min</th></tr>
        </thead>
        <tbody>
          {phases.map((p) => {
            const m = table.mine?.[p] || {}, e = table.expert?.[p] || {};
            return (
              <tr key={p}>
                <td>{phaseName(p)}</td>
                <Compare mine={m.ias_sd} expert={e.ias_sd} fmt={(v) => num(v, 1, " kt")} tol={0.6} />
                <Compare mine={m.alt_sd} expert={e.alt_sd} fmt={(v) => num(v, 0, " ft")} tol={0.6} />
                <Compare mine={m.vs_sd} expert={e.vs_sd} fmt={(v) => num(v, 0, " fpm")} tol={0.6} />
                <Compare mine={m.reversals_per_min} expert={e.reversals_per_min} fmt={(v) => num(v, 0)} tol={0.6} />
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="pg-chart-title pg-gap-top">Approach and landing, flight by flight</p>
      <ResponsiveContainer width="100%" height={190}>
        <ComposedChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
          <XAxis dataKey="name" {...XAXIS} />
          <YAxis yAxisId="kt" {...AXIS} width={30} />
          <YAxis yAxisId="fpm" orientation="right" {...AXIS} width={40} />
          <Tooltip {...TIP} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Line yAxisId="kt" dataKey="ias_sd" name="Final approach speed ± (kt)" stroke="#60a5fa" strokeWidth={2} connectNulls isAnimationActive={false} />
          <Line yAxisId="fpm" dataKey="td" name="Touchdown sink rate (ft/min)" stroke="#f5a623" strokeWidth={2} connectNulls isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
      {tdSpread != null && (
        <p className="muted pg-note">Landing consistency: touchdown sink rate varies by ±{Math.round(tdSpread)} ft/min from flight to flight.</p>
      )}
    </Section>
  );
}

// ------------------------------------------------------------------ instructor dashboard
function GroupView() {
  const [d, err, reload] = usePending(() => api.progressCohort(), []);
  if (err) return <div className="card"><p className="err">{err}</p></div>;
  if (!d) return <div className="card"><p className="muted">Loading…</p></div>;
  const maxCause = Math.max(1, ...d.causes.map((c) => c.count));
  return (
    <>
      <Section title="Pilots side by side"
        sub={`Each trainee's last ${d.last} checked flights: ${d.pilots.length} trainee${d.pilots.length === 1 ? "" : "s"}, ${d.flights} flight${d.flights === 1 ? "" : "s"}.`}>
        <Pending n={d.pending} />
        <div className="pg-grid-wrap">
          <table className="pg-table">
            <thead>
              <tr><th>Trainee</th><th>Flights</th><th>Rules broken / flight</th><th>Trend</th><th>Main cause</th>
                <th>Final: airspeed every</th><th>Looking outside</th><th>Final speed ±</th><th>Touchdown</th><th>Callouts fixed</th></tr>
            </thead>
            <tbody>
              {d.pilots.map((p) => (
                <tr key={p.pilot}>
                  <td>{p.pilot}</td>
                  <td>{p.flights}</td>
                  <td>{p.breaches_per_flight}</td>
                  <td className={p.breaches_trend > 0 ? "pg-cmp--bad" : p.breaches_trend < 0 ? "pg-good" : ""}>
                    {p.breaches_trend == null ? "–" : p.breaches_trend > 0 ? `▲ ${p.breaches_trend}` : p.breaches_trend < 0 ? `▼ ${-p.breaches_trend}` : "steady"}
                  </td>
                  <td>{p.top_cause || "–"}</td>
                  <td>{num(p.final_airspeed_every_s, 1, " s")}</td>
                  <td>{pct(p.outside)}</td>
                  <td>{num(p.final_ias_sd, 1, " kt")}</td>
                  <td>{p.touchdown_fpm == null ? "–" : `${Math.abs(Math.round(p.touchdown_fpm))} fpm`}</td>
                  <td>{pct(p.callouts_helped)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
      <Section title="Rules broken most often">
        {d.rules.length === 0 ? <p className="muted">No rule checks yet.</p> : (
          <table className="pg-table">
            <thead><tr><th>Rule</th><th>Broken</th><th className="pg-bar-col" /><th>Trainees</th></tr></thead>
            <tbody>
              {d.rules.map((r) => (
                <tr key={r.title}>
                  <td>{r.title}</td>
                  <td>{r.broken} of {r.flights}</td>
                  <td className="pg-bar-col"><div className="pg-bar"><div style={{ width: `${(r.fail_rate || 0) * 100}%` }} /></div></td>
                  <td>{r.pilots}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>
      <Section title="Which handbook rules matter most in practice"
        sub="Ranked by how often they're broken, how serious they are, and how often breaking one is followed by breaking another within a minute (a knock-on).">
        {d.impact.length === 0 ? <p className="muted">No rule checks yet.</p> : (
          <table className="pg-table">
            <thead><tr><th>Rule</th><th>Severity</th><th>Broken in</th><th>Knock-on</th><th className="pg-bar-col">Weight</th></tr></thead>
            <tbody>
              {d.impact.map((r) => (
                <tr key={r.title}>
                  <td>{r.title}</td>
                  <td className={`pg-sev pg-sev--${r.severity}`}>{r.severity}</td>
                  <td>{pct(r.fail_rate)} of flights</td>
                  <td>{pct(r.knock_on)}</td>
                  <td className="pg-bar-col"><div className="pg-bar"><div style={{ width: `${Math.min(1, r.score / Math.max(...d.impact.map((x) => x.score), 1e-9)) * 100}%`, background: "var(--accent)" }} /></div></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>
      <Section title="Which callouts help, and which are ignored" sub="All trainees' flights above.">
        {d.coaching.length === 0 ? <p className="muted">No MAVIS correction callouts recorded yet.</p> : <CoachingTable rows={d.coaching} />}
      </Section>
      <Section title="Most common causes">
        {d.causes.length === 0 ? <p className="muted">Nothing found yet.</p> : (
          <ul className="pg-cause-bars">
            {d.causes.map((c) => (
              <li key={c.code}>
                <span className="pg-cause-bars__label">{c.label}</span>
                <div className="pg-bar"><div style={{ width: `${(c.count / maxCause) * 100}%`, background: KIND[c.kind]?.color }} /></div>
                <b>{c.count}</b>
              </li>
            ))}
          </ul>
        )}
      </Section>
      <ExpertFlights onChange={reload} count={d.expert_flights} />
    </>
  );
}

function ExpertFlights({ onChange, count }) {
  const [open, setOpen] = useState(false);
  const [list, setList] = useState(null);
  const [q, setQ] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => {
    if (open && !list) api.progressExpertFlights().then(setList).catch((e) => setErr(e.message));
  }, [open, list]);
  const shown = useMemo(() => (list || []).filter((f) => !q || `${f.pilot} ${f.label}`.toLowerCase().includes(q.toLowerCase())), [list, q]);
  const toggle = async (f) => {
    try {
      await api.setExpertFlight(f.id, !f.expert);
      setList((l) => l.map((x) => (x.id === f.id ? { ...x, expert: !f.expert } : x)));
      onChange();
    } catch (e) { setErr(e.message); }
  };
  return (
    <Section title={`Expert reference flights (${count})`}
      sub="Flights the trainees are compared with (scan pattern and handling, per phase). Usually an instructor flying the same lesson well. They're left out of the trainee figures.">
      {!open ? <button className="secondary" onClick={() => setOpen(true)}>Choose flights…</button> : (
        <>
          {err && <p className="err">{err}</p>}
          <input className="pg-search" placeholder="Filter by pilot or lesson" value={q} onChange={(e) => setQ(e.target.value)} />
          {!list ? <p className="muted">Loading…</p> : (
            <ul className="pg-expert">
              {shown.map((f) => (
                <li key={f.id}>
                  <label><input type="checkbox" checked={f.expert} onChange={() => toggle(f)} /> {f.pilot} · {f.label}</label>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </Section>
  );
}
