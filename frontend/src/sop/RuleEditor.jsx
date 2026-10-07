import { useEffect, useState } from "react";
import { KIND_LABEL, OP_TXT, metricInfo, phaseTxt, unitLabel, fmt, gazeIsShare, gazeShow, lessonTypes, canRef, refTxt,
  refValue, usedInText, notInNormal } from "./ruleText.js";

// Number field that lets you type "-", "1." etc. without the value jumping;
// reports null for an empty box.
function NumInput({ value, onChange, disabled, id }) {
  const [txt, setTxt] = useState(fmt(value));
  const [focus, setFocus] = useState(false);
  useEffect(() => { if (!focus) setTxt(fmt(value)); }, [value, focus]);
  return (
    <input
      id={id} type="text" inputMode="decimal" value={txt} disabled={disabled}
      onFocus={() => setFocus(true)} onBlur={() => { setFocus(false); setTxt(fmt(value)); }}
      onChange={(e) => {
        const t = e.target.value;
        setTxt(t);
        if (t.trim() === "") onChange(null);
        else if (!Number.isNaN(Number(t))) onChange(Number(t));
      }}
    />
  );
}

function Field({ label, children, wide, htmlFor, hint }) {
  return (
    <div className={"sop-field" + (wide ? " sop-field--wide" : "")}>
      <label htmlFor={htmlFor}>{label}{hint && <span className="sop-field__hint"> {hint}</span>}</label>
      {children}
    </div>
  );
}

function Num({ meta, rule, k, label, set, disabled }) {
  const id = `sop-${k}`;
  return (
    <Field label={label} htmlFor={id}>
      <div className="sop-unit">
        <NumInput id={id} value={rule[k]} onChange={(v) => set({ [k]: v })} disabled={disabled} />
        <em>{unitLabel(meta, rule.metric)}</em>
      </div>
    </Field>
  );
}

/** Form for one rule. Controlled: every change calls onChange(updatedRule). */
export default function RuleEditor({ meta, rule, onChange, readOnly = false, quoteEditable = false }) {
  const set = (patch) => onChange({ ...rule, ...patch });
  const dis = readOnly;
  const k = rule.kind;
  const metrics = meta.metrics;
  const rel = canRef(rule) && !!rule.ref_speed;      // numbers are differences from an aircraft speed

  function setKind(kind) {
    // keep what still makes sense; the server clears numbers for "not observable"
    const patch = { kind };
    if (kind !== "unobservable" && kind !== "gaze" && !rule.metric) patch.metric = "ias";
    if (kind === "gaze") {
      patch.aoi = rule.aoi || "outside";
      patch.gaze_measure = rule.gaze_measure || "min_share";
      if (!(rule.tags || []).includes("scan")) patch.tags = [...(rule.tags || []), "scan"].slice(0, 4);
    }
    if (kind === "event" && !rule.op) patch.op = ">=";
    if (kind === "capture" && !rule.capture_at) patch.capture_at = "liftoff";
    set(patch);
  }
  function togglePhase(p) {
    const has = (rule.phases || []).includes(p);
    set({ phases: has ? rule.phases.filter((x) => x !== p) : [...(rule.phases || []), p] });
  }
  const conds = rule.conditions || [];
  const setCond = (i, patch) => set({ conditions: conds.map((c, j) => (j === i ? { ...c, ...patch } : c)) });

  return (
    <div className="sop-grid">
      <Field label="Title" wide htmlFor="sop-title">
        <input id="sop-title" value={rule.title || ""} maxLength={120} disabled={dis}
          onChange={(e) => set({ title: e.target.value })} />
      </Field>
      <Field label="Rule type" htmlFor="sop-kind">
        <select id="sop-kind" value={k} disabled={dis} onChange={(e) => setKind(e.target.value)}>
          {meta.kinds.map((x) => <option key={x} value={x}>{KIND_LABEL[x]}</option>)}
        </select>
      </Field>
      {k !== "unobservable" && k !== "gaze" && (
        <Field label="Measured value" htmlFor="sop-metric">
          <select id="sop-metric" value={rule.metric || ""} disabled={dis} onChange={(e) => set({ metric: e.target.value })}>
            {metrics.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
          </select>
        </Field>
      )}
      <Field label="Severity" htmlFor="sop-sev">
        <select id="sop-sev" value={rule.severity || "caution"} disabled={dis} onChange={(e) => set({ severity: e.target.value })}>
          {meta.severities.map((s) => <option key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</option>)}
        </select>
      </Field>

      {k === "band" && <>
        <Num meta={meta} rule={rule} k="target" label={rel ? "Target (difference)" : "Target"} set={set} disabled={dis} />
        <Num meta={meta} rule={rule} k="tol_hi" label="Allowed above (+)" set={set} disabled={dis} />
        <Num meta={meta} rule={rule} k="tol_lo" label="Allowed below (−)" set={set} disabled={dis} />
      </>}
      {k === "limit" && <>
        <Num meta={meta} rule={rule} k="min_value" label={rel ? "Minimum (difference)" : "Minimum"} set={set} disabled={dis} />
        <Num meta={meta} rule={rule} k="max_value" label={rel ? "Maximum (difference)" : "Maximum"} set={set} disabled={dis} />
      </>}
      {k === "event" && <>
        <Field label="Must be" htmlFor="sop-op">
          <select id="sop-op" value={rule.op || ">="} disabled={dis} onChange={(e) => set({ op: e.target.value })}>
            {meta.ops.map((o) => <option key={o} value={o}>{OP_TXT[o]}</option>)}
          </select>
        </Field>
        <Num meta={meta} rule={rule} k="value" label="Value" set={set} disabled={dis} />
        <Field label="By start of" htmlFor="sop-gate">
          <select id="sop-gate" value={rule.gate_phase || ""} disabled={dis} onChange={(e) => set({ gate_phase: e.target.value || null })}>
            <option value="">(any time)</option>
            {meta.phases.map((p) => <option key={p} value={p}>{phaseTxt(p)}</option>)}
          </select>
        </Field>
      </>}
      {k === "capture" && <>
        <Field label="Measured at" htmlFor="sop-cap">
          <select id="sop-cap" value={rule.capture_at || "liftoff"} disabled={dis} onChange={(e) => set({ capture_at: e.target.value })}>
            {meta.capture_at.map((c) => <option key={c} value={c}>{c.replace(/_/g, " ")}</option>)}
          </select>
        </Field>
        <Num meta={meta} rule={rule} k="min_value" label="Minimum" set={set} disabled={dis} />
        <Num meta={meta} rule={rule} k="max_value" label="Maximum" set={set} disabled={dis} />
      </>}

      {rule.suggestion && !dis && ((k === "band" && rule.tol_hi == null && rule.tol_lo == null) || (k === "gaze" && rule.value == null)) && (
        <div className="sop-field sop-field--wide sop-suggest">
          <div>
            <b>FAA suggestion: {rule.suggestion.what.toLowerCase()} {rule.suggestion.shown}</b>
            <div className="muted sop-small">{rule.suggestion.text}.</div>
          </div>
          <button type="button" className="secondary" id="sop-suggest-fill"
            onClick={() => set({ ...rule.suggestion.fields, note: rule.suggestion.note })}>Fill in</button>
        </div>
      )}

      {canRef(rule) && meta.ref_speeds && (
        <Field label="Measured from" hint="(a speed that differs per aircraft, e.g. 1.3 × VSO)" wide htmlFor="sop-ref">
          <div className="sop-cond sop-ref">
            {rel && <>
              <div className="sop-unit">
                <NumInput id="sop-ref-factor" value={rule.ref_factor ?? 1} disabled={dis} onChange={(v) => set({ ref_factor: v ?? 1 })} />
                <em>×</em>
              </div>
            </>}
            <select id="sop-ref" value={rule.ref_speed || ""} disabled={dis}
              onChange={(e) => {
                const v = e.target.value || null;
                // switching it on: the numbers become differences from that speed
                set({ ref_speed: v, ref_factor: v ? rule.ref_factor ?? 1 : null,
                  ...(v && !rule.ref_speed && k === "band" ? { target: 0 } : {}) });
              }}>
              <option value="">A fixed number of knots</option>
              {meta.ref_speeds.map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}
            </select>
          </div>
          {rel && (
            <div className="muted sop-small sop-ref__note">
              The numbers above are differences from {refTxt(meta, rule)} in knots (0 = exactly that speed).{" "}
              {meta.aircraft.map((a) => {
                const v = refValue(meta, rule, a);
                return v == null ? `${a.name}: not in its profile, so the rule is skipped there.` : `${a.name}: ${refTxt(meta, rule)} = ${fmt(v)} kt.`;
              }).join(" ")}
            </div>
          )}
        </Field>
      )}

      {k === "gaze" && <>
        <Field label="Look at" htmlFor="sop-aoi" hint="(AOI zone role)">
          <select id="sop-aoi" value={rule.aoi || ""} disabled={dis} onChange={(e) => set({ aoi: e.target.value })}>
            {!rule.aoi && <option value="">Choose…</option>}
            {(meta.aoi_roles || []).map((a) => <option key={a.id} value={a.id} title={a.help}>{a.label}</option>)}
          </select>
        </Field>
        <Field label="Check" htmlFor="sop-gm">
          <select id="sop-gm" value={rule.gaze_measure || ""} disabled={dis}
            onChange={(e) => {
              const m = e.target.value;
              // seconds <-> share: a number typed for one makes no sense for the other
              const keep = gazeIsShare(m) === gazeIsShare(rule.gaze_measure);
              set({ gaze_measure: m, ...(keep ? {} : { value: null }) });
            }}>
            {!rule.gaze_measure && <option value="">Choose…</option>}
            {(meta.gaze_measures || []).map((g) => <option key={g.id} value={g.id} title={g.help}>{g.label}</option>)}
          </select>
        </Field>
        <Field label="Threshold" htmlFor="sop-gv" hint={rule.value == null ? "(set by the instructor)" : ""}>
          <div className="sop-unit">
            <NumInput id="sop-gv" value={gazeShow(rule.gaze_measure, rule.value)} disabled={dis}
              onChange={(v) => set({ value: v == null ? null : gazeIsShare(rule.gaze_measure) ? v / 100 : v })} />
            <em>{gazeIsShare(rule.gaze_measure) ? "%" : "s"}</em>
          </div>
        </Field>
      </>}

      {meta.tags && (
        <Field label="Tags" hint="(what the rule is about — up to 4)" wide>
          <div className="sop-phases">
            {meta.tags.map((t) => {
              const on = (rule.tags || []).includes(t.id);
              return (
                <button type="button" key={t.id} disabled={dis || (!on && (rule.tags || []).length >= 4)} title={t.help}
                  className={"sop-phase" + (on ? " is-on" : "")}
                  onClick={() => set({ tags: on ? rule.tags.filter((x) => x !== t.id) : [...(rule.tags || []), t.id] })}>
                  {t.label}
                </button>
              );
            })}
          </div>
        </Field>
      )}

      {meta.lesson_types && (
        <Field label="Used in" hint="(the kind of lesson that checks this rule)" wide>
          <div className="sop-phases" role="group" aria-label="Lesson types">
            {meta.lesson_types.map((t) => {
              const cur = lessonTypes(rule), on = cur.includes(t.id);
              return (
                <button type="button" key={t.id} title={t.help} aria-pressed={on} disabled={dis || (on && cur.length === 1)}
                  className={"sop-phase sop-phase--lesson" + (on ? " is-on" : "")}
                  onClick={() => set({ lesson_types: on ? cur.filter((x) => x !== t.id) : [...cur, t.id] })}>
                  {t.label}
                </button>
              );
            })}
          </div>
          <div className={"sop-small sop-usedin" + (notInNormal(rule) ? " sop-usedin--only" : " muted")}>
            {notInNormal(rule)
              ? `${usedInText(rule)}: this rule is never checked in a normal circuit lesson.`
              : lessonTypes(rule).length > 1 ? `${usedInText(rule)}.` : "Checked in normal circuit lessons."}
          </div>
        </Field>
      )}

      <Field label="Applies during" hint="(none selected = every phase)" wide>
        <div className="sop-phases">
          {meta.phases.map((p) => (
            <button type="button" key={p} disabled={dis}
              className={"sop-phase" + ((rule.phases || []).includes(p) ? " is-on" : "")}
              onClick={() => togglePhase(p)}>{phaseTxt(p)}</button>
          ))}
        </div>
      </Field>

      {k !== "unobservable" && k !== "gaze" && (
        <Field label="Only while" wide>
          {conds.length === 0 && <div className="muted sop-small">No conditions</div>}
          {conds.map((c, i) => (
            <div className="sop-cond" key={i}>
              <select aria-label="Condition value" value={c.metric} disabled={dis} onChange={(e) => setCond(i, { metric: e.target.value })}>
                {metrics.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
              </select>
              <select aria-label="Comparison" value={c.op} disabled={dis} onChange={(e) => setCond(i, { op: e.target.value })}>
                {meta.ops.map((o) => <option key={o} value={o}>{OP_TXT[o]}</option>)}
              </select>
              <div className="sop-unit">
                <NumInput value={c.value} disabled={dis} onChange={(v) => setCond(i, { value: v ?? 0 })} />
                <em>{unitLabel(meta, c.metric)}</em>
              </div>
              {!dis && <button type="button" className="secondary sop-icon" title="Remove condition"
                onClick={() => set({ conditions: conds.filter((_, j) => j !== i) })}>✕</button>}
            </div>
          ))}
          {!dis && <button type="button" className="secondary sop-small-btn"
            onClick={() => set({ conditions: [...conds, { metric: "flaps_deg", op: ">=", value: 0 }] })}>+ Condition</button>}
        </Field>
      )}

      <Field label={`Source${rule.source_page ? ` — page ${rule.source_page}` : ""}${rule.quote_score != null && rule.quote_score < 1 ? ` (${Math.round(rule.quote_score * 100)}% match)` : ""}`} wide htmlFor="sop-quote">
        {quoteEditable && !dis ? (
          <div className="sop-quote-edit">
            <textarea id="sop-quote" rows={6} value={rule.source_quote || ""}
              placeholder="Paste the exact SOP sentence this rule comes from (optional for rules you add)"
              onChange={(e) => set({ source_quote: e.target.value || null })} />
            <input aria-label="Page" type="number" min={1} placeholder="Page" value={rule.source_page || ""}
              onChange={(e) => set({ source_page: e.target.value ? Number(e.target.value) : null })} />
          </div>
        ) : (
          <blockquote className="sop-quote">{rule.source_quote || <span className="muted">No source — added by hand</span>}</blockquote>
        )}
      </Field>
      <Field label="Note" wide htmlFor="sop-note">
        <textarea id="sop-note" rows={2} value={rule.note || ""} disabled={dis}
          placeholder="Optional — e.g. why you changed it" onChange={(e) => set({ note: e.target.value || null })} />
      </Field>
    </div>
  );
}

export { metricInfo };
