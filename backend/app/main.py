"""FastAPI entrypoint."""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import auth, db, mavis_bridge, videos
from .config import settings
from .routers import accounts, adapter, aoi_zones, analytics, data, ingest, sessions, stream

os.makedirs(settings.video_dir, exist_ok=True)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    await auth.ensure_schema()  # users/auth_tokens tables + first admin account
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
app.include_router(sessions.router)
app.include_router(adapter.router)
app.include_router(aoi_zones.router)
app.include_router(ingest.router)
app.include_router(data.router)
app.include_router(analytics.router)
app.include_router(stream.router)
app.include_router(videos.router)

app.mount("/videos", StaticFiles(directory=settings.video_dir), name="videos")

@app.get("/health")
async def health():
    return {"status": "ok"}
