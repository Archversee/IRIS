import { useState } from "react";
import { fmt } from "./utils.js";

// Live flight telemetry for Live Monitor, drawn the way MAVIS's own in-flight
// panel does it (vertical speed/height/climb tapes, stick-style control bars)
// plus an attitude indicator, with a toggle to the raw numbers.
//
// Units (flight_data, see db/schema.sql + backend/app/mavis_bridge.py):
// aileron/elevator/rudder are SimConnect "Position" (-1..1; + elevator =
// stick back / nose up, - aileron = left), throttle/flaps are 0..100 %,
// elevator trim is -100..100 %. Anything a source doesn't log comes through null.

const SPEED_MAX = 163; // C172 Vne, same scale MAVIS uses
const CLIMB_MAX = 1200;

const clamp01 = (v) => Math.max(0, Math.min(1, v));
const num = (v) => (v == null || Number.isNaN(+v) ? null : +v);

function Tape({ label, unit, value, min, max, title }) {
  const v = num(value);
  const pct = clamp01(((v ?? min) - min) / (max - min)) * 100;
  const zeroPct = min < 0 ? clamp01((0 - min) / (max - min)) * 100 : null;
  return (
    <div className="ft-tape" title={title}>
      <div className="ft-tape__label">{label}<small>{unit}</small></div>
      <div className="ft-tape__bar">
        <b className="ft-tape__mx">{max}</b>
        {zeroPct != null && <div className="ft-tape__zero" style={{ bottom: `${zeroPct}%` }} />}
        <div className="ft-tape__ptr" style={{ bottom: `${pct}%` }} />
        <b className="ft-tape__mn">{min}</b>
      </div>
      <div className="ft-tape__val">{v == null ? "--" : Math.round(v)}</div>
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
        <RO k="Flaps" v={`${fmt(cur.flaps_handle_pct, 0)}%`} />
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

export default function FlightTelemetryCard({ cur, altMax }) {
  const [view, setView] = useState("gauges");
  const altTop = Math.max(2000, Math.ceil(((altMax ?? 0) * 1.1) / 500) * 500);

  return (
    <div className="rev-card ft-card">
      <div className="card-head">
        <h3>Flight telemetry</h3>
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
            <Tape label="SPEED" unit="knots" value={cur.airspeed_kt} min={0} max={SPEED_MAX}
              title="How fast the aircraft is flying, in knots." />
            <Tape label="HEIGHT" unit="feet" value={cur.altitude_ft} min={0} max={altTop}
              title="Altitude above sea level, in feet." />
            <Tape label="CLIMB ▲▼" unit="ft/min" value={cur.vertical_speed_fpm} min={-CLIMB_MAX} max={CLIMB_MAX}
              title="Climbing (up) or descending (down), in feet per minute. Middle line = level." />
          </div>
          <div className="ft-group">
            <Attitude pitch={cur.pitch_deg} bank={cur.bank_deg} heading={cur.heading_true_deg} />
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
          </div>
        </div>
      )}
    </div>
  );
}
