from SimConnect import SimConnect, AircraftRequests
import time

sm = SimConnect()
aq = AircraftRequests(sm, _time=200)  # cache values for 200ms

while True:
    altitude = aq.get("PLANE_ALTITUDE")
    airspeed = aq.get("AIRSPEED_INDICATED")
    heading = aq.get("PLANE_HEADING_DEGREES_TRUE")

    print(altitude, airspeed, heading)
    time.sleep(0.1)