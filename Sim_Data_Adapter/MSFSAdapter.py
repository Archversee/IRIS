"""
MSFS2020 SimConnect data logger
Collects the canonical schema fields (Flight State, Control Inputs, Environment)
and writes them to CSV on a fixed interval, plus a stub for event-based logging
(system failures / checklist / scenario triggers) using SimConnect's system events.

Requires:
    pip install SimConnect
    pip install pysimconnect

Run this AFTER MSFS2020 is running with a flight loaded.
"""

import csv
import time
from datetime import datetime, timezone
 
from simconnect import SimConnect
 
# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------
POLL_INTERVAL_SEC = 0.1
 
# Output folder:
LOG_DIR = os.path.join(os.getcwd(), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
 
OUTPUT_CSV = os.path.join(LOG_DIR, "simconnect_log.csv")
EVENTS_CSV = os.path.join(LOG_DIR, "simconnect_events.csv")
 
# Canonical field -> SimConnect simvar name, unit
SIMVARS = {
    # --- Flight State ---
    "altitude_ft":            ("PLANE ALTITUDE", "feet"),
    "airspeed_kt":             ("AIRSPEED INDICATED", "knots"),
    "vertical_speed_fpm":      ("VERTICAL SPEED", "feet/minute"),
    "heading_true_deg":        ("PLANE HEADING DEGREES TRUE", "degrees"),
    "pitch_deg":                ("PLANE PITCH DEGREES", "degrees"),
    "bank_deg":                 ("PLANE BANK DEGREES", "degrees"),
    "yaw_rate_radps":          ("ROTATION VELOCITY BODY Z", "radians per second"),
    "fuel_total_qty_gal":      ("FUEL TOTAL QUANTITY", "gallons"),
 
    # --- Control Inputs ---
    "throttle_pct":             ("GENERAL ENG THROTTLE LEVER POSITION:1", "percent"),
    "elevator_trim_pct":       ("ELEVATOR TRIM PCT", "percent"),
    "elevator_position":       ("ELEVATOR POSITION", "position"),
    "rudder_position":          ("RUDDER POSITION", "position"),
    "flaps_handle_pct":        ("FLAPS HANDLE PERCENT", "percent"),
    "gear_handle_position":    ("GEAR HANDLE POSITION", "bool"),
 
    # --- Environment ---
    "latitude":                  ("PLANE LATITUDE", "degrees"),
    "longitude":                 ("PLANE LONGITUDE", "degrees"),
    "ambient_visibility_m":    ("AMBIENT VISIBILITY", "meters"),
    "ambient_wind_kt":          ("AMBIENT WIND VELOCITY", "knots"),
    "ambient_temp_c":           ("AMBIENT TEMPERATURE", "celsius"),
 
    # --- Events, polled as simvars
    "sim_running":              ("SIM RUNNING", "bool"),
    "sim_paused":                ("SIM PAUSED", "bool"),
}
 
# True system events need real event subscription, not polling.
SYSTEM_EVENTS = ["Crashed", "CrashReset"]
 
 
def read_row(sc):
    row = {"timestamp_utc": datetime.now(timezone.utc).isoformat()}
    for field, (simvar, unit) in SIMVARS.items():
        try:
            row[field] = sc.get(simvar, unit)
        except Exception:
            row[field] = None
    return row
 
 
def log_event(event_name):
    ts = datetime.now(timezone.utc).isoformat()
    print(f"[EVENT] {ts} — {event_name}")
    with open(EVENTS_CSV, "a", newline="") as f:
        csv.writer(f).writerow([ts, event_name])
 
 
def main():
    print("Connecting to SimConnect (make sure MSFS2020 is running with a flight loaded)...")
 
    with SimConnect() as sc:
        # Subscribe to the real system events (crash detection)
        for evt_name in SYSTEM_EVENTS:
            sc.subscribe(evt_name, lambda data, name=evt_name: log_event(name))
 
        fieldnames = ["timestamp_utc"] + list(SIMVARS.keys())
 
        with open(OUTPUT_CSV, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
 
            print(f"Logging to {OUTPUT_CSV} at {1 / POLL_INTERVAL_SEC:.0f} Hz. Ctrl+C to stop.")
            try:
                while True:
                    row = read_row(sc)
                    writer.writerow(row)
                    f.flush()
                    time.sleep(POLL_INTERVAL_SEC)
            except KeyboardInterrupt:
                print("\nStopped logging.")
 
 
if __name__ == "__main__":
    main()