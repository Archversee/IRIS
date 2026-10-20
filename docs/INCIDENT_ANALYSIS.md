# Incident analysis: the 30 s around a broken rule

After a flight, Review's **Incidents** button lists every time an SOP rule was broken, in order, with what the analysis found. Click one: the videos and timeline jump to a few seconds before it and the 30 seconds before and after open, built from the flight data, the eye tracker, the AOI zones and MAVIS's callouts. (The **Analyse** button next to each breach in the SOP drawer does the same.)

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
| `backend/app/routers/incidents.py` | `GET /sessions/{id}/incidents` (the list, findings cached in `incident_findings`), `GET /rule-results/{id}/incidents/{n}`, `POST …/explain` (instructors), `PATCH /incident-explanations/{id}` (rating) |
| `backend/app/incident_explain.py` | Prompt, the instructor-editable guide (`KNOWLEDGE`), number check, Ollama call, `incident_explanations` table |
| `backend/app/aoi_roles.py` | AOI zone name → instrument ("Airspeed", "ASI", "IAS" → airspeed) and which instruments show each value |
| `backend/app/eye_clock.py` | `POST /sessions/{id}/eye-clock`, `sessions.eye_clock_*` |
| `Sim_Data_Adapter/LiveAdapter.py` | `ClockSync`: measures and corrects the Smart Eye clock |
| `frontend/src/components/session/IncidentsPanel.jsx` | Review → Incidents: the list |
| `frontend/src/components/session/IncidentView.jsx` | The ±30 s view |

## What it needs

**AOI zones named for the instruments.** Draw and name zones in AOI Zones as before. The zone table now shows which instrument each name is taken to be (`Airspeed → Airspeed indicator`) or *not recognised*. Recognised names include Airspeed/ASI/IAS, Altimeter/ALT, VSI/Vertical speed, Attitude/AI/Horizon, Heading/HSI/DG, Turn coordinator/TC, Engine/RPM/Tach. If a rule's instrument has no zone, the analysis says so instead of guessing.

**Eye and flight clocks in step.** The adapter measures how far the Smart Eye PC's clock is from this PC's: each packet's arrival time minus Smart Eye's own timestamp, a few ms on a LAN. It re-stamps the eye samples onto this PC's clock (`CORRECT_EYE_CLOCK = True`) and reports the offset to IRIS every 30 s. The analysis shows the clock state and warns when it wasn't measured. This assumes the adapter runs on the same PC as MAVIS (as `launch_all.ps1` does). Turning on *Set time automatically* on both PCs still fixes it at the source.

**A model for the debrief notes.** Set in MAVIS's `config.yaml` under `debrief:` (IRIS reads it; no restart needed after editing). On the PC that runs Ollama:

```
ollama pull qwen3:14b
```

```yaml
debrief:
  ollama_url: http://localhost:11434     # or the laptop: http://192.168.0.107:11434
  model: qwen3:14b                       # 24 GB+ GPU: qwen3:32b · 8 GB GPU: qwen3:8b · or the SOP model
  think: true
  auto_on_save: true                     # write the summary + every note when a session is saved
  max_auto_notes: 15
  window_before_s: 30                    # default close-up window
  window_after_s: 30
```

`EXPLAIN_OLLAMA_URL` / `EXPLAIN_MODEL` / `EXPLAIN_TIMEOUT_S` in `backend/.env` still override it. With no `debrief:` block, MAVIS's SOP model (`sop:`) is used.

## Window

The close-up shows 30 s before the breach and 30 s after the recovery by default (`debrief.window_before_s` / `window_after_s`). Each view can type its own, e.g. 15 before and 20 after (or "-15" / "+20"), 10-120 s each side; the choice is remembered for the next incident. The findings are worked out again for that window. A note says which window it was written for.

## Written automatically

Saving a session (Save → Next) starts a background job on the backend: first the whole-flight summary (Review → **Debrief**), then a note for each broken-rule moment that has none (up to `max_auto_notes`), one model call at a time. It waits up to 2 minutes for MAVIS's rule check. Review → Debrief shows the progress; instructors can rewrite the summary, write any missing notes, and rate both. A backend restart forgets a running job (what was already written is kept; press the button again).

The **Debrief** summary has four parts (overview, what went well, what to work on, next flight), written only from the flight's measured facts (rules, each broken-rule moment and its findings, scan and handling per phase, final approach, touchdown, MAVIS's callouts, the pilot's previous flight). Its numbers are checked the same way. `backend/app/flight_summary.py` holds its prompt and facts; `backend/app/routers/debrief.py` the job and API.

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
