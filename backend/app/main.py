"""FastAPI entrypoint."""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import db, videos
from .config import settings
from .routers import analytics, data, ingest, sessions, stream

os.makedirs(settings.video_dir, exist_ok=True)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    yield
    await db.disconnect()


app = FastAPI(title="Flight Review System API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sessions.router)
app.include_router(ingest.router)
app.include_router(data.router)
app.include_router(analytics.router)
app.include_router(stream.router)
app.include_router(videos.router)

app.mount("/videos", StaticFiles(directory=settings.video_dir), name="videos")

@app.get("/health")
async def health():
    return {"status": "ok"}
