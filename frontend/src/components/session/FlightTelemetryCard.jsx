import { useState } from "react";
import { fmt } from "./utils.js";

// Flight telemetry for Live Monitor and Review (the current instant), drawn the way MAVIS's own in-flight
// panel does it (vertical speed/height/climb tapes, stick-style control bars)
// plus an attitude indicator, with a toggle to the raw numbers.
//
// On the Live Monitor, `bands` (MAVIS's state.bands, for the step being
// flown) adds what's allowed: a green zone for the range the lesson asks
// for (kind "band") or red past a safety limit (kind "limit"), a tick at the
// target, and the reading turns amber when it's outside.
//
// Units (flight_data, see db/schema.sql + backend/app/mavis_bridge.py):
// aileron/elevator/rudder are SimConnect "Position" (-1..1; + elevator =
// stick back / nose up, - aileron = left), throttle/flaps are 0..100 %,
// elevator/aileron trim are -100..100 % (aileron + = right, as for the controls).
// (rudder trim is still logged in flight_data.rudder_trim_pct, just not shown.) Anything a source doesn't log comes through null.

const SPEED_MAX = 163; // C172 Vne, same scale MAVIS uses
const CLIMB_MAX = 1200;

const clamp01 = (v) => Math.max(0, Math.min(1, v));
const num = (v) => (v == null || Number.isNaN(+v) ? null : +v);

// Is v inside the band? null when there's no band (or no reading) to judge by.
function inBand(v, band) {
  if (v == null || !band || (band.lo == null && band.hi == null)) return null;
  return (band.lo == null || v >= band.lo) && (band.hi == null || v <= band.hi);
}

// "aim 80 · 70–90" / "max 900" -- the band in words, under a reading.
function bandText(band, unit = "") {
  if (!band) return null;
  const n = (x) => `${Math.round(x)}${unit}`;
  const parts = [];
  if (band.target != null) parts.push(`aim ${n(band.target)}`);
  if (band.kind === "limit") {
    if (band.lo != null && band.hi != null) parts.push(`limit ${n(band.lo)}…${n(band.hi)}`);
    else if (band.lo != null) parts.push(`min ${n(band.lo)}`);
    else if (band.hi != null) parts.push(`max ${n(band.hi)}`);
  } else if (band.lo != null && band.hi != null) {
    parts.push(`${n(band.lo)}–${n(band.hi)}`);
  }
  return parts.join(" · ") || null;
}

const NBSP = "\u00a0";

function Tape({ label, unit, value, min, max, title, band }) {
  const v = num(value);
  const at = (x) => clamp01((x - min) / (max - min)) * 100;
  const pct = at(v ?? min);
  const zeroPct = min < 0 ? at(0) : null;
  const ok = inBand(v, band);
  // "band": green between lo and hi; "limit": red beyond lo / hi
  const zones = [];
  if (band?.kind === "limit") {
    if (band.lo != null) zones.push({ cls: "bad", from: 0, to: at(band.lo) });
    if (band.hi != null) zones.push({ cls: "bad", from: at(band.hi), to: 100 });
  } else if (band && (band.lo != null || band.hi != null)) {
    zones.push({ cls: "ok", from: at(band.lo ?? min), to: at(band.hi ?? max) });
  }
  return (
    <div className="ft-tape" title={title}>
      <div className="ft-tape__label">{label}<small>{unit}</small></div>
      <div className="ft-tape__bar">
        {zones.map((z, i) => (
          <div key={i} className={`ft-tape__zone ft-tape__zone--${z.cls}`}
            style={{ bottom: `${z.from}%`, height: `${Math.max(0, z.to - z.from)}%` }} />
        ))}
        <b className="ft-tape__mx">{max}</b>
        {zeroPct != null && <div className="ft-tape__zero" style={{ bottom: `${zeroPct}%` }} />}
        {band?.target != null && <div className="ft-tape__tgt" style={{ bottom: `${at(band.target)}%` }} />}
        <div className={"ft-tape__ptr" + (ok === false ? " is-out" : "")} style={{ bottom: `${pct}%` }} />
        <b className="ft-tape__mn">{min}</b>
      </div>
      <div className={"ft-tape__val" + (ok === false ? " is-out" : ok ? " is-ok" : "")}>{v == null ? "--" : Math.round(v)}</div>
      <div className="ft-band-txt">{bandText(band) || NBSP}</div>
    </div>
  );
}

// Point on a dial: angle in degrees clockwise from straight up.
const polar = (r, deg) => [r * Math.sin((deg * Math.PI) / 180), -r * Math.cos((deg * Math.PI) / 180)];
function arc(r, a0, a1) {
  const [x0, y0] = polar(r, a0), [x1, y1] = polar(r, a1);
  return `M${x0} ${y0} A${r} ${r} 0 ${Math.abs(a1 - a0) > 180 ? 1 : 0} 1 ${x1} ${y1}`;
}

// Bank words: 45° L / 38–52° L (bank + = right, - = left).
function bankText(band) {
  if (!band) return null;
  const side = (x) => (Math.round(x) === 0 ? "0°" : `${Math.abs(Math.round(x))}°${x < 0 ? " L" : " R"}`);
  const parts = [];
  if (band.target != null) parts.push(`aim ${side(band.target)}`);
  if (band.lo != null && band.hi != null) {
    if (band.kind === "limit" && band.lo === -band.hi) parts.push(`max ${Math.round(band.hi)}°`);
    else if (band.lo === -band.hi) parts.push(`within ${Math.round(band.hi)}°`);
    else if (band.lo < 0 && band.hi <= 0) parts.push(`${Math.round(-band.hi)}–${Math.round(-band.lo)}° L`);
    else if (band.lo >= 0) parts.push(`${Math.round(band.lo)}–${Math.round(band.hi)}° R`);
  }
  return parts.join(" · ") || null;
}

// Bank: a top-half dial, 0 at the top, right bank clockwise, with the step's
// bank band or the steep-bank limit.
const BANK_MAX = 60;
function BankDial({ bank, band }) {
  const b = num(bank);
  const ang = (x) => Math.max(-BANK_MAX, Math.min(BANK_MAX, x)) * (90 / BANK_MAX);
  const ok = inBand(b, band);
  const zones = [];
  if (band?.kind === "limit") {
    if (band.lo != null) zones.push(["bad", -BANK_MAX, band.lo]);
    if (band.hi != null) zones.push(["bad", band.hi, BANK_MAX]);
  } else if (band && band.lo != null && band.hi != null) {
    zones.push(["ok", band.lo, band.hi]);
  }
  const [nx, ny] = polar(38, ang(b ?? 0));
  const tick = (d, r0, r1) => { const [x0, y0] = polar(r0, ang(d)), [x1, y1] = polar(r1, ang(d)); return { x1: x0, y1: y0, x2: x1, y2: y1 }; };
  return (
    <div className="ft-dial" title="Bank angle: how far the wings are tilted. Right bank swings the needle right.">
      <div className="ft-tape__label">BANK<small>degrees</small></div>
      <svg viewBox="-50 -50 100 62" className="ft-dial__svg">
        <path d={arc(40, -90, 90)} className="ft-dial__track" />
        {zones.map(([cls, lo, hi], i) => (
          <path key={i} d={arc(40, ang(lo), ang(hi))} className={`ft-dial__zone ft-dial__zone--${cls}`} />
        ))}
        {[-60, -45, -30, -15, 0, 15, 30, 45, 60].map((d) => (
          <line key={d} {...tick(d, 44, d % 30 ? 47 : 49)} className="ft-dial__tick" />
        ))}
        {band?.target != null && <line {...tick(band.target, 33, 46)} className="ft-dial__tgt" />}
        <line x1="0" y1="0" x2={nx} y2={ny} className={"ft-dial__needle" + (ok === false ? " is-out" : "")} />
        <circle r="3" className="ft-dial__hub" />
      </svg>
      <div className={"ft-tape__val" + (ok === false ? " is-out" : ok ? " is-ok" : "")}>
        {b == null ? "--" : `${Math.abs(Math.round(b))}°${Math.round(b) ? (b < 0 ? " L" : " R") : ""}`}
      </div>
      <div className="ft-band-txt">{bankText(band) || NBSP}</div>
    </div>
  );
}

// Heading: a compass card that turns under a fixed lubber line.
function Compass({ heading }) {
  const h = num(heading);
  return (
    <div className="ft-dial" title="Heading: which way the nose points (true degrees).">
      <div className="ft-tape__label">HEADING<small>degrees</small></div>
      <svg viewBox="-50 -50 100 100" className="ft-dial__svg ft-dial__svg--round">
        <circle r="46" className="ft-dial__face" />
        <g transform={`rotate(${h == null ? 0 : -h})`} className="ft-compass__card">
          {Array.from({ length: 36 }, (_, i) => i * 10).map((d) => {
            const [x0, y0] = polar(46, d), [x1, y1] = polar(d % 30 ? 42 : 38, d);
            return <line key={d} x1={x0} y1={y0} x2={x1} y2={y1} className="ft-dial__tick" />;
          })}
          {[["N", 0], ["E", 90], ["S", 180], ["W", 270]].map(([t, d]) => {
            const [x, y] = polar(29, d);
            return <text key={t} x={x} y={y} className={"ft-compass__lbl" + (t === "N" ? " is-n" : "")}
              transform={`rotate(${d} ${x} ${y})`}>{t}</text>;
          })}
        </g>
        <path d="M0 -47 L-4 -38 H4 Z" className="ft-dial__lubber" />
        <path d="M0 -14 L-6 10 L0 6 L6 10 Z" className="ft-compass__plane" />
      </svg>
      <div className="ft-tape__val">{h == null ? "--" : `${String(Math.round(h) % 360).padStart(3, "0")}°`}</div>
      <div className="ft-band-txt">{NBSP}</div>
    </div>
  );
}

// Artificial horizon: sky/ground disc rotated by bank and shifted by pitch.
function Attitude({ pitch, bank, heading }) {
  const p = num(pitch) ?? 0, b = num(bank) ?? 0;
  const shift = Math.max(-40, Math.min(40, p * 2)); // 2 px per degree in a 100px box
  return (
    <div className="ft-att" title="Attitude: sky above, ground below. Tilts with bank, moves with pitch.">
      <div className="ft-tape__label">ATTITUDE<small>pitch · bank</small></div>
      <svg viewBox="-50 -50 100 100" className="ft-att__svg">
        <defs><clipPath id="ft-att-clip"><circle r="48" /></clipPath></defs>
        <g clipPath="url(#ft-att-clip)">
          <g transform={`rotate(${-b}) translate(0 ${shift})`}>
            <rect x="-150" y="-150" width="300" height="150" fill="#2f6fb0" />
            <rect x="-150" y="0" width="300" height="150" fill="#7a5530" />
            <line x1="-150" y1="0" x2="150" y2="0" stroke="#fff" strokeWidth="1.2" />
            {[-20, -10, 10, 20].map((d) => (
              <line key={d} x1={-Math.abs(d) / 1.5} x2={Math.abs(d) / 1.5} y1={-d * 2} y2={-d * 2}
                stroke="#fff" strokeOpacity="0.7" strokeWidth="0.8" />
            ))}
          </g>
        </g>
        <circle r="48" fill="none" stroke="#2d3a48" strokeWidth="2" />
        <path d="M-24 0 H-8 L0 6 L8 0 H24" fill="none" stroke="#ffb64d" strokeWidth="2.4" strokeLinejoin="round" />
        <circle r="1.8" fill="#ffb64d" />
      </svg>
      <div className="ft-att__vals">
        <span>{num(pitch) == null ? "--" : `${fmt(p, 0)}°`} <small>pitch</small></span>
        <span>{num(bank) == null ? "--" : `${fmt(b, 0)}°`} <small>bank</small></span>
        <span>{num(heading) == null ? "--" : `${String(Math.round(num(heading)) % 360).padStart(3, "0")}°`} <small>hdg</small></span>
      </div>
    </div>
  );
}

// Fills from the bottom (0..1).
function VBar({ label, value, color, text, title }) {
  const v = num(value);
  return (
    <div className="ft-ctl" title={title}>
      <div className="ft-vbar">
        <div className="ft-vbar__fill" style={{ height: `${clamp01(v ?? 0) * 100}%`, background: color }} />
      </div>
      <div className="ft-ctl__cap">{label}</div>
      <div className="ft-ctl__val">{v == null ? "--" : text}</div>
    </div>
  );
}

// Centred bar for -1..1 inputs: fills up/right from the middle for +, down/left for -.
function CenterBar({ label, value, horizontal, text, title }) {
  const v = num(value);
  const x = Math.max(-1, Math.min(1, v ?? 0));
  const size = `${Math.abs(x) * 50}%`;
  const style = horizontal
    ? { width: size, [x >= 0 ? "left" : "right"]: "50%" }
    : { height: size, [x >= 0 ? "bottom" : "top"]: "50%" };
  return (
    <div className="ft-ctl" title={title}>
      <div className={horizontal ? "ft-hbar" : "ft-vbar"}>
        <div className={horizontal ? "ft-hbar__mid" : "ft-vbar__mid"} />
        <div className={horizontal ? "ft-hbar__fill" : "ft-vbar__fill ft-vbar__fill--center"} style={style} />
      </div>
      <div className="ft-ctl__cap">{label}</div>
      <div className="ft-ctl__val">{v == null ? "--" : text(x)}</div>
    </div>
  );
}

// 2D stick position, like MAVIS's stick pad: left/right = aileron (roll),
// up/down = elevator (stick forward at the top, pulled back at the bottom).
function StickPad({ aileron, elevator }) {
  const a = num(aileron), e = num(elevator);
  const x = 50 + Math.max(-1, Math.min(1, a ?? 0)) * 45;
  const y = 50 + Math.max(-1, Math.min(1, e ?? 0)) * 45;
  const words = [];
  if (e != null && Math.abs(e) >= 0.08) words.push(e > 0 ? "back" : "fwd");
  if (a != null && Math.abs(a) >= 0.08) words.push(a < 0 ? "left" : "right");
  return (
    <div className="ft-ctl ft-ctl--stick"
      title="The dot is the control stick: left/right = roll, up/down = pushed forward (nose down) or pulled back (nose up).">
      <div className="ft-stick">
        <div className="ft-stick__dot" style={{ left: `${x}%`, top: `${y}%` }} />
      </div>
      <div className="ft-ctl__cap">STICK</div>
      <div className="ft-ctl__val">{a == null && e == null ? "--" : words.join(" · ") || "center"}</div>
    </div>
  );
}

function RO({ k, v }) {
  return <div className="ro-row"><span className="k">{k}</span><span className="v">{v}</span></div>;
}

function Numbers({ cur }) {
  return (
    <div className="readout-cols">
      <div className="ro-group">
        <h4>Flight state</h4>
        <RO k="Airspeed" v={`${fmt(cur.airspeed_kt)} kt`} />
        <RO k="Altitude" v={`${fmt(cur.altitude_ft, 0)} ft`} />
        <RO k="Vert speed" v={`${fmt(cur.vertical_speed_fpm, 0)} fpm`} />
        <RO k="Heading" v={`${fmt(cur.heading_true_deg, 0)}°`} />
        <RO k="Pitch" v={`${fmt(cur.pitch_deg)}°`} />
        <RO k="Bank" v={`${fmt(cur.bank_deg)}°`} />
        <RO k="Yaw rate" v={fmt(cur.yaw_rate_radps, 3)} />
      </div>
      <div className="ro-group">
        <h4>Control input</h4>
        <RO k="Throttle" v={`${fmt(cur.throttle_pct, 0)}%`} />
        <RO k="Aileron" v={fmt(cur.aileron_position, 2)} />
        <RO k="Elevator" v={fmt(cur.elevator_position, 2)} />
        <RO k="Rudder" v={fmt(cur.rudder_position, 2)} />
        <RO k="Elev trim" v={`${fmt(cur.elevator_trim_pct)}%`} />
        <RO k="Aileron trim" v={`${fmt(cur.aileron_trim_pct)}%`} />
        <RO k="Flaps" v={`${fmt(cur.flaps_handle_pct, 0)}%`} />
        <RO k="Gear" v={cur.gear_handle_position ? "DOWN" : "UP"} />
      </div>
      <div className="ro-group">
        <h4>Environment</h4>
        <RO k="Fuel" v={`${fmt(cur.fuel_total_qty_gal)} gal`} />
        <RO k="Wind" v={`${fmt(cur.ambient_wind_kt)} kt`} />
        <RO k="Lat" v={fmt(cur.latitude, 4)} />
        <RO k="Long" v={fmt(cur.longitude, 4)} />
      </div>
    </div>
  );
}

// MAVIS-sourced rows always carry flaps_deg (flap surface angle), even
// from builds that don't send the flap-handle %; fall back to it so the
// flaps gauge works either way (C172 full flap = 30°).
const FLAPS_FULL_DEG = 30;
function withDerived(row) {
  if (!row || num(row.flaps_handle_pct) != null || num(row.flaps_deg) == null) return row;
  return { ...row, flaps_handle_pct: clamp01(row.flaps_deg / FLAPS_FULL_DEG) * 100 };
}

export default function FlightTelemetryCard({ cur: rawCur, altMax, bands }) {
  const cur = withDerived(rawCur);
  const [view, setView] = useState("gauges");
  // Height tape: 0 .. a bit over the highest point; while the step asks for a
  // height band, zoomed to the band (+/- 600 ft) so a 200 ft band is readable.
  const ab = bands?.alt;
  let altMin = 0;
  let altTop = Math.max(2000, Math.ceil((Math.max(altMax ?? 0, ab?.hi ?? 0, ab?.target ?? 0) * 1.1) / 500) * 500);
  if (ab && ab.lo != null && ab.hi != null) {
    altMin = Math.max(0, Math.floor((ab.lo - 600) / 100) * 100);
    altTop = Math.ceil((ab.hi + 600) / 100) * 100;
  }

  return (
    <div className="rev-card ft-card">
      <div className="card-head">
        <h3>Flight telemetry</h3>
        {bands && view === "gauges" && (
          <span className="ft-legend" title="From the lesson step being flown right now">
            <i className="ft-legend__ok" />allowed <i className="ft-legend__tgt" />target <i className="ft-legend__bad" />limit
          </span>
        )}
        <div className="ft-toggle" role="tablist">
          <button type="button" role="tab" aria-selected={view === "gauges"}
            className={view === "gauges" ? "on" : ""} onClick={() => setView("gauges")}>Gauges</button>
          <button type="button" role="tab" aria-selected={view === "numbers"}
            className={view === "numbers" ? "on" : ""} onClick={() => setView("numbers")}>Numbers</button>
        </div>
      </div>
      {!cur ? (
        <div className="scan-empty">No telemetry yet.</div>
      ) : view === "numbers" ? (
        <Numbers cur={cur} />
      ) : (
        <div className="ft-gauges">
          <div className="ft-group">
            <Tape label="SPEED" unit="knots" value={cur.airspeed_kt} min={0} max={SPEED_MAX} band={bands?.speed}
              title="How fast the aircraft is flying, in knots." />
            <Tape label="HEIGHT" unit="feet" value={cur.altitude_ft} min={altMin} max={altTop} band={bands?.alt}
              title="Altitude above sea level, in feet." />
            <Tape label="CLIMB ▲▼" unit="ft/min" value={cur.vertical_speed_fpm} min={-CLIMB_MAX} max={CLIMB_MAX}
              band={bands?.vs}
              title="Climbing (up) or descending (down), in feet per minute. Middle line = level." />
          </div>
          <div className="ft-group ft-group--dials">
            <Attitude pitch={cur.pitch_deg} bank={cur.bank_deg} heading={cur.heading_true_deg} />
            <BankDial bank={cur.bank_deg} band={bands?.bank} />
            <Compass heading={cur.heading_true_deg} />
          </div>
          <div className="ft-group ft-group--controls">
            <VBar label="ENGINE POWER" value={num(cur.throttle_pct) == null ? null : cur.throttle_pct / 100}
              color="var(--ft-good)" text={`${fmt(cur.throttle_pct, 0)}%`}
              title="Engine power (throttle lever)." />
            <StickPad aileron={cur.aileron_position} elevator={cur.elevator_position} />
            <CenterBar label="RUDDER" horizontal value={cur.rudder_position}
              text={(x) => (Math.abs(x) < 0.08 ? "center" : x < 0 ? "left" : "right")}
              title="Rudder pedals. Middle = centred." />
            <VBar label="FLAPS" value={num(cur.flaps_handle_pct) == null ? null : cur.flaps_handle_pct / 100}
              color="var(--ft-warn)" text={`${fmt(cur.flaps_handle_pct, 0)}%`}
              title="Wing flaps, for flying slowly on approach and landing." />
            <CenterBar label="TRIM" value={num(cur.elevator_trim_pct) == null ? null : cur.elevator_trim_pct / 100}
              text={(x) => (Math.abs(x) < 0.05 ? "neutral" : `${x > 0 ? "nose up" : "nose dn"} ${Math.round(Math.abs(x) * 100)}%`)}
              title="Elevator trim wheel. Middle = neutral; up = nose-up trim." />
            <CenterBar label="AILERON TRIM" horizontal
              value={num(cur.aileron_trim_pct) == null ? null : cur.aileron_trim_pct / 100}
              text={(x) => (Math.abs(x) < 0.05 ? "neutral" : `${x < 0 ? "left" : "right"} ${Math.round(Math.abs(x) * 100)}%`)}
              title="Aileron (roll) trim: takes the sideways push off the stick. Middle = neutral. Reads 0 on aircraft without it." />
          </div>
        </div>
      )}
    </div>
  );
}
