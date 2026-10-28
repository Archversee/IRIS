// Review → Incidents: every time this flight broke an SOP rule, in order.
// Clicking one jumps the videos and timeline there and opens its ±30 s
// close-up (IncidentView: value against its limits, where the pilot was
// looking, the controls, MAVIS's callouts, the AI debrief note with
// instructor feedback). Post-flight only. Backend: GET /sessions/{id}/incidents.
import { useEffect, useState } from "react";
import { api } from "../../api/client.js";
import IncidentView from "./IncidentView.jsx";
import { RUNNING } from "./DebriefPanel.jsx";

const LEAD_S = 5;      // start the video this long before the limit was crossed
const KIND_COLOR = {
  perception: "#60a5fa", action: "#f5a623", handling: "#a78bfa", workload: "#94a3b8",
  context: "#64748b", positive: "#4ade80",
};

export function IncidentsChip({ count, onClick }) {
  return (
    <button className={"rc-chip" + (count ? " has-fail" : "")} onClick={onClick}
      title="Each broken rule: a ±30 s close-up with the video at that moment">
      Incidents{count != null ? ` ${count}` : ""}
    </button>
  );
}

function mmss(s) {
  if (s == null || !isFinite(s)) return "–";
  const t = Math.max(0, Math.round(s));
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
}

export default function IncidentsPanel({ sessionId, seekTo, flightStartMs, onCount, debriefJob }) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState("");
  const [open, setOpen] = useState(null);     // {resultId, index}

  useEffect(() => {
    let alive = true, timer;
    const go = () => api.sessionIncidents(sessionId)
      .then((x) => {
        if (!alive) return;
        setD(x);
        onCount?.(x.incidents.length);
        if (x.pending > 0) timer = setTimeout(go, 800);   // the backend analyses a few at a time
      })
      .catch((e) => alive && setErr(e.detail || e.message));
    go();
    return () => { alive = false; clearTimeout(timer); };
    // debriefJob.done: a note was just written in the background -> refresh the "AI note" badges
  }, [sessionId, debriefJob?.done, debriefJob?.state]); // eslint-disable-line react-hooks/exhaustive-deps

  const toT = (epoch) => (flightStartMs ? epoch - flightStartMs / 1000 : null);
  const pick = (i) => {
    const t = i.t0 != null ? toT(i.t0) : null;
    if (t != null) seekTo(Math.max(0, t - LEAD_S));
    setOpen({ resultId: i.result_id, index: i.index });
  };

  return (
    <>
      {open && (
        <IncidentView resultId={open.resultId} index={open.index} onClose={() => setOpen(null)} debriefJob={debriefJob}
          onWatch={(epoch) => { setOpen(null); seekTo(Math.max(0, toT(epoch) ?? 0)); }} />
      )}
      <div className="rc-pane" role="region" aria-label="Incidents">
        <div className="rc-head">
          <h2>Incidents</h2>
        </div>
        <div className="rc-muted inc-list-sub">
          Each time a rule was broken. Click one to jump the video there and see the 30 s before and after.
        </div>
        {err && <div className="rc-err">{err}</div>}
        {!d && !err && <div className="rc-empty">Loading…</div>}
        {d && !d.checked && <div className="rc-empty">This flight hasn't been checked against its SOP rules yet (see SOP rule checks above).</div>}
        {d?.checked && d.incidents.length === 0 && <div className="rc-empty">No rules broken in this flight.</div>}
        {d?.pending > 0 && <div className="inc-list-pending">Analysing {d.pending} more…</div>}
        {RUNNING(debriefJob) && <div className="inc-list-pending">AI notes: {debriefJob.state}…</div>}
        {d?.incidents.map((i) => {
          const shown = (i.findings || []).filter((f) => f.kind !== "data");
          return (
            <button key={`${i.result_id}-${i.index}`} className={`rc-rule sev-${i.severity} inc-item`} onClick={() => pick(i)}>
              <div className="inc-item__head">
                <span className="inc-item__time">{mmss(i.t0 != null ? toT(i.t0) : null)}</span>
                <span className="rc-title">{i.title}</span>
                {i.explained && <span className="inc-item__ai" title="Has an AI debrief note">AI note</span>}
                <span className="inc-item__go">›</span>
              </div>
              <div className="rc-muted inc-item__meta">
                {[i.phase, i.peak_text, i.dur_s != null && `outside for ${i.dur_s.toFixed(1)} s`].filter(Boolean).join(" · ")}
              </div>
              {i.pending ? <div className="rc-muted">Analysing…</div>
                : i.findings == null ? <div className="rc-muted">Couldn't be analysed.</div>
                : shown.length > 0 && (
                  <ul className="inc-item__findings">
                    {shown.slice(0, 3).map((f) => (
                      <li key={f.code}><span className="inc-item__dot" style={{ background: KIND_COLOR[f.kind] || "#64748b" }} />{f.text}</li>
                    ))}
                    {shown.length > 3 && <li className="rc-muted">+{shown.length - 3} more</li>}
                  </ul>
                )}
            </button>
          );
        })}
      </div>
    </>
  );
}
