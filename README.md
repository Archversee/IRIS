# Flight Review System

Full-stack skeleton for the *Integrated Review and Insight System for Eye
Tracking Data within Flight Simulators*.

- **Backend:** FastAPI + asyncpg
- **Frontend:** React (Vite) + recharts
- **Database:** Supabase Postgres (plain Postgres)

Single-user for now (no auth). Batch CSV ingest works today; a real-time
streaming path is stubbed and ready to build out.

```
flight-review-system/
├── db/schema.sql              # tables + indexes (run in Supabase)
├── backend/                   # FastAPI app
│   ├── app/
│   │   ├── main.py            # entrypoint, CORS, router wiring
│   │   ├── db.py              # asyncpg pool + bulk COPY helpers
│   │   ├── schemas.py         # pydantic models
│   │   └── routers/
│   │       ├── sessions.py    # create/list/get/delete sessions
│   │       ├── ingest.py      # CSV upload (flight / eye / events)
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

## Heads-up: TimescaleDB is not available on Supabase

Supabase **deprecated the `timescaledb` extension** and dropped it from the
Postgres 17 bundle (what new projects get). It only survives on Postgres 15
until that reaches end-of-life (~May 2026). So `CREATE EXTENSION timescaledb`
will not work on a fresh project.

This schema therefore uses **plain Postgres** with a composite
`(session_id, ts)` primary key and a small BRIN index on `ts`. At 10 Hz that
is ~36k rows per flight-hour — trivial for Postgres. If you ever need more,
convert the two time-series tables to **native RANGE partitions on `ts`**
(Supabase recommends `pg_partman`) with no application changes.

## 1. Database

In the Supabase dashboard → SQL Editor, paste and run `db/schema.sql`.
Or from a terminal:

```bash
psql "$DATABASE_URL" -f db/schema.sql
```

## 2. Backend

#PYTHON 3.12.10

```bash
cd backend
py -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then paste your Supabase connection string
uvicorn app.main:app --reload --port 8000
```

Get the connection string from **Project Settings → Database → Connection
string (URI)**. Use the *Session* pooler URI for a long-running server. Strip
any `?pgbouncer=true` / `+asyncpg` suffix — asyncpg wants the plain
`postgresql://` scheme.

Interactive API docs: <http://localhost:8000/docs>.

## To Run after setup
cd backend
.venv\Scripts\activate
uvicorn app.main:app --reload --port 8000

## 3. Frontend

```bash
cd frontend
npm install
npm run dev                    # http://localhost:5173
```

The dev server proxies `/api/*` to `http://localhost:8000` (see
`vite.config.js`), so no CORS headaches locally.

## Data flow

1. On the sim laptop, `MSFSAdapter.py` writes `logs/simconnect_log.csv`
   (flight state) and `logs/simconnect_events.csv` (crash events).
2. Smart Eye (separate laptop) exports its gaze CSV.
3. In the app: **Upload** → create a session → attach the CSVs → ingest.
4. **Review** plays flight + gaze back on one synchronized timeline.
5. **Analytics** shows derived metrics and gaze dwell per area of interest.

### CSV column expectations

- **Flight** — headers matching `MSFSAdapter.py`'s `SIMVARS` keys plus
  `timestamp_utc`. Ingest maps `timestamp_utc → ts`.
- **Eye** — a `ts` (ISO-8601) column plus any of the columns in
  `EYE_COLUMNS` (`backend/app/db.py`). Rename your Smart Eye export headers
  to match, or edit that list to match your export. `aoi` (area of interest)
  drives the dwell analytics.
- **Events** — either the raw two-column `MSFSAdapter` format
  `[timestamp, event_name]` (no header) or a headered
  `ts,event_type,label` CSV.

## Real-time streaming (later)

`backend/app/routers/stream.py` already persists live samples via:

- `POST /sessions/{id}/stream/flight` — push one JSON sample
- `WS   /sessions/{id}/stream/ws` — push a stream of samples

To go live, have `MSFSAdapter.py` POST rows here instead of (or alongside)
writing CSV. Live *playback* fan-out to the browser isn't built yet — that's
the next piece.

## Next steps / ideas

- Add auth + Row Level Security when you go multi-user (schema has a note).
- Fixation/saccade detection and AOI transition matrices from gaze data.
- Correlate gaze AOI with flight phase (e.g. eyes-outside % during approach).
- Live playback broadcast channel for real-time instructor view.
