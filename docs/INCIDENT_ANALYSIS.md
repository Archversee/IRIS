# Incident analysis: the 30 s around a broken rule

After a flight, every time an SOP rule was broken (Review → SOP drawer) has an **Analyse** button. It opens the 30 seconds before and after that moment, built from the flight data, the eye tracker, the AOI zones and MAVIS's callouts:

- **The value against its limits**, marked with: when it started heading for the limit, when the limit was crossed, the first control input, the return within limits, and MAVIS's callouts.
- **Where the pilot was looking**: one coloured bar per look, by AOI zone.
- **Controls**: throttle, stick and rudder.
- **Findings** in plain words, each backed by numbers, e.g. *"Didn't look at the airspeed indicator for 14 s before the limit was exceeded"*, *"First correcting input (throttle) came 4.0 s after…"*, *"Over-corrected"*, *"Built up during a turn"*.
- **Watch from the build-up / Watch the breach**: moves the videos and timeline to that moment.
- **Debrief note** (instructors: *Explain with AI*): a short note for the student written by a local reasoning model from the findings only. Every number in it is checked against the measured facts; any it can't back up is flagged. Instructors mark each note helpful or not; those ratings are kept.

It runs after the flight only. Nothing here runs live.

## Pieces

| File | What it does |
|---|---|
| `backend/app/incidents.py` | The analysis. Pure: data in, facts and findings out |
| `backend/app/incidents_selftest.py` | `python -m app.incidents_selftest` (from `backend/`): made-up flights, checks the story each one tells |
| `backend/app/routers/incidents.py` | `GET /rule-results/{id}/incidents/{n}`, `POST …/explain` (instructors), `PATCH /incident-explanations/{id}` (rating) |
| `backend/app/incident_explain.py` | Prompt, the instructor-editable guide (`KNOWLEDGE`), number check, Ollama call, `incident_explanations` table |
| `backend/app/aoi_roles.py` | AOI zone name → instrument ("Airspeed", "ASI", "IAS" → airspeed) and which instruments show each value |
| `backend/app/eye_clock.py` | `POST /sessions/{id}/eye-clock`, `sessions.eye_clock_*` |
| `Sim_Data_Adapter/LiveAdapter.py` | `ClockSync`: measures and corrects the Smart Eye clock |
| `frontend/src/components/session/IncidentView.jsx` | The view |

## What it needs

**AOI zones named for the instruments.** Draw and name zones in AOI Zones as before. The zone table now shows which instrument each name is taken to be (`Airspeed → Airspeed indicator`) or *not recognised*. Recognised names include Airspeed/ASI/IAS, Altimeter/ALT, VSI/Vertical speed, Attitude/AI/Horizon, Heading/HSI/DG, Turn coordinator/TC, Engine/RPM/Tach. If a rule's instrument has no zone, the analysis says so instead of guessing.

**Eye and flight clocks in step.** The adapter measures how far the Smart Eye PC's clock is from this PC's: each packet's arrival time minus Smart Eye's own timestamp, a few ms on a LAN. It re-stamps the eye samples onto this PC's clock (`CORRECT_EYE_CLOCK = True`) and reports the offset to IRIS every 30 s. The analysis shows the clock state and warns when it wasn't measured. This assumes the adapter runs on the same PC as MAVIS (as `launch_all.ps1` does). Turning on *Set time automatically* on both PCs still fixes it at the source.

**A model for the debrief note.** By default, MAVIS's own SOP model from `MAVIS/ai-instructor/config.yaml` (`sop.ollama_url`, `sop.model`). A reasoning model (e.g. Qwen3) is used with thinking on; others work without it. Override in `backend/.env`:

```
EXPLAIN_OLLAMA_URL=http://192.168.0.107:11434
EXPLAIN_MODEL=qwen3.6:27b
EXPLAIN_TIMEOUT_S=240
```

## How the findings are worked out

| Finding | Rule |
|---|---|
| Build-up | Walking back from the breach while the value was still moving toward the limit; stops at a move the other way or 1.5 s steady |
| Not monitored | No look at the rule's instrument in the 10 s (or more) before the breach |
| Late detection | First look at it came after the breach |
| Fixation | One unbroken look ≥ 5 s at something unrelated in the 30 s before |
| Slow action | First real control input ≥ 2 s after first looking (or after the breach) |
| Prompted | The correction came after a MAVIS callout |
| Over-correction | Went past the opposite limit after recovering, or broke the rule again within 30 s |
| High workload | Pupil ≥ 15 % above this pilot's session median (a hint only: brightness changes pupils too) |
| Busy | A turn (bank ≥ 15°), flap change or phase change in the 10 s before the build-up |
| Good recovery | Back within limits in ≤ 3 s, no over-correction |

The thresholds are constants at the top of `incidents.py`.
