"""Tests for the FastAPI application assembly."""

from fastapi.middleware.cors import CORSMiddleware

from src import app as app_module
from src.config import settings


def test_cors_middleware_uses_settings_origins(monkeypatch):
    monkeypatch.setattr(settings, "cors_origins", ["https://hub.example.com"])

    app = app_module.create_app()

    entry = next(m for m in app.user_middleware if m.cls is CORSMiddleware)
    assert entry.kwargs["allow_origins"] == ["https://hub.example.com"]
