"""Unit tests for the background plugin healthcheck loop."""

import asyncio

import pytest

from src.plugins import health as health_module


@pytest.mark.asyncio
async def test_healthcheck_loop_restarts_unhealthy(monkeypatch):
    restarted: list[str] = []

    class _FakeManager:
        def plugin_names(self):
            return ["demo", "ok"]

        def is_healthy(self, name):
            return name == "ok"

        async def restart_plugin(self, name):
            restarted.append(name)

    monkeypatch.setattr(health_module, "plugin_manager", _FakeManager())
    iterations = {"count": 0}

    async def fake_sleep(_seconds):
        iterations["count"] += 1
        if iterations["count"] > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(health_module.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await health_module.healthcheck_loop()

    assert restarted == ["demo"]


@pytest.mark.asyncio
async def test_healthcheck_loop_survives_restart_error(monkeypatch):
    class _FakeManager:
        def plugin_names(self):
            return ["demo"]

        def is_healthy(self, name):
            return False

        async def restart_plugin(self, name):
            raise RuntimeError("boom")

    monkeypatch.setattr(health_module, "plugin_manager", _FakeManager())
    iterations = {"count": 0}

    async def fake_sleep(_seconds):
        iterations["count"] += 1
        if iterations["count"] > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(health_module.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await health_module.healthcheck_loop()

    assert iterations["count"] == 2
