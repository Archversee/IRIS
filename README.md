# Flight Review System

Full-stack skeleton for the *Integrated Review and Insight System for Eye
Tracking Data within Flight Simulators*.

- **Backend:** FastAPI + asyncpg
- **Frontend:** React (Vite) + recharts
- **Database:** local Postgres (plain Postgres, no Supabase — see §1)

This repo also hosts **MAVIS** (`MAVIS/`), a separate live AI flight
instructor app (its own FastAPI + WS server on :6945) that IRIS integrates
with. See `docs/INTEGRATION_PLAN.md` for the full architecture; this
README covers running IRIS's own backend/frontend, and MAVIS in §4.

> **Note on machines:** the target machine is the sim PC (runs MSFS,
> Postgres, both backends). If you're setting this up on a
> different dev machine first, everything below still works — MAVIS runs
> fine without MSFS (defaults to a replay/mock adapter) and IRIS's Postgres
> is just a local install, same steps either way.

```
flight-review-system/
├── db/schema.sql              # tables + indexes (run against local Postgres — see §1)
├── db/schema_mavis_integration.sql  # additive migration for the MAVIS integration
├── backend/                   # FastAPI app
│   ├── app/
│   │   ├── main.py            # entrypoint, CORS, router wiring
│   │   ├── db.py              # asyncpg pool + bulk COPY helpers
│   │   ├── schemas.py         # pydantic models
│   │   └── routers/
│   │       ├── sessions.py    # create/list/get/delete sessions
│   │       ├── ingest.py      # CSV upload (flight / eye)
│   │       ├── data.py        # time-range + downsampled reads
│   │       ├── analytics.py   # derived performance metrics
│   │       └── stream.py      # real-time stub (WS + REST)
│   ├── requirements.txt
│   └── .env.example
└── frontend/                  # React app
    └── src/
        ├── api/client.js
        └── pages/{Sessions,Upload,Review,Analytics}.jsx
```

## 1. Database (local Postgres)

**1a. Install PostgreSQL 17** (Windows):

1. Download the installer from postgresql.org's Windows download page (the
   EDB installer) — pick version **17** to match `db/schema.sql`.
2. Run it. Keep the defaults (install dir, port **5432**, locale). When it
   asks for a superuser (`postgres`) password, **set it to `postgres`** —
   that matches this app's built-in local default
   (`backend/app/config.py`'s fallback `DATABASE_URL`), so you may not even
   need a `.env` file afterward. Use a different password if you prefer;
   you'll just need to put it in `.env` explicitly (step 1c).
3. Keep "Command Line Tools" checked in the component list — that's what
   gives you `psql` on PATH. If the installer doesn't offer to add
   `C:\Program Files\PostgreSQL\17\bin` to your PATH, add it yourself
   (System Properties → Environment Variables), then open a **new**
   terminal so it takes effect.
4. Verify it's up:
   ```bash
   pg_isready
   # should print: .../postgres:5432 - accepting connections
   ```

**1b. Apply the schema** — the base schema, then the MAVIS-integration
migration on top of it (both are plain SQL, idempotent — safe to re-run):

```bash
psql -U postgres -d postgres -f db/schema.sql
psql -U postgres -d postgres -f db/schema_mavis_integration.sql
psql -U postgres -d postgres -f db/schema_sop_rules.sql   # MAVIS SOP -> rules (see MAVIS/ai-instructor/docs/sop-rules.md)
psql -U postgres -d postgres -f db/schema_rule_results.sql # flights checked against those rules (also applied at backend startup; see MAVIS/ai-instructor/docs/rule-checks.md)
```

(It'll prompt for the postgres user's password each time unless you set
`PGPASSWORD` in your shell first.)

**1c. Point the backend at it** — copy the example env file and edit if
your password/port differ from the defaults:

```bash
cd backend
cp .env.example .env
```

`.env.example` already has `DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres`
— if you set a different password in step 1a.2, edit that line to match.

## 2. Backend

#PYTHON 3.12.10

```powershell
cd backend
py -m venv .venv
.venv\Scripts\Activate.ps1      # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Interactive API docs: <http://localhost:8000/docs>.

## To Run after setup
```bash
cd backend
.venv\Scripts\activate
uvicorn app.main:app --reload --port 8000
uvicorn app.main:app --port 8000
```

## 3. Frontend

```bash
cd frontend
npm install
npm run dev                    # http://localhost:5173
```

The dev server proxies `/api/*` to `http://localhost:8000` (see
`vite.config.js`), so no CORS headaches locally.

## 4. MAVIS

MAVIS (`MAVIS/ai-instructor/`) is the live AI flight instructor: a gentle
teacher companion for MSFS (and later Prepar3D) that guides a trainee through
a traffic pattern at Paya Lebar (WSAP) in a C172 G1000, with live coaching,
typed Q&A, and a video avatar. It is a separate app with its own dependencies
and its own FastAPI + WS server on :6945 — see `docs/INTEGRATION_PLAN.md` for
how it fits together with IRIS. Everything runs locally: Ollama for the LLM,
Kokoro/Piper/SAPI for the voice.

Commands and paths in this section are relative to `MAVIS/ai-instructor/`
unless they say otherwise.

### Setup (one-time)

```bash
cd MAVIS/ai-instructor
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pip install SimConnect pygame pyttsx3 kokoro soundfile sounddevice
```

The second line is deliberately separate from `requirements.txt` (see the
commented-out block at its end) — these are Windows/sim-PC-only (SimConnect
for `sim.adapter: msfs`, pygame for local joystick/HID, the rest for real
TTS instead of console-only fallback) and would fail to install on a
non-Windows dev machine, so they're not pulled in by default.

The sim adapter comes from `sim.adapter` in `config.yaml` (`mock` | `msfs` |
`replay`); `--sim <adapter>` overrides it for one run:

```bash
.venv\Scripts\python main.py --sim mock     # self-flying mock aircraft, no sim needed
.venv\Scripts\python main.py --sim msfs     # live MSFS via SimConnect (sim PC)
.venv\Scripts\python main.py --sim replay   # a recorded flight played back as live
```

MAVIS serves its own UI at <http://localhost:6945> (also reachable through
the reverse proxy at `/mavis/`, see §6) — that's what you actually fly the
lesson from; nothing else in this repo talks to SimConnect.

### Quick start (mock mode, no sim needed)

Run `main.py --sim mock`, open `http://<this-pc-ip>:6945` on the tablet (same
Wi-Fi) and tap **Start Lesson**. The mock aircraft flies one circuit by itself
so you can see phases, coaching, and the map end-to-end. Add `?demo=1` to the
URL for presenter buttons (skip phase / night / restart). Time-of-day can also
be changed via chat.

### Hardware topology

```
SIM PC (MSFS + Warthog/rudder USB)      LAPTOP RTX 5090 (this app)        TABLET
  MSFS SimConnect  ──── LAN ────▶  Ollama + lesson engine + web  ◀─ browser only
  [optional] hid_forwarder.py ─▶  server + TTS synthesis            :6945
```

- **Sim PC, nothing to install** for telemetry. One config edit only (below).
  Control positions (stick/throttle/rudder as the sim sees them) already arrive
  via SimConnect. *Optionally*, for raw input timing: copy `tools/hid_forwarder.py`
  there, `pip install pygame`, run `python hid_forwarder.py --to <laptop-ip>`,
  and start MAVIS with `python main.py --sim msfs --input udp`.
- **Laptop** runs everything else. A browser on the laptop at `localhost:6945`
  works exactly like the tablet.

#### Enable network SimConnect on the sim PC (one-time)

Edit `SimConnect.xml` (MSFS: `%APPDATA%\Microsoft Flight Simulator\SimConnect.xml`;
create if missing) and make sure it has a global IPv4 section:

```xml
<SimConnect.Comm>
  <Protocol>IPv4</Protocol>
  <Scope>global</Scope>
  <Address>0.0.0.0</Address>
  <Port>500</Port>
  <MaxClients>64</MaxClients>
</SimConnect.Comm>
```

Allow port 500 through the sim PC's firewall. Restart MSFS.

### Real sim (run on the laptop)

1. The setup above, with `SimConnect` and ONE voice engine installed;
   recommended: `kokoro soundfile` (or `pyttsx3` for a basic voice;
   `engine: auto` picks the best installed). Voice plays on the **tablet** by
   default (`voice.output: tablet`).
2. Ollama on the laptop: `ollama pull qwen2.5:14b-instruct`
   (`coach.ollama_url: http://127.0.0.1:11434`).
3. `config.yaml`: set `sim.host` to the sim PC's LAN IP (the app writes
   `SimConnect.cfg` automatically from it).
4. In MSFS: C172 G1000, parked at WSAP runway 02, daytime.
5. `python main.py --sim msfs` (add `--input udp` if using hid_forwarder / joystick PTT).

### Replay a recorded flight (no sim needed)

`--sim replay` plays a logged flight back as live telemetry, so the map,
gauges, controls, coaching and score are the real flight rather than the mock
aircraft. To test a new recording, overwrite two files and start the app — no
code or config edits:

| Drop in | What it is |
|---|---|
| `data/recordings/replay/flight.csv` | the logger's SimConnect CSV export |
| `frontend/public/media/videos/replay.mp4` (from the repo root) | screen recording of the same flight, started and stopped with the logger |

- The runway end is detected from the log's initial heading, and the lesson
  geometry (map, pattern, scoring) follows it.
- The first start after a new `replay.mp4` spends ~30 s building
  `replay.web.mp4` (browser-ready, fixed 30 fps, dropped frames held); later
  starts reuse it. The clip is started the moment playback begins, so it runs
  in step with the log.
- Sanity-check a log without the UI: `python tools/score_log.py data/recordings/replay/flight.csv --reciprocal`
  prints the phase transitions and scorecard (`--reciprocal` when flown from runway 20).
- Recording tips: 30 fps is enough and avoids the encoder dropping frames;
  log `AILERON_POSITION` too, or the stick's roll axis is inferred from bank.

#### Milestone-1 spike checklist (do this first)

- [ ] Telemetry reads OK (`--sim msfs`, watch log/UI)
- [ ] Day→night works: `curl -X POST localhost:6945/demo/night`
      If the sim ignores ZULU_*_SET events (version-dependent), bind a key in
      MSFS to a time preset as fallback.
- [ ] Verify RWY 02 threshold lat/lon in `lessons/lesson01_pattern.yaml`
      against the sim (slew to threshold, read UI values).

### Code layout

```
main.py                 wiring: builds the runtime, web actions, the 10 Hz tick loop, server
runtime/   context.py           `rt`: the shared runtime context every module reads
           bootstrap.py         config/lesson loading, sim adapter / engine / scorer factories
           session.py           configure / start / restart / relaunch / quit, lesson select
           flight_flow.py       briefing, freeze + control lock, demo, airborne start, night
           tick_steps.py        the tick loop's steps (event routing, scoring, state, track)
           checkpoints.py       "let me try that again": checkpoints, retry, forced repeats
           chat_router.py       chat intents: deterministic rules -> LLM tool pick -> Q&A
           scoring_flow.py      scorecard, debrief (with fallback), leaderboard
           speech.py            serialized TTS channel, reading-lesson narration
           sop_live.py          SOP rules checked live during the flight
           instructor_admin.py  instructor PIN / accounts / reference runs
adapters/  sim_msfs.py  SimConnect (MSFS/P3D), all sim-specific code lives here
           sim_mock.py  self-flying mock for development/demo
           input_hid.py Warthog/rudder raw HID via device profile YAMLs
core/      phase_detector.py  pattern geometry -> phase transitions
           monitors.py        altitude/speed/flaps/bank/centerline/control checks
           lesson_engine.py   state machine + optional night event + cooldowns
coach/     llm_coach.py  Ollama phrasing + typed Q&A, template fallback
           phrases.py    gentle persona + fallback lines (edit tone here)
voice/     tts.py        kokoro | piper | pyttsx3, all local
web/       server.py + static/index.html   tablet UI (avatar, chat, map, gauges)
profiles/  aircraft & device YAMLs         lessons/  lesson scripts
```

See **`MAVIS/ai-instructor/docs/phase-transitions.md`** for exact rules to
advance each phase.

**How main.py and runtime/ fit together.** `main.py` creates the shared
objects (state, session, coach, event log, ...), publishes each on `rt`, and
calls one `build_X(rt)` per runtime module; each module defines its actions and
publishes them on `rt` too (`rt.start`, `rt.finish_lesson`, `rt.speak`, ...).
Code always reads through `rt` at call time and never keeps its own copy:
`select_lesson()` swaps `rt.lesson` / `rt.engine` / `rt.scorer` mid-run, and a
held reference would go stale. A new shared name needs a slot in
`runtime/context.py`.

**The tick loop** (`ticker()` in main.py) is one synchronous, ordered loop
by design: read telemetry, spike filter, `rt.engine.tick()` (phase detection +
monitors), then the `tick_steps` calls in a fixed order. The steps never
`await`; background work (forced repeats, finishing a lesson) is only started
from them with `asyncio.create_task`. Keep deterministic reflexes in this loop:
don't move them into callbacks or async handlers.

### Swapping things (the flexibility story)

- **Sim → Prepar3D:** new adapter class in `adapters/` (P3D speaks SimConnect;
  mostly variable-name tweaks), set `sim.adapter`.
- **Aircraft:** copy `profiles/aircraft/c172sp_g1000.yaml`, change speeds/flaps/facts.
- **Stick/throttle/rudder:** new YAML in `profiles/devices/` mapping axes.
- **Voice/LLM:** `voice.engine`, `coach.model` in config.

## 5. OBS / Smart-Eye adapter (optional)

`Sim_Data_Adapter/LiveAdapter.py` no longer reads SimConnect (MAVIS is the
only SimConnect reader) — it just drives OBS recording and bridges Smart
Eye's live UDP feed, self-syncing to whichever session MAVIS starts. Setup:

```bash
cd Sim_Data_Adapter
py -m venv .venv
.venv\Scripts\pip install obsws-python websocket-client
```

Skip this entirely if you're not recording video/live gaze preview for a
given session — Smart Eye's own file export + the Upload page (§"Data
flow" below) is still the durable path regardless. Run by hand with
`.venv\Scripts\python LiveAdapter.py`, let the launcher (§7) start it, or
just start flying in MAVIS — IRIS's backend auto-launches it the first
time it sees a MAVIS session, so nothing in IRIS's UI needs to be clicked
for this. All three ways of starting it are safe to combine (see the
script's own lock-file docstring), and it stays running across many
flights, not just one — it restarts OBS recording and retargets the eye
bridge on its own every time MAVIS mints a new session.

## 6. Reverse proxy

The single URL you actually open — `/` serves the built React app, `/api/*`
proxies to IRIS's backend, `/mavis/*` proxies to MAVIS. Setup:

```bash
cd proxy
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

Needs `frontend/dist` to exist (`cd frontend && npm run build`). Run:

```bash
.venv\Scripts\python -m uvicorn app:app --port 3000
```

Then open <http://localhost:3000>.

## 7. Launcher (start everything at once)

Once every component above has its venv set up once, `scripts/launch_all.ps1`
starts all of them in order (Postgres → MSFS check → MAVIS → IRIS backend →
OBS/Smart-Eye adapter → proxy), health-checking each step but never hard-
stopping on a failure — the dashboard still comes up showing whichever
services are actually available. Ctrl+C stops everything it started.

```powershell
./scripts/launch_all.ps1                  # everything
./scripts/launch_all.ps1 -SkipAdapter      # no OBS/Smart-Eye this session
./scripts/launch_all.ps1 -DryRun           # print what it would do, start nothing
```

## Data flow

1. Fly in MAVIS (§4) — Basic Flight or Free Flight, either mints a fresh
   session id everything else joins on, coaches live, and scores the run.
   MAVIS is the only thing that ever starts or ends a session; IRIS never
   logs anything on its own, only views.
2. IRIS's backend (`app/mavis_bridge.py`) watches MAVIS's own WebSocket
   feed in the background and writes flight telemetry, coaching events,
   the scorecard, and leaderboard updates into Postgres as they happen —
   no manual step, no separate script for this part. The first time it
   sees a session, it also auto-launches the OBS/Smart-Eye adapter (§5).
3. OBS records the instrument + OTW views automatically, and Smart Eye's
   live feed is now persisted to Postgres as it streams in (not just held
   for live preview), same as flight telemetry. Smart Eye's own file
   export, imported via **Upload → ingest** after the flight, is still the
   durable, full-resolution path and replaces whatever the live feed
   captured for that session.
4. Ending the flight in MAVIS shows the scorecard, then routes straight
   into **Upload**, pre-filled with that session — fill in
   name/pilot/aircraft/notes, link the OBS video files, and ingest the
   Smart Eye export CSV if you have one.
5. **Review** plays flight + gaze back on one synchronized timeline, phase
   by phase, with MAVIS's coaching callouts alongside.
6. **Analytics** shows derived metrics and gaze dwell per area of interest;
   **Leaderboard** shows MAVIS's scored runs across sessions.

### Live Monitor (view-only)

Live Monitor (under IRIS in the sidebar) never starts or stops anything —
it lists whichever MAVIS sessions are currently in progress
(`GET /sessions/running`) and lets you pick one to watch, attaching to it
the same way Review attaches to a finished one. A session drops off that
list the moment its MAVIS End Session/Main-menu flow finishes (§ above),
not merely because a scorecard appeared ("Fly again" keeps the same
session going). Screen capture for the two views uses the browser's own
picker (`getDisplayMedia`), independent of OBS's own recording.

### Video recording (OBS)

`LiveAdapter.py` can drive two OBS Studio instances over `obs-websocket`
so the instrument-view and OTW-view recordings start/stop automatically
with flight data logging, instead of being triggered by hand. No password
required — just leave authentication off in OBS.

Setup (once):

1. Run two separate OBS Studio processes — one scened to the instrument
   capture source, one to the OTW capture source. For the **instrument**
   instance, set Base (Canvas) and Output (Scaled) Resolution to match
   Smart Eye's calibrated screen resolution exactly (1920x1080) — Smart
   Eye reports gaze position (`gaze_point_x`/`gaze_point_y`) in that
   screen's native pixel coordinates, so a mismatch here silently
   misaligns any gaze-to-instrument-dial mapping built on top of it.
2. In each instance: **Tools → WebSocket Server Settings → Enable
   WebSocket server**, using a different port per instance (e.g. `4455`
   and `4456`, since both run on the same machine), and leave **Enable
   Authentication unchecked** — `LiveAdapter.py` connects with no password.
3. Point each instance's recording output path at `data/videos` (or
   wherever `backend/app/config.py`'s `video_dir` is set) so the files
   show up under **Upload** without moving them by hand.
4. In `LiveAdapter.py`, set `OBS_INSTANCES` to match each instance's
   host/port (`ENABLE_OBS_RECORDING = False` to skip OBS entirely and
   just log flight data).
5. `pip install obsws-python` (in addition to `pysimconnect`).

At runtime: once SimConnect data starts flowing, the adapter calls
`start_record()` on both OBS instances and logs an
`obs_record_start:<name>` event; on exit (including Ctrl+C) it stops
both and logs `obs_record_stop:<name>:<output_path>`, printing each
file's path so you know which is which. Match the resulting mp4s to a
session under **Upload** via `PUT /sessions/{id}/video/{screen}`
(`screen` is `instrument` or `otw`).

The two `start_record()` calls are sequential over separate websocket
connections, so there's a few-ms skew between the two files — fine for
the timestamp-based sync used elsewhere in this app, but not
frame-accurate.

### CSV column expectations

- **Flight** — headers matching `db.FLIGHT_COLUMNS` (`backend/app/db.py`)
  plus `timestamp_utc`. Ingest maps `timestamp_utc → ts`. This is for a
  manually-exported CSV; a MAVIS-driven session doesn't need this step at
  all (`mavis_bridge.py` writes flight_data directly — see "Data flow").
- **Eye** — a `ts` (ISO-8601) column plus any of the columns in
  `EYE_COLUMNS` (`backend/app/db.py`). Rename your Smart Eye export headers
  to match, or edit that list to match your export. `aoi` (area of interest)
  drives the dwell analytics.

## Real-time streaming (later)

`backend/app/routers/stream.py` already persists live samples via:

- `POST /sessions/{id}/stream/flight` — push one JSON sample
- `WS   /sessions/{id}/stream/ws` — push a stream of samples

## Next steps / ideas

- Add auth + Row Level Security when you go multi-user (schema has a note).
- Fixation/saccade detection and AOI transition matrices from gaze data.
- Correlate gaze AOI with flight phase (e.g. eyes-outside % during approach).
- Live playback broadcast channel for real-time instructor view.
