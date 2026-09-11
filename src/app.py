"""Сборка FastAPI-приложения LoomHub Core."""

import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded

from src.api import artifacts, auth, chat, health, plugins, websocket
from src.api.deps import rate_limit_exceeded_handler
from src.config import settings
from src.core.rate_limit import limiter
from src.plugins import plugin_manager
from src.plugins.health import healthcheck_loop

logger = logging.getLogger("loomhub.app")


def create_app() -> FastAPI:
    """Создать и сконфигурировать экземпляр приложения."""
    app = FastAPI(
        title="LoomHub Core",
        version="0.0.1",
        docs_url="/docs" if settings.debug else None,
    )

    # Регистрируем обработчик 429
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

    # --- Подключение CORS для React (Vite/Next.js) ---
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router)
    app.include_router(health.router)
    app.include_router(plugins.router)
    app.include_router(chat.router)
    app.include_router(artifacts.router)
    app.include_router(websocket.router)

    @app.on_event("startup")
    async def startup():
        print("🚀 LoomHub Core starting up...")

        discovered = plugin_manager.discover_plugins(settings.plugins_dir)
        print(f"🔌 Plugins loaded: {', '.join(discovered) or 'none'}")

        # Запускаем фоновый healthcheck каждые 30 секунд
        asyncio.create_task(healthcheck_loop())

    return app
