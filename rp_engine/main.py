"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import logging
import socket
import time
import traceback
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from rp_engine import __version__
from rp_engine.config import get_config
from rp_engine.container import ServiceContainer

logger = logging.getLogger(__name__)


class _HealthLogFilter(logging.Filter):
    """Suppress repeated /health access-log entries after the first one."""

    def __init__(self) -> None:
        super().__init__()
        self._seen_first = False

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if "/health" not in msg:
            return True
        if not self._seen_first:
            self._seen_first = True
            return True
        return False


def _get_lan_ip() -> str:
    """Detect the machine's LAN IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        return "127.0.0.1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    config = get_config()

    # Suppress repeated /health access-log noise
    logging.getLogger("uvicorn.access").addFilter(_HealthLogFilter())

    services = await ServiceContainer.build(config)
    await services.start()
    app.state.services = services

    # Individual attrs for backward compatibility with dependencies.py
    for field in ServiceContainer.__dataclass_fields__:
        setattr(app.state, field, getattr(services, field))

    yield

    await services.close()


app = FastAPI(
    title="RP Engine",
    description="REST API for managing roleplay sessions",
    version=__version__,
    lifespan=lifespan,
)

# CORS — configurable origins (defaults to localhost only)
from starlette.middleware.cors import CORSMiddleware  # noqa: E402

_config = get_config()
_cors_origins = list(_config.server.cors_origins)
if _config.server.lan_access:
    _lan_ip = _get_lan_ip()
    _cors_origins.append(f"http://{_lan_ip}:{_config.server.port}")
    _cors_origins.append(f"http://{_lan_ip}:5173")
    logger.info("LAN access enabled — added origins for %s", _lan_ip)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Request logging middleware — logs every request when diagnostics enabled
class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        diag = getattr(getattr(request.app.state, "services", None), "diagnostic_logger", None)
        if diag is None or not diag.enabled:
            return await call_next(request)

        start = time.perf_counter()
        try:
            response = await call_next(request)
            elapsed_ms = (time.perf_counter() - start) * 1000
            diag.log(
                category="request",
                event="request_complete",
                data={
                    "method": request.method,
                    "path": str(request.url.path),
                    "query": str(request.url.query) if request.url.query else None,
                    "status_code": response.status_code,
                    "elapsed_ms": round(elapsed_ms, 1),
                },
            )
            return response
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            diag.log_error(
                event="request_error",
                data={
                    "method": request.method,
                    "path": str(request.url.path),
                    "elapsed_ms": round(elapsed_ms, 1),
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
                content={"traceback": traceback.format_exc()},
            )
            # Auto-report on error if configured
            if diag.config.auto_report.enabled and diag.config.auto_report.on_error:
                _report_task = asyncio.create_task(diag.send_report())  # noqa: RUF006
            raise


app.add_middleware(RequestLoggingMiddleware)

# Include routers
from rp_engine.routers import (  # noqa: E402
    agent_chat,
    analyze,
    annotations,
    bookmarks,
    branches,
    cards,
    chat,
    config,
    context,
    continuity,
    custom_state,
    diagnostics,
    exchanges,
    lorebook,
    npc,
    openai_compat,
    prompt,
    rp,
    sessions,
    state,
    threads,
    timeline,
    triggers,
    vectors,
    writing,
)

app.include_router(cards.router)
app.include_router(sessions.router)
app.include_router(exchanges.router)
app.include_router(bookmarks.router)
app.include_router(bookmarks.list_router)
app.include_router(annotations.router)
app.include_router(annotations.top_router)
app.include_router(rp.router)
app.include_router(context.router)
app.include_router(prompt.router)
app.include_router(lorebook.router)
app.include_router(triggers.router)
app.include_router(npc.router)
app.include_router(state.router)
app.include_router(threads.router)
app.include_router(analyze.router)
app.include_router(branches.router)
app.include_router(writing.router)
app.include_router(config.router)
app.include_router(vectors.router)
app.include_router(timeline.router)
app.include_router(custom_state.router)
app.include_router(chat.router)
app.include_router(continuity.router)
app.include_router(diagnostics.router)
app.include_router(openai_compat.router)
app.include_router(agent_chat.router)


@app.get("/health")
async def health_check():
    config = get_config()
    db = app.state.db if hasattr(app.state, "db") else None
    db_health = await db.health() if db else {"status": "not_initialized"}

    # Add indexed card count
    indexed = None
    if db:
        indexed = await db.fetch_val("SELECT COUNT(*) FROM story_cards")

    return {
        "status": "ok",
        "version": __version__,
        "vault_root": str(config.paths.vault_root),
        "database": db_health,
        "indexed_cards": indexed,
        "timestamp": datetime.now(UTC).isoformat(),
    }


# Serve frontend static build (must be AFTER all API routes)
from fastapi.staticfiles import StaticFiles as _StaticFiles  # noqa: E402
from starlette.responses import FileResponse as _FileResponse  # noqa: E402


class _SPAStaticFiles(_StaticFiles):
    """StaticFiles subclass that sets no-cache on HTML (SPA shell) responses."""

    async def get_response(self, path: str, scope) -> _FileResponse:  # type: ignore[override]
        response = await super().get_response(path, scope)
        # Don't cache HTML — it references hashed JS/CSS chunks that change on rebuild
        ct = response.headers.get("content-type", "")
        if "text/html" in ct:
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response


_FRONTEND_BUILD = Path(__file__).parent.parent / "frontend" / "build"
if _FRONTEND_BUILD.exists():
    app.mount("/", _SPAStaticFiles(directory=_FRONTEND_BUILD, html=True), name="frontend")
