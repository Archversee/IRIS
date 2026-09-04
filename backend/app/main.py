"""FastAPI entrypoint."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import db
from .config import settings
from .routers import analytics, data, ingest, sessions, stream


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


@app.get("/health")
async def health():
    return {"status": "ok"}
