import { fmt } from "./utils.js";

function RO({ k, v }) {
  return <div className="ro-row"><span className="k">{k}</span><span className="v">{v}</span></div>;
}

// Current-instant flight telemetry readout, split into flight state and
// control input columns.
export default function LiveStateCard({ cur }) {
  return (
    <div className="rev-card">
      <h3>Live state</h3>
      <div className="readout-cols">
        <div className="ro-group">
          <h4>Flight state</h4>
          <RO k="Heading" v={`${fmt(cur.heading_true_deg, 0)}°`} />
          <RO k="Pitch" v={`${fmt(cur.pitch_deg)}°`} />
          <RO k="Bank" v={`${fmt(cur.bank_deg)}°`} />
          <RO k="Yaw rate" v={fmt(cur.yaw_rate_radps, 3)} />
          <RO k="Fuel" v={`${fmt(cur.fuel_total_qty_gal)} gal`} />
          <RO k="Lat" v={fmt(cur.latitude, 4)} />
          <RO k="Long" v={fmt(cur.longitude, 4)} />
          <RO k="Wind" v={`${fmt(cur.ambient_wind_kt)} kt`} />
        </div>
        <div className="ro-group">
          <h4>Control input</h4>
          <RO k="Throttle" v={`${fmt(cur.throttle_pct, 0)}%`} />
          <RO k="Elev trim" v={`${fmt(cur.elevator_trim_pct)}%`} />
          <RO k="Elevator" v={fmt(cur.elevator_position, 2)} />
          <RO k="Rudder" v={fmt(cur.rudder_position, 2)} />
          <RO k="Flaps" v={`${fmt(cur.flaps_handle_pct, 0)}%`} />
          <RO k="Gear" v={cur.gear_handle_position ? "DOWN" : "UP"} />
          <RO k="Altitude" v={`${fmt(cur.altitude_ft, 0)} ft`} />
          <RO k="Airspeed" v={`${fmt(cur.airspeed_kt)} kt`} />
        </div>
      </div>
    </div>
  );
}
