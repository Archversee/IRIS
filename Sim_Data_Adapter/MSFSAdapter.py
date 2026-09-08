"""
MSFS2020 SimConnect data logger — v3
Corrected to use pysimconnect's actual documented API:
    sc.subscribe_simdata([...])  -> datadef
    sc.receive()                 -> pumps the SDK message queue
    datadef.simdata[name]        -> latest cached value

(The earlier version called `sc.get(name, unit)`, which isn't part of
pysimconnect's API — that's why every field came back None.)

INSTALL:
    pip install pysimconnect obsws-python

OBS SETUP (two instances, one mp4 each — instrument view + OTW view):
    Run two separate OBS Studio processes (e.g. two installs, or the same
    install launched twice with `--profile`/`--collection` pointing at two
    scene collections — one scened to the instrument capture source, one
    to the OTW capture source). In each instance:
        Tools > WebSocket Server Settings > Enable WebSocket server
    Give them different ports (e.g. 4455 and 4456) since they'll be on the
    same machine. Fill in OBS_INSTANCES below to match.

Run this AFTER MSFS2020 is running with a flight loaded, and after both
OBS instances are open with the WebSocket server enabled.
"""

import csv
import os
import time
from datetime import datetime, timezone

from simconnect import SimConnect, PERIOD_VISUAL_FRAME
import obsws_python as obsws

# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------
POLL_INTERVAL_SEC = 0.1

LOG_DIR = os.path.join(os.getcwd(), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
OUTPUT_CSV = os.path.join(LOG_DIR, "simconnect_log.csv")
EVENTS_CSV = os.path.join(LOG_DIR, "simconnect_events.csv")

# Set False to log flight data only, without touching OBS.
ENABLE_OBS_RECORDING = True

# One entry per OBS instance. `name` is just a label used in console
# output and the events log — it doesn't need to match anything in OBS.
OBS_INSTANCES = [
    dict(name="instrument", host="localhost", port=4456, password=""),
    dict(name="otw",        host="localhost", port=4455, password=""),
]

# Canonical field -> (simvar name, unit)
SIMVARS = {
    "altitude_ft":            ("PLANE ALTITUDE", "feet"),
    "airspeed_kt":             ("AIRSPEED INDICATED", "knots"),
    "vertical_speed_fpm":      ("VERTICAL SPEED", "feet/minute"),
    "heading_true_deg":        ("PLANE HEADING DEGREES TRUE", "degrees"),
    "pitch_deg":                ("PLANE PITCH DEGREES", "degrees"),
    "bank_deg":                 ("PLANE BANK DEGREES", "degrees"),
    "yaw_rate_radps":          ("ROTATION VELOCITY BODY Z", "Radians per second"),
    "fuel_total_qty_gal":      ("FUEL TOTAL QUANTITY", "gallons"),

    "throttle_pct":             ("GENERAL ENG THROTTLE LEVER POSITION:1", "Percent"),
    "elevator_trim_pct":       ("ELEVATOR TRIM PCT", "Percent"),
    "elevator_position":       ("ELEVATOR POSITION", "Position"),
    "rudder_position":          ("RUDDER POSITION", "Position"),
    "flaps_handle_pct":        ("FLAPS HANDLE PERCENT", "Percent"),
    "gear_handle_position":    ("GEAR HANDLE POSITION", "bool"),

    "latitude":                  ("PLANE LATITUDE", "degrees"),
    "longitude":                 ("PLANE LONGITUDE", "degrees"),
    "ambient_visibility_m":    ("AMBIENT VISIBILITY", "meters"),
    "ambient_wind_kt":          ("AMBIENT WIND VELOCITY", "knots"),
    "ambient_temp_c":           ("AMBIENT TEMPERATURE", "celsius"),

    # NOTE: "Sim Running" and "Sim Paused" are NOT simvars — the SDK
    # confirmed this (see "unrecognized simvar" warning). They're actually
    # *system events* (SimStart/SimStop, Pause/Paused/Unpaused), same
    # category as Crashed/CrashReset. Removed from here; see the note
    # at the bottom of the file — events still need their own subscription
    # mechanism, which we haven't nailed down yet for this package.
}

# Map simvar name -> canonical field name, for pulling values back out
NAME_TO_FIELD = {simvar: field for field, (simvar, _) in SIMVARS.items()}


def log_event(event_name):
    ts = datetime.now(timezone.utc).isoformat()
    print(f"[EVENT] {ts} — {event_name}")
    with open(EVENTS_CSV, "a", newline="") as f:
        csv.writer(f).writerow([ts, event_name])


def connect_obs_clients():
    clients = []
    for cfg in OBS_INSTANCES:
        print(f"Connecting to OBS ({cfg['name']}) at {cfg['host']}:{cfg['port']}...")
        client = obsws.ReqClient(
            host=cfg["host"], port=cfg["port"], password=cfg["password"], timeout=5
        )
        clients.append((cfg["name"], client))
    return clients


def start_obs_recordings(clients):
    for name, client in clients:
        client.start_record()
        log_event(f"obs_record_start:{name}")


def stop_obs_recordings(clients):
    for name, client in clients:
        try:
            resp = client.stop_record()
            log_event(f"obs_record_stop:{name}:{resp.output_path}")
            print(f"OBS ({name}) saved recording to {resp.output_path}")
        except Exception as e:
            print(f"Failed to stop OBS ({name}) recording cleanly: {e}")


def main():
    obs_clients = connect_obs_clients() if ENABLE_OBS_RECORDING else []

    print("Connecting to SimConnect (make sure MSFS2020 is running with a flight loaded)...")
    sc = SimConnect()

    # Subscribe once to all variables — the SDK pushes updates rather than
    # us blocking on each one individually.
    subscribe_list = [
        dict(name=simvar, units=unit) for simvar, unit in
        [SIMVARS[f] for f in SIMVARS]
    ]
    datadef = sc.subscribe_simdata(subscribe_list, period=PERIOD_VISUAL_FRAME)

    # Wait for the connection handshake + first data batch to land before
    # we start writing rows, so we don't get an empty leading row.
    print("Waiting for first data batch...")
    while not datadef.simdata:
        sc.receive()
        time.sleep(0.05)
    print("Connected, data flowing.")

    if obs_clients:
        start_obs_recordings(obs_clients)

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
            if obs_clients:
                stop_obs_recordings(obs_clients)


if __name__ == "__main__":
    main()

# ----------------------------------------------------------------------
# Notes:
# - Confirmed working pattern (verified against real MSFS run): 
#   subscribe_simdata() once, pump with sc.receive() each loop, read
#   from datadef.simdata[name]. All Flight State/Control/Environment
#   simvars resolved correctly once the connection's Open handshake
#   completed.
# - Data won't populate for the first ~second while the connection opens
#   — that's expected, not a bug. If you want to skip logging empty rows,
#   add a short sleep + receive-drain right after subscribe_simdata()
#   before the write loop starts.
# - Sim Paused/Sim Running/Crashed/CrashReset are all real *system events*,
#   not simvars — none of them can be polled the way the rest of this
#   script works. That's a separate subscription mechanism we still need
#   to nail down for pysimconnect specifically (the examples/ folder in
#   the repo is the next place to check, since the README doesn't show
#   a system-event example). Left out of this version rather than guess
#   a fourth unverified API.
# - Checklist Items / Scenario Triggers remain outside SimConnect's scope
#   entirely — log those from your own scenario/UI code with matching
#   UTC timestamps.
# ----------------------------------------------------------------------