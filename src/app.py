"""LoomCore FastAPI application assembly."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded

from src.api import artifacts, auth, chat, health, plugins, sessions, websocket
from src.api.deps import rate_limit_exceeded_handler
from src.config import settings
from src.core.rate_limit import limiter
from src.core.startup_checks import check_security_posture
from src.core.version import CORE_VERSION
from src.plugins import plugin_manager
from src.plugins.health import healthcheck_loop

logger = logging.getLogger("loomcore.app")


def _configure_logging() -> None:
    """Make ``loomcore.*`` logs visible under uvicorn's logging config.

    Uvicorn configures only its own loggers and leaves the root logger at
    WARNING with no handler, so ``loomcore.*`` INFO records are silently
    dropped. Attach a handler to the ``loomcore`` base logger and set its
    level: INFO when DEBUG or LOOMCORE_TIMING is on, WARNING otherwise so
    normal runs stay quiet.
    """
    base = logging.getLogger("loomcore")
    if not base.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        base.addHandler(handler)
    base.setLevel(
        logging.INFO
        if (settings.debug or settings.timing_enabled)
        else logging.WARNING
    )


def _log_healthcheck_failure(task: asyncio.Task[None]) -> None:
    """Surface a background healthcheck crash that would otherwise be silent.

    Retrieving the exception here also marks it as observed, so a failed task
    does not emit an "exception was never retrieved" warning.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Background healthcheck stopped unexpectedly", exc_info=exc)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Discover plugins and run the background healthcheck for the app lifetime."""
    logger.info("LoomCore starting up")

    check_security_posture()

    discovered = plugin_manager.discover_plugins(settings.plugins_dir)
    logger.info("Plugins loaded: %s", ", ".join(discovered) or "none")

    # Prime the tool cache so plugin tool counts and schemas are available
    # immediately (discover_plugins only spawns the subprocesses).
    try:
        tools = await plugin_manager.get_all_tools()
        logger.info("Tools indexed: %d", len(tools))
    except Exception as exc:  # pragma: no cover - defensive startup guard
        logger.warning(f"Failed to prime plugin tools at startup: {exc}")

    # Start the background healthcheck every 30 seconds
    healthcheck_task = asyncio.create_task(healthcheck_loop())
    healthcheck_task.add_done_callback(_log_healthcheck_failure)
    try:
        yield
    finally:
        healthcheck_task.cancel()
        # A task that already failed was logged by its done-callback; awaiting it
        # would re-raise the stored exception and mask the shutdown reason.
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await healthcheck_task


def create_app() -> FastAPI:
    """Create and configure the application instance."""
    _configure_logging()
    app = FastAPI(
        title="LoomCore",
        version=CORE_VERSION,
        docs_url="/docs" if settings.debug else None,
        lifespan=lifespan,
    )

    # Register the 429 handler
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

    # --- CORS for the frontend (Vite/Next.js) ---
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router)
    app.include_router(health.router)
    app.include_router(plugins.router)
    app.include_router(chat.router)
    app.include_router(sessions.router)
    app.include_router(artifacts.router)
    app.include_router(websocket.router)

    return app
