// How a pilot is getting on across flights, and (instructors) the whole group.
// Post-flight only: built from the SOP rule checks, the analysis of each
// broken-rule moment and the eye tracker. Backend: backend/app/routers/progress.py.
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Bar, CartesianGrid, ComposedChart, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import "./progress.css";

const VERDICT = {
  pass: { sym: "✓", label: "Met", cls: "pass" },
  fail: { sym: "✗", label: "Not met", cls: "fail" },
  instructor: { sym: "?", label: "Instructor to assess", cls: "assess" },
  not_applicable: { sym: "–", label: "Didn't apply", cls: "na" },
  insufficient_data: { sym: "·", label: "No data", cls: "na" },
};
const INSIGHT_TITLE = {
  improving: "Improving", persistent: "Keeps happening", regression: "Slipped back",
  cause: "Why it happens", scan: "Scan habit", info: "",
};
const KIND = {
  perception: { label: "Seeing it (scan / attention)", color: "#60a5fa" },
  action: { label: "Acting on it", color: "#f5a623" },
  handling: { label: "Handling (over-correcting)", color: "#a78bfa" },
  workload: { label: "Workload", color: "#94a3b8" },
};
const AXIS = { stroke: "var(--muted)", tick: { fontSize: 11 } };
const XAXIS = { ...AXIS, interval: 0, padding: { left: 24, right: 24 } };

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
              <button className={tab === "pilot" ? "" : "secondary"} onClick={() => setTab("pilot")}>One pilot</button>
              <button className={tab === "group" ? "" : "secondary"} onClick={() => setTab("group")}>Group</button>
            </div>
          )}
        </div>
        <p className="muted pg-sub">
          After-flight trends from the SOP rule checks, the analysis of each broken rule and the eye tracker.
        </p>
        {err && <p className="err">{err}</p>}
        {tab === "pilot" && pilots && (
          pilots.length === 0
            ? <p className="muted">No flights with a pilot name yet.</p>
            : pilots.length > 1 && (
              <label className="pg-pick">
                Pilot{" "}
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
  useEffect(() => {
    let alive = true, timer;
    setData(null);
    setErr(null);
    const go = () => load()
      .then((d) => {
        if (!alive) return;
        setData(d);
        if (d.pending > 0) timer = setTimeout(go, 1500);
      })
      .catch((e) => alive && setErr(e.message));
    go();
    return () => { alive = false; clearTimeout(timer); };
  }, deps); // eslint-disable-line react-hooks/exhaustive-deps
  return [data, err];
}

function Pending({ n }) {
  return n > 0 ? <p className="pg-pending">Still analysing {n} item{n === 1 ? "" : "s"} from older flights…</p> : null;
}

function PilotView({ name }) {
  const [d, err] = usePending(() => api.progressPilot(name), [name]);
  if (err) return <div className="card"><p className="err">{err}</p></div>;
  if (!d) return <div className="card"><p className="muted">Loading…</p></div>;
  const checked = d.flights.filter((f) => f.checked);
  const unchecked = d.flights.length - checked.length;
  return (
    <>
      <div className="card">
        <h3>{d.pilot}: what stands out</h3>
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
          {unchecked > 0 && `, ${unchecked} without SOP rule checks (not counted)`}.
        </p>
      </div>
      {checked.length > 0 && <RuleGrid flights={checked} titles={d.rule_titles} />}
      {checked.length > 0 && <CauseChart flights={checked} causes={d.causes} kinds={d.cause_kind} />}
      <ScanCharts flights={d.flights} />
    </>
  );
}

function shortLabel(f) {
  return f.label.split(" · ")[0];
}

function RuleGrid({ flights, titles }) {
  const rows = useMemo(() => {
    // Rules that were ever broken first, then the rest.
    const fails = (t) => flights.filter((f) => f.rules[t] === "fail").length;
    return [...titles].sort((a, b) => fails(b) - fails(a) || a.localeCompare(b));
  }, [flights, titles]);
  return (
    <div className="card">
      <h3>Rules, flight by flight</h3>
      <div className="pg-grid-wrap">
        <table className="pg-grid">
          <thead>
            <tr>
              <th className="pg-grid__rule">Rule</th>
              {flights.map((f) => (
                <th key={f.id} title={f.label}>
                  <Link to={`/sessions/${f.id}/review`}>{shortLabel(f)}</Link>
                  {f.signed_off && <span className="pg-signed" title="Signed off by an instructor">✔</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => (
              <tr key={t}>
                <td className="pg-grid__rule">{t}</td>
                {flights.map((f) => {
                  const v = VERDICT[f.rules[t]];
                  return (
                    <td key={f.id} className={v ? `pg-v pg-v--${v.cls}` : "pg-v pg-v--na"} title={v ? v.label : "Not in this check"}>
                      {v ? v.sym : ""}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted pg-note">✓ met · ✗ not met · ? instructor to assess · – didn't apply. Instructor verdicts replace the computed ones. Click a flight to open it.</p>
    </div>
  );
}

function CauseChart({ flights, causes, kinds }) {
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
  if (!flights.some((f) => f.breaches)) {
    return <div className="card"><h3>Broken rules and why</h3><p className="muted">No broken rules in these flights.</p></div>;
  }
  return (
    <div className="card">
      <h3>Broken rules and why</h3>
      <div className="pg-chart">
        <ResponsiveContainer width="100%" height={220}>
          <ComposedChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
            <XAxis dataKey="name" {...XAXIS} />
            <YAxis allowDecimals={false} {...AXIS} width={30} />
            <Tooltip contentStyle={{ background: "var(--panel)", border: "1px solid var(--border)" }} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            {used.map((k) => <Bar key={k} dataKey={k} name={KIND[k].label} stackId="c" fill={KIND[k].color} />)}
            <Line dataKey="breaches" name="Times a rule was broken" stroke="var(--danger)" strokeWidth={2} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <p className="muted pg-note">Bars: what the analysis of each broken rule found. A broken rule with nothing found (e.g. a quick, clean recovery) only counts on the line.</p>
      {top.length > 0 && (
        <>
          <p className="muted pg-note">Causes in the last {Math.min(5, flights.length)} flights (one broken rule can have more than one):</p>
          <ul className="pg-causes">
            {top.map(([c, n]) => (
              <li key={c}><span className="pg-dot" style={{ background: KIND[kinds[c]]?.color }} />{causes[c]} <b>{n}</b></li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function ScanCharts({ flights }) {
  const data = flights.map((f) => ({
    name: shortLabel(f),
    asi: f.scan?.approach?.airspeed_check_every_s ?? null,
    alt: f.scan?.approach?.altimeter_check_every_s ?? null,
    outside: f.scan?.airborne?.share?.outside != null ? Math.round(f.scan.airborne.share.outside * 100) : null,
    coverage: f.scan?.eye_coverage,
  }));
  if (!data.some((r) => r.asi != null || r.outside != null)) {
    return <div className="card"><h3>Scan habits</h3><p className="muted">No eye-tracking data in these flights yet.</p></div>;
  }
  const thin = data.filter((r) => r.coverage != null && r.coverage < 0.6).map((r) => r.name);
  return (
    <div className="card">
      <h3>Scan habits</h3>
      <div className="pg-two">
        <div>
          <p className="pg-chart-title">On base and final: seconds between looks (lower = checked more often)</p>
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
              <XAxis dataKey="name" {...XAXIS} />
              <YAxis {...AXIS} width={30} />
              <Tooltip contentStyle={{ background: "var(--panel)", border: "1px solid var(--border)" }} formatter={(v) => `${v} s`} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Line dataKey="asi" name="Airspeed" stroke="#60a5fa" strokeWidth={2} connectNulls isAnimationActive={false} />
              <Line dataKey="alt" name="Altimeter" stroke="#4ade80" strokeWidth={2} connectNulls isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div>
          <p className="pg-chart-title">While airborne: % of the time looking outside</p>
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
              <XAxis dataKey="name" {...XAXIS} />
              <YAxis domain={[0, 100]} {...AXIS} width={30} />
              <Tooltip contentStyle={{ background: "var(--panel)", border: "1px solid var(--border)" }} formatter={(v) => `${v}%`} />
              <Line dataKey="outside" name="Outside" stroke="#f5a623" strokeWidth={2} connectNulls isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
      {thin.length > 0 && (
        <p className="pg-pending">Eye tracker covered less than 60% of the flight on {thin.join(", ")}: read those points with care.</p>
      )}
      <p className="muted pg-note">Instruments are recognised from the AOI zone names (Settings → AOI Zones).</p>
    </div>
  );
}

function GroupView() {
  const [d, err] = usePending(() => api.progressCohort(), []);
  if (err) return <div className="card"><p className="err">{err}</p></div>;
  if (!d) return <div className="card"><p className="muted">Loading…</p></div>;
  const maxCause = Math.max(1, ...d.causes.map((c) => c.count));
  return (
    <>
      <div className="card">
        <h3>Rules most often broken</h3>
        <p className="muted pg-note">Each pilot's last {d.last} checked flights: {d.pilots} pilot{d.pilots === 1 ? "" : "s"}, {d.flights} flight{d.flights === 1 ? "" : "s"}.</p>
        <Pending n={d.pending} />
        {d.rules.length === 0 ? <p className="muted">No rule checks yet.</p> : (
          <table className="pg-table">
            <thead><tr><th>Rule</th><th>Broken</th><th className="pg-bar-col" /><th>Pilots</th></tr></thead>
            <tbody>
              {d.rules.map((r) => (
                <tr key={r.title}>
                  <td>{r.title}</td>
                  <td>{r.failed} of {r.flights}</td>
                  <td className="pg-bar-col"><div className="pg-bar"><div style={{ width: `${(r.fail_rate || 0) * 100}%` }} /></div></td>
                  <td>{r.pilots_failed}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <div className="card">
        <h3>Most common causes</h3>
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
        <div className="pg-legend">
          {Object.values(KIND).map((k) => <span key={k.label}><span className="pg-dot" style={{ background: k.color }} />{k.label}</span>)}
        </div>
      </div>
    </>
  );
}
