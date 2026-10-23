# Progress: how trainees get on across flights

**Progress** (IRIS sidebar) sums up many flights; one flight is looked at in Review. Everything is worked out after the flights; nothing runs live. A trainee sees only their own page (their account's name or username must match the pilot name on their flights). Instructors and admins pick any trainee and also get the **Instructor dashboard**.

## Trainee view

| Section | What it shows | Where it comes from |
|---|---|---|
| What stands out | Plain-words notes: rules still broken, improved or slipping back, forgetting between lessons, the main cause, scan / handling clearly behind the expert flights, how well callouts work | all below |
| Learning curves | Times each rule was broken, flight by flight; longest run broken in a row; met then broken again after a break of 7+ days ("Forgot"). Gaps of 7+ days are marked over the flight | SOP rule checks (instructor verdicts count) |
| Why rules were broken | Causes per flight: not seeing it, acting late, over-correcting, workload | the ±30 s analysis of each broken rule |
| Scan pattern | Per phase: % of time looking outside, how often airspeed and altimeter were checked, how spread out attention was. Last 3 flights against the expert flights; a chart per phase over all flights | eye tracker + AOI zone names |
| Does the coaching work? | Per MAVIS callout: times given, responded (a control input within 10 s), typical response time, fixed it (the value moved the right way within 15 s), ignored | MAVIS callouts + flight data |
| Handling | Per phase: how steadily speed, height (downwind) and sink rate were held, control reversals a minute; final approach speed spread and touchdown sink rate per flight, and how much landings vary | flight data |

## Instructor dashboard

- **Pilots side by side**: each trainee's last 5 checked flights: rules broken per flight and the trend, main cause, airspeed scan on final, time outside, final approach speed spread, touchdown sink rate, share of callouts that fixed the problem.
- **Rules broken most often** and **which handbook rules matter most in practice**: ranked by how often they're broken, their severity, and the knock-on (how often breaking it is followed by breaking another rule within a minute).
- **Which callouts help, and which are ignored**, over everyone.
- **Most common causes.**
- **Expert reference flights**: tick the flights to compare trainees with (e.g. an instructor flying the same lesson well). They're left out of the trainee figures.

## Needs

- **Pilot names on sessions.** New MAVIS sessions save the trainee's name. Older ones are matched from the session name ("Your First Circuit – Alex"); others can be renamed on Save.
- **The callout type.** MAVIS now sends each callout's code (e.g. `SPEED_FAST`) with the spoken line. Flights recorded before this update have the text only, so they don't count in "Does the coaching work?".
- **AOI zones named for the instruments** (as for the incident analysis).
- **At least one expert flight** for the comparisons.

## How the numbers are worked out (`backend/app/progress.py`)

| Number | Rule |
|---|---|
| Phase | MAVIS's flight phase; the first 5 s of each phase are skipped for handling (the transition itself) |
| Checked every | Average time between the starts of looks at that instrument (gaps over 60 s left out) |
| Attention spread | Normalised entropy of time per thing looked at: 0 = one thing, 100% = evenly |
| Held within ± | Standard deviation over the phase |
| Control reversals | Stick / pedal changing direction by more than 4% of travel |
| Responded | A throttle / stick / pedal / flap move (that can fix what the callout was about), seen in the 3 s average against the 3 s before, within 10 s |
| Fixed it | The value moved the right way within 15 s: 3 kt, 40 ft, 5° bank, 200 ft/min, 3° pitch, 5° flap |
| Forgot | Met on a flight, broken on the next one it applied to, with 7+ days between |
| Matters most | broken-rate × severity (warning 3, caution 2, advisory 1) × (1 + knock-on) |

The thresholds are constants at the top of `progress.py`. Results per flight are cached in `session_progress_stats` (redone when the AOI zones change); the page fills in older flights a few at a time.

Self-test: `python -m app.progress_selftest` (from `backend/`).
