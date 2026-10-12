# Checking flights against the SOP rules

The rules made from an SOP or a training handbook ([sop-rules.md](sop-rules.md)) are checked against what the aircraft actually did: **live** during a MAVIS lesson, and **post-flight** on the recorded flight in IRIS's Review page. Both use the same engine, so a live callout and the post-flight report never disagree. The instructor has the final say on every result.

## How it fits together

```
flight_rules (active set + handbook sets)
        │  loaded at lesson start (sop/runtime.py)
        ▼
  sop/evaluate.py  RuleEvaluator ── one sample at a time ──┐
        ▲                                                   │
  live: main.py ticker (10 Hz)          post-flight: sop/results.py replays flight_data
        │                                                   │
        ▼                                                   ▼
  state["sop_live"] (in-flight view)       flight_evaluations + rule_results (Postgres)
  SOP_RULE callout for warning rules                        │
                                                            ▼
                                    IRIS Review: chip, drawer, timeline bands,
                                    instructor verdicts + sign-off (backend/app/routers/rule_results.py)
```

| File | What it does |
|---|---|
| `sop/evaluate.py` | The engine. Pure: rules + samples in, results out. Strictness presets |
| `sop/results.py` | Loads a session's `flight_data`, picks the rules and strictness, stores the results |
| `sop/eval_routes.py` | `GET /sop/strictness`, `POST /sop/evaluate/{session}`, `GET /sop/evaluations/{session}` |
| `main.py` | Live: starts the engine when the SOP loads, steps it every tick, speaks warnings, stores the run's results at the end |
| `db/schema_rule_results.sql` | `flight_evaluations`, `rule_results`, `users.rule_strictness` (IRIS applies it at startup) |
| IRIS `backend/app/routers/rule_results.py` | Read results; instructor verdicts, sign-off, per-trainee strictness (role-checked) |
| IRIS `frontend/src/components/session/RuleChecksPanel.jsx` | Review page chip + drawer + timeline bands |
| IRIS `frontend/src/live/components/InFlightPanel.jsx` | Live: rules being broken right now |

## How each kind of rule is judged

| Kind | Judged | Fails when |
|---|---|---|
| `band` | While the rule applies, except the first *settle* seconds after a phase change | An excursion lasts longer than *sustain*, or more than *max out* of the time was outside. Excursions shorter than *debounce* are ignored |
| `limit` | While the rule applies (hard limit, no tolerance multiplier) | Exceeded for *debounce* seconds or longer |
| `event` | At the start of `gate_phase` (it may still come true within *debounce*); without a gate, any time the rule applies | Not true by then / never true |
| `capture` | At lift-off, touchdown (the last airborne sample), phase entry or phase exit | The value is outside min … max |
| `gaze` | Not yet: needs AOI roles on IRIS's AOI zones | → *Not checked yet* |
| `unobservable` | Can't be measured | → *Instructor to assess* |

A rule "applies" when the phase is one of its `phases` (none = every phase except briefing and debrief) and all of its `conditions` hold. Results: **Met**, **Not met**, **Didn't apply** (phase never flown, condition never true), **No data** (the metric wasn't recorded), **Not checked yet**, **Instructor to assess**.

## Strictness

| | Lenient | Standard (ACS-like) | Strict |
|---|---|---|---|
| Ignore anything shorter than | 3 s | 2 s | 1 s |
| A band excursion fails after | 10 s | 5 s | 2 s |
| Grace after a phase change | 10 s | 6 s | 3 s |
| Band tolerance multiplier | ×1.5 | ×1.0 | ×1.0 |
| Time allowed outside a band | 25 % | 15 % | 5 % |

Which preset a check uses, first match wins:

1. **The session's.** MAVIS `POST /session {strictness}` before the flight, or the instructor's choice when re-checking in Review. Later re-checks keep it.
2. **The trainee's.** `users.rule_strictness`, set by their instructor or an admin (`PUT /users/{id}/rule-strictness`). Sessions aren't linked to accounts yet, so the trainee is found by the session's pilot name (full name or username).
3. **The mode's default.** Visitor → lenient, trainee → standard, pilot → strict.

The evaluation records which preset it used and why.

## Live

- When the SOP loads at lesson start, the engine starts with the flight's rules and strictness (`state["sop_live"]`).
- Every tick (only while flying, not while the aircraft is held) it gets the telemetry and phase.
- The in-flight view shows the rules being broken right now, then the last one, then the end-of-run summary.
- A **warning** rule that is broken is spoken (`SOP_RULE`, word for word, with the SOP's own numbers). This happens in every mode except exam, at most once per rule every 30 s, and not at all if the value is back within limits before it would be spoken. Cautions and advisories are only shown.
- At the end of the run the results are stored as a `live` evaluation of the IRIS session. If IRIS isn't running (no session row), they stay in the live view only.

## Post-flight (Review)

- **The chip** in the title bar shows met / not met / to assess, and whether the instructor has signed off. Click it to open the drawer.
- **Instructors** can:
  - **Re-check** with another strictness, and optionally with the rules as they are now. Otherwise the flight's own rule snapshot is used, so later edits never change an old result silently.
  - Set any rule to **Met / Not met / Didn't apply**, with a note. This is the final verdict, and it is carried over to every later re-check of the session.
  - **Sign off** the results, with a comment for the trainee.
- **Trainees** see "Work on these first" (rules not met, warnings first), the instructor's notes, and *Provisional* until it is signed off.
- Each broken rule lists when it was broken. Click a moment to jump the videos and timeline there. The same moments are red (warning) or amber bands on the timeline.

A session flown without MAVIS (CSV upload) has no lesson phases. Its rules that apply in every phase are still checked; rules for a particular phase show *No data*. The aircraft comes from `sessions.aircraft`, matched like the simulator's aircraft name.

## When nothing shows up

The live views (MAVIS `/live` and IRIS's **Live Monitor**) always show a rules line once a lesson starts. If no rules are being checked, it says why:

| Message | Fix |
|---|---|
| *No active rule set for <aircraft> and no active training handbook* | SOP Rules page: **Make active** on a rule set for that aircraft, or on a handbook set |
| *Aircraft not recognised (…)* | Add the simulator's name for the aircraft to `sim_match` in `profiles/aircraft/<id>.yaml`, or set `sop.aircraft_override` |
| *Rules database unavailable* | Start Postgres; check `database.url` / `DATABASE_URL` |
| *The active rule sets have no enabled rules for … lessons* | Enable rules, or tick the lesson type on them |
| *Rule checks are disabled in MAVIS* | `pip install asyncpg`, restart MAVIS |

On Review, the chip reads **SOP: no rules checked** when the flight was checked while no rule set was active. Activate one and press **Re-check**: a check with no rules never pins the flight to that empty set. **SOP checks: not run** means the flight was never checked. That happens with flights flown before this feature, or when IRIS wasn't running during the flight. An instructor can press **Check now**.

After updating: restart MAVIS **and** the IRIS backend (it creates the tables), and restart or rebuild the frontend.

## Tests

```
python -m sop.evaltest     # every kind on hand-made samples + a full mock circuit (no DB, no sim)
```

## Not done yet

- Gaze rules (needs an AOI role on each AOI zone, and eye data reaching MAVIS for live).
- A runway / lesson picker for uploaded CSV flights, so phase-specific rules can be checked on them too.
- The LLM-written trainee debrief from these results.
- A strictness picker in MAVIS's main menu (the API takes it; the menu doesn't offer it yet).
- Linking sessions to trainee accounts (strictness is matched by name until then).
