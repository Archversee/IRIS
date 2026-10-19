import { useEffect, useState } from "react";
import { sopApi } from "./sopApi.js";

// Which flights MAVIS checks against the rules live (MAVIS sop/check_settings.py).
// Circuit / lesson flights can be switched off here; Free Flight is always checked.
// Takes effect from the next flight that starts.
export default function LiveChecksToggle() {
  const [on, setOn] = useState(null);       // null = not loaded yet
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    sopApi.getCheckSettings().then((s) => setOn(!!s.circuit_flights)).catch((e) => setErr(e.message));
  }, []);

  async function flip() {
    setBusy(true); setErr("");
    try {
      const s = await sopApi.putCheckSettings({ circuit_flights: !on });
      setOn(!!s.circuit_flights);
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="sop-livechecks" aria-label="Live rule checks">
      <div className="sop-livechecks__row">
        <b>Live rule checks</b>
        <label className="sop-livechecks__item">
          <span>Circuit flights</span>
          <button type="button" role="switch" aria-checked={!!on} disabled={on === null || busy}
            className={"sop-switch" + (on ? " is-on" : "")} onClick={flip}
            title={on ? "Click to stop checking circuit / lesson flights" : "Click to check circuit / lesson flights"}>
            <span className="sop-switch__knob" />
          </button>
          <span className={on ? "sop-livechecks__on" : "sop-livechecks__off"}>
            {on === null ? "…" : on ? "On" : "Off"}
          </span>
        </label>
        <span className="sop-livechecks__item">
          <span>Free Flight</span>
          <span className="sop-livechecks__on" title="Free Flight is always checked against the rules">Always on</span>
        </span>
      </div>
      <div className="sop-livechecks__help">
        {on === false
          ? "Circuit and lesson flights are not checked live: no rule callouts, and no results until an instructor presses Check now on Review."
          : "Every flight is checked live against the active rule sets."}
        {" "}Changes apply from the next flight that starts.
        {err && <span className="sop-error-inline"> {err}</span>}
      </div>
    </section>
  );
}
