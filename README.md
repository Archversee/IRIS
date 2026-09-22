# Flight Review System

Full-stack skeleton for the *Integrated Review and Insight System for Eye
Tracking Data within Flight Simulators*.

- **Backend:** FastAPI + asyncpg
- **Frontend:** React (Vite) + recharts
- **Database:** local Postgres (plain Postgres, no Supabase — see §1)

This repo also hosts **MAVIS** (`MAVIS/`), a separate live AI flight
instructor app (its own FastAPI + WS server on :6945) that IRIS integrates
with. See `docs/INTEGRATION_PLAN.md` for the full architecture; this
README covers running IRIS's own backend/frontend.

> **Note on machines:** the real target machine is the sim PC (runs MSFS,
> Ollama, Postgres, both backends). If you're setting this up on a
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

```bash
cd backend
py -m venv .venv && source .venv/bin/activate
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

## Data flow

1. On the sim laptop, `Sim_Data_Adapter/LiveAdapter.py` writes
   `logs/simconnect_log.csv` (flight state), and — if OBS recording is
   enabled — starts/stops the instrument and OTW recordings in step with
   the flight data (see below). The same script also bridges Smart Eye's
   live UDP feed to the Live page when a session id is attached (see
   "Going live" below) — flight telemetry, eye tracking and OBS recording
   are all one process, one script, one session id.
2. Smart Eye (separate laptop) exports its gaze CSV for durable, full-
   resolution review after the flight.
3. In the app: **Upload** → create a session → attach the CSVs → ingest.
4. **Review** plays flight + gaze back on one synchronized timeline.
5. **Analytics** shows derived metrics and gaze dwell per area of interest.

### Going live

Click **● Go Live** on the Live page: it creates the session and asks the
backend to launch `LiveAdapter.py` for you, with that session's id already
filled in — no copy-pasting an id into the script by hand. This only works
when the backend runs on the same machine as MSFS/OBS/SEP (the assumed
topology — see `LiveAdapter.py`'s docstring); if the launch can't find the
script there, the Live page falls back to showing the command to run it
yourself. The script path is `backend/app/config.py`'s
`adapter_script_path` / `adapter_python_path` if you need to point it
somewhere else (e.g. a different Python environment).

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

- **Flight** — headers matching `LiveAdapter.py`'s `SIMVARS` keys plus
  `timestamp_utc`. Ingest maps `timestamp_utc → ts`.
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
