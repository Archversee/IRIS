# Phase transition requirements, Lesson 1 (WSAP pattern)

The lesson tracker (`core/phase_detector.py`) advances through these phases in order.
All geometry uses the runway threshold lat/lon in `lessons/lesson01_pattern.yaml` and
`centerline_offset_m` to align the map with the sim.

## Phase order

```
BRIEFING → PRE_TAKEOFF → TAKEOFF_ROLL → CLIMB_OUT → CROSSWIND → DOWNWIND
         → BASE → FINAL → FLARE_TOUCHDOWN → ROLLOUT → DEBRIEF
```

Lesson 1 is **one circuit**: take off, fly the pattern, full-stop landing, debrief.
There is no touch-and-go.

---

## How to leave each phase

### BRIEFING → PRE_TAKEOFF
- **Trigger:** Trainee taps **Start Lesson** (or mock auto-starts after 2 s).
- **You are here:** Parked on runway, engines idle.

### PRE_TAKEOFF → TAKEOFF_ROLL
- **Trigger:** On ground **and** (`IAS > 15 kt` **or** `throttle > 80%`).
- **What to do:** Push the throttle lever fully forward.

### TAKEOFF_ROLL → CLIMB_OUT
- **Trigger:** Airborne (`not on_ground`) **and** `AGL > 50 ft`.
- **Also catches:** Starting already airborne (retry teleport).
- **What to do:** Rotate at 55 kt, climb at 74 kt (Vy).

### CLIMB_OUT → CROSSWIND
- **Trigger (normal):** `AGL > ~270 ft` (90% of 600 ft crosswind height) **and**
  heading has turned **> 30°** toward the pattern side (left for RWY 02).
- **Trigger (fallback):** Displaced **> 250 m** to the pattern side **and**
  heading offset **> 45°** (catches a late or steep turn).
- **What to do:** Climb runway heading until **600 ft AGL**, then turn **left**.
  MAVIS cues at 580 ft: *"You're at six hundred feet. Ease into a gentle left turn now."*

### CLIMB_OUT → DOWNWIND (missed crosswind)
- **Trigger:** On reciprocal heading (~180° from runway) **and** displaced **> 250 m**
  to the pattern side, self-healing if the crosswind turn was skipped.
- **What to do:** Turn left earlier; don't fly so far downwind on runway heading.

### CROSSWIND → DOWNWIND
- **Trigger:** Heading within **30°** of reciprocal (flying parallel, opposite direction).
- **What to do:** Continue the left turn until parallel to the runway, level at **1100 ft MSL**.

### DOWNWIND, flap callout (ABEAM_FLAPS)
- **Trigger:** `along` drops below mid-downwind **and** flaps still up.
  Midpoint is halfway along the long leg (runway end → base-turn corner), not past the threshold.
  ~1600 m with mock/demo geometry (`mock_base_turn_past_abeam_m: 1000`); ~1550 m on MSFS planned circuit (`base_turn_past_abeam_m: 1100`).
- **What to do:** Power back, first notch of flaps (~10°).

### DOWNWIND → BASE
- **Trigger:** Heading is **30–160°** off reciprocal **and** `along < 200 m`
  (abeam or past the threshold going downwind).
- **What to do:** When the touchdown point is ~45° behind you, turn left onto base.

### DOWNWIND / BASE → FINAL
- **Trigger:** Heading within **30°** of runway heading, airborne, `|cross| < 400 m`,
  `along < 0` (approaching from the pattern side).
- **Fallback:** Can enter FINAL from BASE directly when aligned.

### FINAL → FLARE_TOUCHDOWN
- **Trigger:** `AGL < 30 ft`.
- **What to do:** Power idle, eyes to the end of the runway, flare.

### FLARE_TOUCHDOWN → ROLLOUT
- **Trigger:** `on_ground`.

### ROLLOUT → DEBRIEF (full stop)
- **Trigger:** `IAS < 25 kt` on ground, **circuit ≥ total_circuits** (1 for L1).

### ROLLOUT → CLIMB_OUT *(touch-and-go, multi-circuit lessons only)*
- **Trigger:** Airborne again, `AGL > 50 ft`, **circuit < total_circuits**.
- Not used in Lesson 1.

### Any late phase → DEBRIEF (safety net)
- **Trigger:** In FINAL / FLARE / ROLLOUT, on ground, `IAS < 10`, stationary **> 6 s**.

---

## Retry / "try again"

Say e.g. *"let me try the takeoff again"*, *"redo"*, or *"retry"*.

| You say | Teleport target |
|---------|-----------------|
| *(generic)* redo / try again | **PRE_TAKEOFF**, runway start |
| takeoff | **TAKEOFF_ROLL** checkpoint |
| landing / final / approach | **FINAL** checkpoint |
| crosswind / downwind / base / climb | that phase's checkpoint |

**To escape CLIMB_OUT after a bad retry:** turn **left** when AGL ≥ 600 ft, or fly
until the fallback detects you're on downwind heading. Going straight indefinitely
keeps you in CLIMB_OUT.

---

## Map calibration

If the aircraft is centred on the runway in MSFS but offset on the tablet map, adjust
`pattern.centerline_offset_m` in `lessons/lesson01_pattern.yaml`:

- Appears **too far right** on map → **increase** the value (default `18`).
- Appears **too far left** → **decrease** it.
