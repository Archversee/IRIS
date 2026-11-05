import { useEffect, useMemo, useState } from "react";
import { api } from "../../api/client.js";
import { aoiColor, MIN_AOI_DWELL_SEC_DEFAULT } from "./constants.js";
import { buildScanRuns, scanTransitions } from "./utils.js";

// Analytics: how the pilot's eyes moved BETWEEN instruments -- counts of
// direct gaze moves for each pair of areas (either direction). A healthy
// scan cross-checks the attitude indicator with everything else; an area
// that never shows up here was never looked at.
export default function CrossCheckCard({ sessionId }) {
  const [eye, setEye] = useState(null);
  const [zones, setZones] = useState([]);
  const [err, setErr] = useState(null);

  useEffect(() => {
    api.eye(sessionId).then(setEye).catch((e) => setErr(e.message));
    api.listAoiZones().then(setZones).catch(() => {});
  }, [sessionId]);

  const data = useMemo(() => {
    if (!eye?.length) return null;
    const t0 = new Date(eye[0].ts).getTime();
    const elapsed = (ts) => (new Date(ts).getTime() - t0) / 1000;
    const endT = elapsed(eye[eye.length - 1].ts);
    const runs = buildScanRuns(eye, zones, elapsed, MIN_AOI_DWELL_SEC_DEFAULT, endT);
    const tr = scanTransitions(runs);
    const areas = [...tr.visits.keys()].sort((a, b) => tr.visits.get(b) - tr.visits.get(a));
    const color = new Map(areas.map((a, i) => [a, aoiColor(a, i)]));
    const lookup = new Map(tr.pairs.map((p) => [`${p.a}|${p.b}`, p.count]));
    const count = (a, b) => lookup.get(`${a}|${b}`) ?? lookup.get(`${b}|${a}`) ?? 0;
    const max = tr.pairs[0]?.count || 1;
    const missed = zones.map((z) => z.name).filter((n) => !tr.visits.has(n));
    return { ...tr, areas, color, count, max, minutes: endT / 60, missed };
  }, [eye, zones]);

  return (
    <div className="card xc">
      <div className="xc__head">
        <h3>Instrument cross-check</h3>
        <span className="muted">How often the gaze moved directly between each pair of areas ({MIN_AOI_DWELL_SEC_DEFAULT > 0 ? `glances ≥ ${MIN_AOI_DWELL_SEC_DEFAULT}s` : "every glance"})</span>
      </div>

      {err ? <p className="err">{err}</p>
        : !eye ? <p className="muted">Loading gaze data…</p>
        : !data ? <p className="muted">No eye-tracking data in this session.</p>
        : (
          <>
            <div className="readout xc__stats">
              <div className="metric"><div className="k">Moves between areas</div><div className="v">{data.total}</div></div>
              <div className="metric"><div className="k">Moves per minute</div><div className="v">{data.minutes > 0 ? (data.total / data.minutes).toFixed(1) : "—"}</div></div>
              <div className="metric"><div className="k">Areas looked at</div><div className="v">{data.areas.length}</div></div>
              <div className="metric"><div className="k">Most-used link</div>
                <div className="v xc__v-sm">{data.pairs[0] ? `${data.pairs[0].a} ↔ ${data.pairs[0].b}` : "—"}</div></div>
            </div>

            {data.missed.length > 0 && (
              <div className="xc__missed">
                Never looked at: {data.missed.map((n) => <span key={n} className="xc__missed-tag">{n}</span>)}
              </div>
            )}

            <div className="xc__grid">
              <div>
                <h4>Most frequent moves</h4>
                {data.pairs.length === 0 ? <p className="muted">No moves between areas.</p> : (
                  <ul className="xc__pairs">
                    {data.pairs.slice(0, 10).map((p) => (
                      <li key={p.a + p.b}>
                        <span className="xc__pair">
                          <span className="swatch" style={{ background: data.color.get(p.a) }} />{p.a}
                          <span className="xc__arrow">↔</span>
                          <span className="swatch" style={{ background: data.color.get(p.b) }} />{p.b}
                        </span>
                        <span className="xc__bar"><span style={{ width: `${(p.count / data.max) * 100}%` }} /></span>
                        <span className="xc__n">{p.count}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="xc__matrix-wrap">
                <h4>All pairs</h4>
                <table className="xc__matrix">
                  <thead>
                    <tr><th />{data.areas.map((a) => <th key={a} title={a}><span>{a}</span></th>)}</tr>
                  </thead>
                  <tbody>
                    {data.areas.map((r) => (
                      <tr key={r}>
                        <th title={r}>{r}</th>
                        {data.areas.map((c) => {
                          if (r === c) return <td key={c} className="xc__self" />;
                          const n = data.count(r, c);
                          return (
                            <td key={c} title={`${r} ↔ ${c}: ${n}`}
                              style={{ background: n ? `rgba(56, 189, 248, ${0.12 + 0.78 * (n / data.max)})` : undefined }}>
                              {n || ""}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )}
    </div>
  );
}
