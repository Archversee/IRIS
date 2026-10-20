"""Thin reverse proxy -- the single origin the user hits. Not a business-
logic service, just glue so `/`, `/api/*` (IRIS) and `/mavis/*` (MAVIS)
all appear under one port instead of three separate ones. Neither backend
knows this exists; either can be restarted/down without taking the other
or this process down. See docs/INTEGRATION_PLAN.md §4a.

    /            -> frontend/dist (built React shell), SPA fallback to index.html
    /api/*       -> IRIS backend (strip /api)   default http://localhost:8000
    /mavis/*     -> MAVIS          (strip /mavis) default http://localhost:6945
    /health      -> aggregates both backends' health, for the launcher +
                    the dashboard's own "service unavailable" banners

Run: uvicorn proxy.app:app --port 3000
Env: FRONTEND_DIST, IRIS_BACKEND_URL, MAVIS_BACKEND_URL (see defaults below).
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx
import websockets
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIST = Path(os.environ.get("FRONTEND_DIST", REPO_ROOT / "frontend" / "dist"))
IRIS_BACKEND = os.environ.get("IRIS_BACKEND_URL", "http://localhost:8000")
MAVIS_BACKEND = os.environ.get("MAVIS_BACKEND_URL", "http://localhost:6945")

# Headers that are per-hop, not meant to be forwarded verbatim through a proxy.
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host",
}

_client = httpx.AsyncClient()
_STREAM_ABOVE_BYTES = 8 * 1024 * 1024      # request bodies larger than this are streamed to the backend
_UPLOAD_TIMEOUT_S = 900.0                  # ...and given this long
_SOP_TIMEOUT_S = 180.0                     # saving / re-checking SOP rules (everything else: 30 s)


def _to_ws_url(http_url: str) -> str:
    return http_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)


async def _proxy_http(request, target_base: str, strip_prefix: str) -> Response:
    path = request.url.path[len(strip_prefix):] or "/"
    url = target_base.rstrip("/") + path
    if request.url.query:
        url += "?" + request.url.query
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
    # Big uploads (a 300 MB handbook PDF for /mavis/sop/drafts) are passed
    # through as a stream with a long timeout instead of being held in memory
    # and cut off after 30 s. Everything else is buffered as before.
    try:
        size = int(request.headers.get("content-length") or 0)
    except ValueError:
        size = 0
    big = size > _STREAM_ABOVE_BYTES
    try:
        if big:
            upstream = await _client.request(request.method, url, headers=headers, content=request.stream(),
                                             timeout=httpx.Timeout(_UPLOAD_TIMEOUT_S, connect=10.0))
        else:
            # saving a rule set writes a whole document to the database: if the proxy gave up first, the
            # page said "took too long" although MAVIS finished the save a moment later
            slow = _SOP_TIMEOUT_S if path.startswith("/sop/") and request.method != "GET" else 30.0
            upstream = await _client.request(request.method, url, headers=headers, content=await request.body(),
                                             timeout=slow)
    except httpx.ConnectError:
        return JSONResponse({"detail": f"{strip_prefix.strip('/')} backend unavailable"}, status_code=502)
    except httpx.TimeoutException:
        return JSONResponse({"detail": f"{strip_prefix.strip('/')} backend took too long to answer"}, status_code=504)
    resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP}
    return Response(content=upstream.content, status_code=upstream.status_code, headers=resp_headers)


async def _proxy_stream(request, target_base: str, strip_prefix: str) -> Response:
    """Endless responses (the Live Monitor's MJPEG screen feeds) can't be
    buffered like _proxy_http does -- pass them through chunk by chunk, and
    close the upstream when the viewer goes away."""
    url = target_base.rstrip("/") + request.url.path[len(strip_prefix):]
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
    try:
        upstream = await _client.send(
            _client.build_request("GET", url, headers=headers, timeout=httpx.Timeout(None, connect=5.0)),
            stream=True)
    except httpx.ConnectError:
        return JSONResponse({"detail": f"{strip_prefix.strip('/')} backend unavailable"}, status_code=502)

    async def body():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()

    resp_headers = {k: v for k, v in upstream.headers.items()
                    if k.lower() not in _HOP_BY_HOP and k.lower() != "content-length"}
    return StreamingResponse(body(), status_code=upstream.status_code, headers=resp_headers)


async def api_proxy(request):
    if request.method == "GET" and request.url.path.startswith("/api/capture/") and request.url.path.endswith(".mjpg"):
        return await _proxy_stream(request, IRIS_BACKEND, "/api")
    return await _proxy_http(request, IRIS_BACKEND, "/api")


async def mavis_proxy(request):
    return await _proxy_http(request, MAVIS_BACKEND, "/mavis")


async def _ws_proxy(websocket: WebSocket, target_base: str, strip_prefix: str) -> None:
    path = websocket.url.path[len(strip_prefix):] or "/"
    target = _to_ws_url(target_base).rstrip("/") + path
    if websocket.url.query:
        target += "?" + websocket.url.query
    try:
        # No message size cap (the library's default is 1 MB): the Live page's
        # first message is the whole flight so far, several MB a few minutes in.
        # Over the cap the connection just died, so coming back to the Live
        # page mid-flight showed no timeline / ground track.
        upstream = await websockets.connect(target, open_timeout=5, max_size=None)
    except Exception:
        await websocket.close(code=1011)
        return
    await websocket.accept()

    async def client_to_upstream():
        try:
            while True:
                msg = await websocket.receive_text()
                await upstream.send(msg)
        except WebSocketDisconnect:
            pass

    async def upstream_to_client():
        try:
            async for msg in upstream:
                await websocket.send_text(msg)
        except Exception:
            pass

    try:
        done, pending = await asyncio.wait(
            [asyncio.create_task(client_to_upstream()), asyncio.create_task(upstream_to_client())],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
    finally:
        await upstream.close()


async def api_ws_proxy(websocket: WebSocket):
    await _ws_proxy(websocket, IRIS_BACKEND, "/api")


async def mavis_ws_proxy(websocket: WebSocket):
    await _ws_proxy(websocket, MAVIS_BACKEND, "/mavis")


async def health(request):
    async def ok(url: str) -> bool:
        try:
            r = await _client.get(url, timeout=3.0)
            return r.status_code < 500
        except Exception:
            return False

    iris_ok, mavis_ok = await asyncio.gather(ok(IRIS_BACKEND + "/health"), ok(MAVIS_BACKEND + "/"))
    status = "ok" if (iris_ok and mavis_ok) else "degraded"
    return JSONResponse({"status": status, "iris": iris_ok, "mavis": mavis_ok})


routes = [
    Route("/health", health),
    # Specific routes first -- Mount("/") below is a catch-all and must lose
    # to these when both could match the same path.
    Route("/api/{path:path}", api_proxy, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]),
    WebSocketRoute("/api/{path:path}", api_ws_proxy),
    Route("/mavis/{path:path}", mavis_proxy, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]),
    WebSocketRoute("/mavis/{path:path}", mavis_ws_proxy),
]

class SPAStaticFiles(StaticFiles):
    """Serves frontend/dist, falling back to index.html for any path that
    isn't a real file -- /monitor, /live/freeflight, /sessions/<id>/review
    etc. only exist as React Router routes, so without this a browser
    refresh (or opening such a link directly) 404s. Paths that look like a
    file (have an extension, e.g. a missing /assets/x.js) still 404."""

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as e:
            if e.status_code == 404 and "." not in path.rsplit("/", 1)[-1]:
                return await super().get_response("index.html", scope)
            raise


if FRONTEND_DIST.exists():
    routes.append(Mount("/", app=SPAStaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend"))

app = Starlette(routes=routes)
