# MAVIS + IRIS integration plan (proposed — not yet implemented)

Status: draft for review. No integration code has been written. The only
new artifact alongside this doc is `db/schema_mavis_integration.sql`, an
additive migration (proposal (b)) — also not applied to any database yet.

## 0. What's actually there today (grounds this plan in fact, not assumption)

**MAVIS** (`MAVIS/ai-instructor/`, separate git repo, Python 3.11 asyncio,
single process): FastAPI+WS on **:6945**. One global in-process `state`
dict pushed over `WS /ws` at 5 Hz, containing basically the whole app —
telemetry (`tel`), lesson phase, transcript, scorecard, leaderboard, session
config. **There is currently no `session_id` concept anywhere in MAVIS** —
it's a single-trainee, single-flight, process-lifetime app. Persistence is
flat JSON files only (`data/leaderboard.json`, `data/instructors.json`,
`profiles/experts/*.json`, `data/recordings/last_run.json`) — no DB, no
env-var config, everything from `config.yaml`. Every internal timestamp is
a bare `time.time()` epoch float (one place uses epoch *ms*), never an ISO
string. Zero references to eye-tracking/Smart Eye/IRIS anywhere in the code.

**IRIS** (`backend/` + `frontend/` + `Sim_Data_Adapter/`): FastAPI on
**:8000** (asyncpg, raw SQL, Supabase used only as a Postgres endpoint —
no Supabase SDK anywhere), React (Vite, react-router v6) dev server on
**:5173**. `Sim_Data_Adapter/LiveAdapter.py` is a *separate* process that
**currently opens SimConnect itself**, plus bridges Smart Eye over UDP
(not TCP — flagging this against the brief, see §5), plus drives two OBS
instances, plus streams flight samples to IRIS over WS and batch-uploads
the full CSV on exit. `sessions.id` is a `uuid` minted by Postgres
(`gen_random_uuid()`) on `POST /sessions`, today created by the IRIS
frontend/backend, not by MAVIS. No reverse proxy or "serve built React
from FastAPI" wiring exists yet — `frontend/dist/` is built but unused.

The gap this plan closes: today MAVIS and IRIS don't know about each other
at all, IRIS's adapter reads SimConnect (which the target architecture
forbids), and neither app mints a `session_id` the other could join on.

## 1. Coupling design — the one decision everything else follows from

Per the brief, the only allowed coupling is: shared Postgres schema +
`session_id` + MAVIS's WS state feed. Within that, there's a real choice
about **who writes to Postgres**:

- **Option A (recommended): IRIS is the only Postgres writer.** IRIS
  backend runs a background task that connects to MAVIS's `WS /ws` as a
  *client*, reads the state feed, and writes `sessions` / `flight_data` /
  `coaching_events` / `scorecards` / `leaderboard_entries` rows itself.
  MAVIS gains a small change (mint + expose `session_id`) and otherwise
  stays completely DB-agnostic.
- **Option B: both processes write to Postgres directly.** MAVIS would
  need a new asyncpg dependency and write hooks scattered through
  `main.py` (session start, each coaching event, lesson finish, leaderboard
  post) — exactly the kind of tick-loop-adjacent coupling the brief says
  to avoid ("MAVIS's real-time tick loop must stay isolated from IRIS's
  query/video load"), and it doubles the surface area for write conflicts
  on the same tables.

**Recommendation: Option A.** MAVIS's only new responsibility is minting a
`session_id` in memory and putting it in the WS state dict it already
sends — no new dependency, no tick-loop touch, no new failure mode for
MAVIS if Postgres or IRIS is down (the bridge just stops making progress;
MAVIS keeps coaching and keeps writing its own JSON files exactly as
today). This is the smallest change consistent with "ask before touching
MAVIS's tick loop, coaching, or invariants" — the mint happens in
`configure_session()`/session-start, not in `ticker()`.

### 1a. The one MAVIS change this requires (needs your explicit sign-off before I touch MAVIS)

In `main.py`, wherever a run currently starts (`configure_session()` /
`POST /session` — see MAVIS report §4), mint `session_id = str(uuid.uuid4())`
once per run and add it to the global `session` dict so it flows into the
`state` dict already pushed over `/ws` (`state["session_id"]`). Nothing
about phase detection, monitors, scoring, LLM calls, or the 10 Hz tick loop
changes. This is additive bookkeeping, not a refactor — but it's still a
change to `main.py`, so I won't make it until you confirm.

### 1b. New component: `mavis_bridge` inside the IRIS backend

A new module (e.g. `backend/app/mavis_bridge.py`), started as a background
task in FastAPI's `lifespan` (same pattern as `db.connect()`), doing:

1. Open a WS client to `ws://localhost:6945/ws` (URL configurable via
   `settings.mavis_ws_url`), reconnect with backoff if MAVIS isn't up yet
   — the dashboard must degrade gracefully either direction.
2. On each state push: if `state.session_id` is new, upsert a `sessions`
   row (`id`, `name`, `lesson_id`, `mode`, `source='mavis'`) — this is the
   *only* place `sessions.id` gets set to a MAVIS-minted uuid instead of
   Postgres's `gen_random_uuid()` default, which is why `id` needs no
   schema change (already `uuid`).
3. Map `state.tel` (MAVIS `Telemetry.as_dict()`) into a `flight_data` row,
   stamped `ts` from MAVIS's own per-tick `time.time()` (both processes
   are on the same PC — no cross-machine clock skew to correct for here,
   unlike Smart Eye). Field mapping (full list in the migration file's
   comments):
   `alt_msl→altitude_ft, ias→airspeed_kt, vs→vertical_speed_fpm,
   hdg→heading_true_deg, pitch→pitch_deg, bank→bank_deg,
   throttle(0..1)→throttle_pct(×100), elevator→elevator_position,
   rudder→rudder_position, lat/lon→latitude/longitude`, plus straight into
   the new columns: `phase, along_ft/cross_ft (geom), rpm, aileron,
   flaps_deg, on_ground`. Fields MAVIS never reads (yaw rate, fuel,
   ambient wind/vis/temp, gear handle, elevator trim) stay null on
   MAVIS-sourced rows — same nullable columns manual-upload rows already
   leave partially null today.
4. Relay the same sample into the existing in-memory `stream.py` relay
   (the Live page's WS subscribe mechanism) so **Review/Live frontend
   code is unchanged** — it already consumes exactly this shape from
   `LiveAdapter.py` today.
5. Append new `transcript` entries to `coaching_events`; when
   `state.scorecard` transitions from empty to populated, insert into
   `scorecards`; diff `state.leaderboard` against `leaderboard_entries`
   and upsert new rows.
6. Batch flight_data inserts (e.g. every 1s or N samples) rather than
   one insert per 10 Hz tick, to keep write load light — MAVIS's tick
   rate (10 Hz) is higher than the old adapter's write cadence assumed.

### 1c. What happens to `Sim_Data_Adapter/LiveAdapter.py`

It **must stop reading SimConnect** (MAVIS is now the only reader — hard
constraint). What's left for it to do is real hardware I/O that has
nothing to do with Postgres: driving the two OBS instances and bridging
the Smart Eye stream. Trimmed responsibilities:

- Smart Eye UDP bridge → forwards to IRIS's existing
  `/sessions/{id}/stream/eye/ws` (unchanged — this already never touches
  SimConnect).
- OBS start/stop record, but its start/stop trigger can no longer be "first
  SimConnect sample" since it doesn't poll SimConnect anymore. It instead
  watches MAVIS's `WS /ws` itself (a second, independent WS client, much
  lighter than SimConnect polling) purely for start/stop/session-id
  signals.
- It needs the session_id MAVIS minted, which it now reads off that same
  WS feed instead of being launched with it as `argv[1]`.

This means IRIS's `POST /sessions/{id}/launch-adapter` (backend/app/routers/sessions.py)
changes from "frontend tells backend to launch the adapter for a
frontend-created session" to "backend's `mavis_bridge` auto-launches the
trimmed OBS/Smart-Eye adapter the moment it sees a new MAVIS session" —
removing a manual step from the trainee's flow. I'd rename the script
(e.g. `SmartEyeObsAdapter.py`) to reflect the narrower job; `MSFSAdapter.py`
(already noted as superseded) would be safe to delete at that point, but
I'll leave deletions for you to confirm separately since it's a repo
cleanup, not integration work.

### 1d. Discrepancy flag: UDP vs TCP for Smart Eye

The brief says "ingests the Smart Eye TCP stream itself." The actual,
working implementation (both the durable file-import path and the live
bridge) uses **UDP**, matching Smart Eye Pro's actual "Output Data"
network protocol. I'm treating "TCP" in the brief as shorthand for "network
stream" rather than a literal requirement, since Smart Eye doesn't offer a
TCP variant of this feed — flagging so you can correct me if you meant
something else (e.g. a different SEP export mode).

## 2. Unified Postgres schema

See `db/schema_mavis_integration.sql` — a migration additive on top of the
existing `db/schema.sql` (nothing renamed or dropped). Summary:

- `sessions`: + `lesson_id, mode, instructor_callsign, source`
- `flight_data`: + `phase, along_ft, cross_ft, rpm, aileron_position,
  flaps_deg, on_ground` (all nullable)
- new `coaching_events` (session_id, ts, code, severity, who, phase,
  message, data jsonb)
- new `scorecards` (session_id, 5 metric scores, total, grade, retries,
  capped)
- new `leaderboard_entries` (the all-time leaderboard -- MAVIS no longer
  keeps a JSON file; the bridge saves each posted run from
  `state["leaderboard_posts"]`, served by `routers/leaderboard.py`)

`session_id` (the `sessions.id` uuid) is the join key across every table,
minted once by MAVIS per run, consumed everywhere else — exactly the
"single session_id" coupling the brief specifies.

## 3. Frontend route map — one React shell, split by backend

```
/                       Home / main menu (NEW)            — no backend calls, just nav
/live                   MAVIS coaching tablet (embedded)   → MAVIS :6945 (proxied /mavis/*)
/monitor/:sessionId     Live spectator (eye+flight overlay) → IRIS  :8000  (existing LiveSession.jsx, WS)
/sessions               Session list/manage                → IRIS  :8000  (existing Sessions.jsx)
/upload                 Manual CSV/video ingest             → IRIS  :8000  (existing Upload.jsx)
/sessions/:id/review    Debrief dashboard (timeline, scan   → IRIS  :8000  (existing Review.jsx)
                        path, AOI dwell, cognitive-cost)
/sessions/:id/analytics Numeric summary                     → IRIS  :8000  (existing Analytics.jsx)
/leaderboard            Leaderboard (NEW)                   → IRIS  :8000  (new: reads leaderboard_entries)
/aoi-zones              AOI rectangle editor                → IRIS  :8000  (existing AoiEditor.jsx)
```

Notes:
- **`/live`**: MAVIS already has a polished, purpose-built tablet UI
  (`web/static/index.html`) that talks to its own REST/WS API. Rebuilding
  it as native React would be a large, invariant-adjacent MAVIS-frontend
  effort for no functional gain. Recommended first slice: embed it via
  `iframe` pointed at the proxied `/mavis/` path, inside a thin React route
  wrapper (so it lives under the same nav shell). A native React rebuild
  stays an option later if you want a fully consistent look-and-feel, but
  it's out of scope for making the systems *work* together.
- **`/monitor`** vs **`/live`**: these are different audiences — `/live` is
  the trainee/instructor coaching control surface (MAVIS), `/monitor` is
  an eye-tracking/telemetry spectator view (IRIS's existing Live page,
  which already subscribes to the same flight-sample WS shape the bridge
  now produces). Both can be open on different devices at once.
- **Degrade-gracefully rule** applies per-route: each IRIS-backed screen
  wraps its data fetch so a down `:8000` shows "Review unavailable —
  analytics service not running" instead of crashing; `/live` similarly
  shows "Coaching unavailable — MAVIS not running" if `:6945`/the proxied
  path 502s. I'd implement this as a small shared `<ServiceGate>` wrapper
  the new shell uses around each backend-bound route.

## 4. Reverse proxy + launcher design

### 4a. Reverse proxy

A **new, third small process**, not folded into either backend (matches
the brief's `proxy` being its own step after IRIS in the launcher order).
Proposed: a minimal Starlette/`httpx` ASGI app (~150 lines, no new runtime
dependency beyond what's already in the Python ecosystem here) living at
e.g. `IRIS/proxy/app.py`, doing:

- `StaticFiles` mount at `/` serving `frontend/dist/` (the built unified
  React shell) with `html=True` fallback so client-side routing works.
- `/api/*` → reverse-proxy (strip `/api`) to `http://localhost:8000/*`,
  including WS upgrade passthrough for `/api/sessions/*/stream/*`.
- `/mavis/*` → reverse-proxy (strip `/mavis`) to `http://localhost:6945/*`,
  including WS passthrough for `/mavis/ws`.
- `/health` → aggregates both backends' `/health` (MAVIS has no explicit
  `/health` today — `GET /` or `/media/manifest` would serve as a liveness
  probe instead) so the launcher and the frontend's `<ServiceGate>` have
  one place to check status.

Listens on one port for the whole system — proposing **3000** (avoids
needing admin rights for 80 on Windows; easy to change).

### 4b. Launcher

One script (`IRIS/scripts/launch_all.ps1`, PowerShell since this is a
Windows sim PC) doing, in order, each gated on a health check before
proceeding:

1. **Postgres** — check `pg_isready` (or attempt a TCP connect); start the
   local Postgres service if installed-but-stopped, else fail fast with a
   clear message (don't try to install Postgres from the launcher).
2. **MSFS check** — advisory, not blocking: check for a running
   `FlightSimulator.exe` process; warn (not fail) if absent, since MAVIS's
   `mock`/`replay` sim adapters work without MSFS.
3. **MAVIS** — `python main.py` in `MAVIS/ai-instructor/`, poll
   `http://localhost:6945/` until it responds.
4. **IRIS backend** — `uvicorn app.main:app --port 8000` in `backend/`,
   poll `/health` until `{"status":"ok"}`.
5. **Proxy** — start last, poll `/health` (§4a) until both upstreams show
   healthy, then print the single URL (`http://localhost:3000`) for the
   user.

Each step's failure stops the launcher with the specific step named,
rather than silently continuing — but a failure at step 3 or 4 alone
should not prevent the proxy from starting in step 5, since the whole
point of the degrade-gracefully requirement is that the dashboard should
still come up and say what's missing.

## 5. Open items / things I'd like your call on before I write any code

1. **Confirm the MAVIS `session_id`-mint change (§1a)** — smallest
   possible touch to MAVIS, but it's still a `main.py` edit, so I'm not
   doing it without sign-off.
2. **UDP vs TCP for Smart Eye (§1d)** — confirm "TCP" in the brief wasn't
   pointing at a specific different integration than the existing UDP
   bridge.
3. **Proxy port** — 3000 proposed; say if you want a different one (e.g.
   80, which needs elevated privileges on Windows to bind).
4. **`/live` as iframe embed vs. native React rebuild** — recommending
   iframe embed for the first slice; rebuild is a separate, larger,
   later decision.
5. **VRAM/model verification** (qwen2.5:7b-or-smaller + whisper-small,
   frame-time check) — this is a runtime validation task, not a schema/
   architecture decision; I'd tackle it as an early implementation slice
   once the plan is approved, not as part of the plan itself.

Once you've weighed in on the above, I'll break implementation into the
small, individually-testable slices per your working-style instructions —
starting with the local Postgres cutover (change only IRIS's connection
string, run the base + integration migrations) since it's the lowest-risk
first step and unblocks testing everything else.
