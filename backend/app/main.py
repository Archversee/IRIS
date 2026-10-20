"""FastAPI entrypoint."""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import auth, db, eye_clock, incident_explain, mavis_bridge, videos
from .config import settings
from .routers import (accounts, adapter, aoi_zones, analytics, data, debrief, incidents, ingest, progress,
                      routes, rule_results, sessions, stream)

os.makedirs(settings.video_dir, exist_ok=True)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    await auth.ensure_schema()  # users/auth_tokens tables + first admin account
    await videos.ensure_schema()  # sessions.recording_paths (OBS files to delete with a session)
    await eye_clock.ensure_schema()  # sessions.eye_clock_* (Smart Eye clock offset, from LiveAdapter.py)
    try:
        await rule_results.ensure_schema()  # SOP rule checks of flights (db/schema_rule_results.sql)
        await incident_explain.ensure_schema()  # explanations of broken-rule moments (+ instructor ratings)
        await incidents.ensure_schema()  # cached findings of each broken-rule moment
        await progress.ensure_schema()  # Progress page: cached per-flight statistics, expert reference flights
        await debrief.ensure_schema()  # whole-flight AI summaries (Review → Debrief)
    except Exception as e:  # e.g. sessions table not created yet -- never block startup
        logging.getLogger(__name__).warning("rule results schema not applied: %s", e)
    await mavis_bridge.ensure_schema()  # flight_events (MAVIS event start/end times)
    # Background WS client into MAVIS -- reconnects on its own if MAVIS
    # isn't up yet/goes away, so this never blocks IRIS's own startup or
    # crashes the app when MAVIS is offline. See docs/INTEGRATION_PLAN.md §1b.
    mavis_bridge.start()
    yield
    await mavis_bridge.stop()
    await db.disconnect()


app = FastAPI(title="Flight Review System API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin, settings.proxy_origin],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(accounts.router)
app.include_router(routes.router)
app.include_router(sessions.router)
app.include_router(adapter.router)
app.include_router(aoi_zones.router)
app.include_router(ingest.router)
app.include_router(data.router)
app.include_router(analytics.router)
app.include_router(rule_results.router)
app.include_router(stream.router)
app.include_router(videos.router)
app.include_router(eye_clock.router)
app.include_router(incidents.router)
app.include_router(progress.router)
app.include_router(debrief.router)

app.mount("/videos", StaticFiles(directory=settings.video_dir), name="videos")

@app.get("/health")
async def health():
    return {"status": "ok"}
