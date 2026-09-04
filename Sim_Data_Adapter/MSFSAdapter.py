"""
MSFS2020 SimConnect data logger — v3
Corrected to use pysimconnect's actual documented API:
    sc.subscribe_simdata([...])  -> datadef
    sc.receive()                 -> pumps the SDK message queue
    datadef.simdata[name]        -> latest cached value

(The earlier version called `sc.get(name, unit)`, which isn't part of
pysimconnect's API — that's why every field came back None.)

INSTALL:
    pip install pysimconnect

Run this AFTER MSFS2020 is running with a flight loaded.
"""

import csv
import os
import time
from datetime import datetime, timezone

from simconnect import SimConnect, PERIOD_VISUAL_FRAME

# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------
POLL_INTERVAL_SEC = 0.1

LOG_DIR = os.path.join(os.getcwd(), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
OUTPUT_CSV = os.path.join(LOG_DIR, "simconnect_log.csv")
EVENTS_CSV = os.path.join(LOG_DIR, "simconnect_events.csv")

# Canonical field -> (simvar name, unit)
SIMVARS = {
    "altitude_ft":            ("PLANE ALTITUDE", "feet"),
    "airspeed_kt":             ("AIRSPEED INDICATED", "knots"),
    "vertical_speed_fpm":      ("VERTICAL SPEED", "feet/minute"),
    "heading_true_deg":        ("PLANE HEADING DEGREES TRUE", "degrees"),
    "pitch_deg":                ("PLANE PITCH DEGREES", "degrees"),
    "bank_deg":                 ("PLANE BANK DEGREES", "degrees"),
    "yaw_rate_radps":          ("ROTATION VELOCITY BODY Z", "radians per second"),
    "fuel_total_qty_gal":      ("FUEL TOTAL QUANTITY", "gallons"),

    "throttle_pct":             ("GENERAL ENG THROTTLE LEVER POSITION:1", "percent"),
    "elevator_trim_pct":       ("ELEVATOR TRIM PCT", "percent"),
    "elevator_position":       ("ELEVATOR POSITION", "position"),
    "rudder_position":          ("RUDDER POSITION", "position"),
    "flaps_handle_pct":        ("FLAPS HANDLE PERCENT", "percent"),
    "gear_handle_position":    ("GEAR HANDLE POSITION", "bool"),

    "latitude":                  ("PLANE LATITUDE", "degrees"),
    "longitude":                 ("PLANE LONGITUDE", "degrees"),
    "ambient_visibility_m":    ("AMBIENT VISIBILITY", "meters"),
    "ambient_wind_kt":          ("AMBIENT WIND VELOCITY", "knots"),
    "ambient_temp_c":           ("AMBIENT TEMPERATURE", "celsius"),

    "sim_running":              ("SIM RUNNING", "bool"),
    "sim_paused":                ("SIM PAUSED", "bool"),
}

# Map simvar name -> canonical field name, for pulling values back out
NAME_TO_FIELD = {simvar: field for field, (simvar, _) in SIMVARS.items()}


def log_event(event_name):
    ts = datetime.now(timezone.utc).isoformat()
    print(f"[EVENT] {ts} — {event_name}")
    with open(EVENTS_CSV, "a", newline="") as f:
        csv.writer(f).writerow([ts, event_name])


def main():
    print("Connecting to SimConnect (make sure MSFS2020 is running with a flight loaded)...")
    sc = SimConnect()

    # Subscribe once to all variables — the SDK pushes updates rather than
    # us blocking on each one individually.
    subscribe_list = [
        dict(name=simvar, units=unit) for simvar, unit in
        [SIMVARS[f] for f in SIMVARS]
    ]
    datadef = sc.subscribe_simdata(subscribe_list, period=PERIOD_VISUAL_FRAME)

    # NOTE: Crashed/CrashReset event subscription is NOT included here yet.
    # I have not been able to confirm the exact pysimconnect call for
    # receiving system events (as opposed to simulator variables) from
    # documentation alone. Let's get variable logging verified working
    # first, then tackle this as a follow-up — happy to dig into the
    # examples/ folder in the repo with you for the exact call once this
    # part is confirmed working.

    fieldnames = ["timestamp_utc"] + list(SIMVARS.keys())

    with open(OUTPUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        print(f"Logging to {OUTPUT_CSV}")
        print(f"Rate: {1 / POLL_INTERVAL_SEC:.0f} Hz. Ctrl+C to stop.")
        try:
            while True:
                # Pump the SDK's message queue so datadef.simdata refreshes
                while sc.receive():
                    pass

                row = {"timestamp_utc": datetime.now(timezone.utc).isoformat()}
                for field, (simvar, _unit) in SIMVARS.items():
                    try:
                        row[field] = datadef.simdata[simvar]
                    except KeyError as e:
                        print(f"FAILED: {field} ({simvar}) -> {e}")
                        row[field] = None

                writer.writerow(row)
                f.flush()
                time.sleep(POLL_INTERVAL_SEC)
        except KeyboardInterrupt:
            print("\nStopped logging.")
        finally:
            sc.Close()


if __name__ == "__main__":
    main()

# ----------------------------------------------------------------------
# Notes:
# - This version follows pysimconnect's documented pattern from its own
#   PyPI quick-start: subscribe_simdata() once, pump with sc.receive()
#   each loop, read from datadef.simdata[name].
# - If any field prints FAILED with a KeyError, that variable name likely
#   isn't recognized by the SDK/wrapper as written (check exact spelling
#   against the SDK's Simulation Variables reference) — paste me the
#   failing names and I'll fix them.
# - Crashed/CrashReset event logging is deliberately left out until we
#   verify the correct call — logging fabricated code that might silently
#   no-op again isn't worth it. We'll add it once flight-state logging
#   is confirmed working end-to-end.
# ----------------------------------------------------------------------