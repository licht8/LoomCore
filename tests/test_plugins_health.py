"""Unit tests for the background plugin healthcheck loop."""

import asyncio
import logging

import pytest

from src.plugins import health as health_module


@pytest.mark.asyncio
async def test_healthcheck_loop_restarts_unhealthy(monkeypatch):
    restarted: list[str] = []

    class _FakeManager:
        def plugin_names(self):
            return ["demo", "ok"]

        def is_enabled(self, name):
            return True

        def is_healthy(self, name):
            return name == "ok"

        async def restart_plugin(self, name):
            restarted.append(name)
            return True

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

        def is_enabled(self, name):
            return True

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


@pytest.mark.asyncio
async def test_healthcheck_loop_skips_disabled(monkeypatch):
    restarted: list[str] = []

    class _FakeManager:
        def plugin_names(self):
            return ["disabled"]

        def is_enabled(self, name):
            return False

        def is_healthy(self, name):
            raise AssertionError("disabled plugins must not be health-checked")

        async def restart_plugin(self, name):
            restarted.append(name)
            return True

    monkeypatch.setattr(health_module, "plugin_manager", _FakeManager())
    iterations = {"count": 0}

    async def fake_sleep(_seconds):
        iterations["count"] += 1
        if iterations["count"] > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(health_module.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await health_module.healthcheck_loop()

    assert restarted == []


@pytest.mark.asyncio
async def test_healthcheck_loop_logs_warning_when_restart_fails(monkeypatch, caplog):
    class _FakeManager:
        def plugin_names(self):
            return ["demo"]

        def is_enabled(self, name):
            return True

        def is_healthy(self, name):
            return False

        async def restart_plugin(self, name):
            return False

    monkeypatch.setattr(health_module, "plugin_manager", _FakeManager())
    iterations = {"count": 0}

    async def fake_sleep(_seconds):
        iterations["count"] += 1
        if iterations["count"] > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(health_module.asyncio, "sleep", fake_sleep)

    with (
        caplog.at_level(logging.WARNING, logger="loomcore.plugins.health"),
        pytest.raises(asyncio.CancelledError),
    ):
        await health_module.healthcheck_loop()

    assert "could not be restarted" in caplog.text
    assert "restarted successfully" not in caplog.text


@pytest.mark.asyncio
async def test_healthcheck_loop_survives_health_query_error(monkeypatch, caplog):
    class _FakeManager:
        def plugin_names(self):
            return ["demo"]

        def is_enabled(self, name):
            return True

        def is_healthy(self, name):
            raise RuntimeError("corrupted record")

    monkeypatch.setattr(health_module, "plugin_manager", _FakeManager())
    iterations = {"count": 0}

    async def fake_sleep(_seconds):
        iterations["count"] += 1
        if iterations["count"] > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(health_module.asyncio, "sleep", fake_sleep)

    with (
        caplog.at_level(logging.ERROR, logger="loomcore.plugins.health"),
        pytest.raises(asyncio.CancelledError),
    ):
        await health_module.healthcheck_loop()

    assert iterations["count"] == 2
    assert "corrupted record" in caplog.text


@pytest.mark.asyncio
async def test_healthcheck_loop_checks_remaining_plugins_after_an_error(monkeypatch):
    restarted: list[str] = []

    class _FakeManager:
        def plugin_names(self):
            return ["bad", "good"]

        def is_enabled(self, name):
            return True

        def is_healthy(self, name):
            if name == "bad":
                raise RuntimeError("corrupted record")
            return False

        async def restart_plugin(self, name):
            restarted.append(name)
            return True

    monkeypatch.setattr(health_module, "plugin_manager", _FakeManager())
    iterations = {"count": 0}

    async def fake_sleep(_seconds):
        iterations["count"] += 1
        if iterations["count"] > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(health_module.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await health_module.healthcheck_loop()

    assert restarted == ["good"]


@pytest.mark.asyncio
async def test_healthcheck_loop_survives_enumeration_error(monkeypatch, caplog):
    class _FakeManager:
        def plugin_names(self):
            raise RuntimeError("registry corrupted")

    monkeypatch.setattr(health_module, "plugin_manager", _FakeManager())
    iterations = {"count": 0}

    async def fake_sleep(_seconds):
        iterations["count"] += 1
        if iterations["count"] > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(health_module.asyncio, "sleep", fake_sleep)

    with (
        caplog.at_level(logging.ERROR, logger="loomcore.plugins.health"),
        pytest.raises(asyncio.CancelledError),
    ):
        await health_module.healthcheck_loop()

    assert iterations["count"] == 2
    assert "Failed to enumerate plugins" in caplog.text


@pytest.mark.asyncio
async def test_healthcheck_loop_times_out_hanging_restart(monkeypatch, caplog):
    restarted: list[str] = []

    class _FakeManager:
        def plugin_names(self):
            return ["slow", "good"]

        def is_enabled(self, name):
            return True

        def is_healthy(self, name):
            return False

        async def restart_plugin(self, name):
            restarted.append(name)
            if name == "slow":
                await asyncio.Event().wait()
            return True

    monkeypatch.setattr(health_module, "plugin_manager", _FakeManager())
    monkeypatch.setattr(health_module, "RESTART_TIMEOUT_SECONDS", 0.01)
    iterations = {"count": 0}

    async def fake_sleep(_seconds):
        iterations["count"] += 1
        if iterations["count"] > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(health_module.asyncio, "sleep", fake_sleep)

    with (
        caplog.at_level(logging.ERROR, logger="loomcore.plugins.health"),
        pytest.raises(asyncio.CancelledError),
    ):
        await health_module.healthcheck_loop()

    assert restarted == ["slow", "good"]
    assert "timed out" in caplog.text
