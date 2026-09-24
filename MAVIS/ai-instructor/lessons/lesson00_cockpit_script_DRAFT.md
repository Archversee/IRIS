# Cockpit Familiarisation: spoken checklist draft

Working draft for `lesson00_cockpit` (catalog stub today: `available: false`, no `kind`,
no content module). Companion to `lesson00_preflight_script_DRAFT.md`, same convention:
this becomes `lessons/lesson00_cockpit.py`: plain Python, same shape as
`lesson00_preflight.py` (`TITLE` + `CHECKPOINTS` + `as_state()`/`find()`/`speech_text()`).

## Decisions

- **Narration only.** No per-checkpoint verification question, matching
  `lesson00_preflight` exactly. If this changes, it changes for both reading lessons
  together, not one at a time.
- **Fixed pilot's-eye camera per checkpoint**, unlike the walk-around's free orbit.
  Clicking a checkpoint snaps the camera to a preset position looking at that item,
  rather than letting the trainee orbit into an interior wall or through the seat.
  Each checkpoint therefore needs a **camera position + look-at target** in addition to
  its hotspot position, both calibrated by eye once the model is in hand, the same way
  `PF3D_POS` was calibrated against `cessna172.glb`.
- **Model assumption:** a single `.glb` on the same `THREE.GLTFLoader` path as the
  exterior model. If the file instead exposes named sub-meshes per control, hotspots can
  attach directly to those parts instead of hand-placed `[x,y,z]` coordinates. That is a
  different code path, so check the file before writing the viewer.

## Source material: panel mismatch, read before treating this as authoritative

The supplied reference (Aerofly's C172 cockpit tutorial,
`aerofly.com/aircraft-tutorials/c172_cockpit/`) documents the **analog six-pack**
(Airspeed Indicator, Attitude Indicator, Altimeter, Turn Indicator, Heading Indicator,
VSI) plus a separate NAV1/NAV2/DME/ADF/transponder stack and a King-style autopilot.
**This project's aircraft is the G1000 glass panel** (`profiles/aircraft/c172sp_g1000.yaml`),
which has none of those as separate instruments; they are synthesized into the PFD and
MFD, with a GFC 700 autopilot.

- **Checkpoints 1–4 and 7–10** are panel-agnostic systems taken faithfully from Aerofly,
  cross-checked against the cockpit notes already in the root `CLAUDE.md`.
- **Checkpoints 5–6 (PFD/MFD)** are *not* from Aerofly, which covers the wrong panel.
  They are written from general G1000 knowledge and **must be verified against the actual
  simulator panel or G1000 NXi documentation before shipping**, the same class of caveat
  the walk-around content carries for its POH figures.
- `flaps.control_style: switch` in the aircraft profile is a HOTAS phrasing abstraction
  used for in-flight coaching; the real cockpit has a **lever with detents**. Checkpoint 9
  describes the lever, because that is what is physically in the cockpit.

Order below is by system (left-to-right, top-to-bottom). Re-check it against a real seated
flow once the model is in hand. The walk-around follows a physical loop around the
aircraft, and this lesson should follow an equivalent natural order in the cockpit.

## 1. Getting settled

> "Let's get familiar with the cockpit before we go anywhere. Get yourself seated and
> comfortable. Adjust the seat and rudder pedals until you can reach everything with your
> arms slightly bent and your feet flat on the pedals."

- Seat position and rudder pedal adjustment.
- Seatbelt and shoulder harness secure.
- Yoke steers in the air (pitch and roll); rudder pedals steer on the ground and keep the
  nose straight in the air.

## 2. Power quadrant: throttle and mixture

> "In the middle of the panel, two levers control the engine. The black one is the
> throttle. Push it in for more power, pull it out for less. The red one is the
> mixture. Push it in to richen the fuel mix, pull it out to lean it."

- Throttle (black): engine power.
- Mixture (red): fuel/air ratio; full rich for take-off and landing at low altitude.

## 3. Starting the engine

> "Down on the lower left of the panel is the ignition switch: Off, Right, Left, Both,
> and Start, same as a car key with an extra notch. Next to it, the master switches:
> battery gives the aircraft electrical power, alternator keeps the battery charged once
> the engine's running."

- Magneto/ignition switch: OFF / R / L / BOTH / START.
- Master battery switch: electrical power to essential systems.
- Master alternator switch: engine-driven charging once running.

## 4. Fuel system

> "Down by your feet, or on the lower console, the fuel selector picks which tank feeds
> the engine: Left, Both, or Right. There's also an electric fuel pump switch on the
> panel, and a shut-off valve for emergencies."

- Fuel selector: LEFT / BOTH / RIGHT (BOTH normally for take-off and landing).
- Electric fuel pump switch.
- Fuel shut-off valve (pull to stop fuel delivery).

## 5. The G1000 Primary Flight Display (PFD)

> "This big screen in front of you is the Primary Flight Display, and everything you need to
> fly is on it. Airspeed on a tape down the left side, the artificial horizon in the
> middle showing your pitch and bank, altitude on a tape down the right, your climb or
> descent rate next to that, and your heading on a compass rose along the bottom."

- Airspeed tape (left): replaces the analog ASI.
- Attitude indicator (centre): pitch and bank.
- Altitude tape (right): replaces the analog altimeter, with a barometric setting.
- Vertical speed indicator (right, beside altitude).
- Heading indicator / HSI (bottom), with a heading bug.
- **Verify field layout against the actual G1000 NXi panel before shipping.**

## 6. The G1000 Multi-Function Display (MFD)

> "The second screen, usually to the right, is the Multi-Function Display. It shows a
> moving map of where you are, engine and fuel information down the left side, and the
> radio frequencies along the top."

- Moving map.
- Engine/fuel instrument strip (RPM, oil temperature and pressure, fuel quantity).
- Integrated COM/NAV frequency boxes and transponder code, replacing the separate radio
  stack found on a steam-gauge aircraft.
- **Verify field layout against the actual G1000 NXi panel before shipping.**

## 7. Avionics, electrical, and circuit breakers

> "Behind the yoke, the avionics master switch turns the radios and displays on and off
> separately from the battery, which is useful so you're not powering sensitive electronics
> during engine start. Pitot heat prevents ice blocking your airspeed reading. And the
> row of circuit breakers is your electrical fuse box: pushed in and normal, popped out
> means something tripped."

- Avionics master switch(es).
- Pitot heat switch.
- Circuit breaker panel: pushed in is normal.

## 8. Exterior lighting

> "Five switches control the lights: beacon is that red rotating light warning the prop's
> about to turn, nav lights are red left, green right, white tail, and always on at night,
> strobes are the bright flashes for visibility, and landing and taxi lights light your
> way on the ground and on approach."

- Beacon (BCN).
- Navigation lights: red left, green right, white tail.
- Strobe lights.
- Landing light.
- Taxi light.

## 9. Flaps and trim

> "Flaps are that lever, with four positions: up, ten, twenty, and thirty degrees;
> they help you fly slower and land shorter. Below the panel, the trim wheel takes the
> pressure off the yoke so you're not fighting it to hold your pitch."

- Flap lever: 0° / 10° / 20° / 30° detents.
- Pitch trim wheel.

## 10. Parking brake and final check

> "Last thing. The parking brake, usually a small lever under the panel between your
> knees. Set before shutdown, released before you move. Take one more look across the
> whole panel. If anything doesn't make sense yet, that's completely normal, we'll use
> all of this together as we fly."

- Parking brake.
- Doors secure, seatbelts on.
- Closing "anything unclear, ask" line, matching the walk-around's final checkpoint tone.

---

## Before this becomes `lesson00_cockpit.py`

1. Generalise the reading-lesson plumbing from single-lesson to lesson-id-keyed.
   `index.html:1583` dispatches on `les.kind==="reading"`, not on lesson id, so *any*
   reading lesson currently opens the walk-around's content and model. Affects
   `openPreflightChecklist()`, `PF3D_POS`, `ensurePf3D()`, and the `preflight_*` names in
   `main.py`. Rename-and-parameterise, not a redesign.
2. Verify the G1000 PFD/MFD content in checkpoints 5–6 against the real panel.
3. Write the content module, with the verification caveat in its docstring (as
   `lesson00_preflight.py` does).
4. Once the `.glb` exists: calibrate a hotspot position **and** a camera position/target
   per checkpoint; confirm whether the model's sub-meshes are named.
5. Confirm checkpoint order against a real seated cockpit flow.
6. Add `"kind": "reading"` and set `"available": true` on `lesson00_cockpit` in
   `catalog.json`.
