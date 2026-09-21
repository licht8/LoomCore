"""Tests for the runtime LLM provider settings routes."""

import pytest
from httpx import ASGITransport, AsyncClient

from src.api import llm as llm_api
from src.config import settings
from src.llm import config as llm_config
from src.main import app


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


_AUTH = {"Authorization": "Bearer secret"}


def _auth(monkeypatch):
    monkeypatch.setattr(settings, "admin_password_hash", "")
    monkeypatch.setattr(settings, "admin_totp_secret", "")
    monkeypatch.setattr(settings, "admin_token", "secret")


@pytest.fixture(autouse=True)
def _isolated_llm_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "llm_settings_file", str(tmp_path / "llm.json"))
    monkeypatch.setattr(settings, "llm_provider", "")
    monkeypatch.setattr(settings, "llm_base_url", "")
    monkeypatch.setattr(settings, "llm_model", "")
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(settings, "llm_enable_thinking", False)
    llm_config.clear_cache()
    yield
    llm_config.clear_cache()


def _configured_payload(provider="openai", **overrides):
    payload = {
        "provider": provider,
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_providers_list_excludes_hetzner(monkeypatch):
    _auth(monkeypatch)
    async with _client() as client:
        response = await client.get("/api/llm/providers", headers=_AUTH)

    assert response.status_code == 200
    ids = {preset["id"] for preset in response.json()["providers"]}
    assert ids == {"openai", "openrouter", "groq", "deepseek", "custom"}
    assert "hetzner" not in response.text.lower()


@pytest.mark.asyncio
async def test_settings_never_return_the_raw_key(monkeypatch):
    _auth(monkeypatch)
    async with _client() as client:
        await client.put(
            "/api/llm/settings",
            headers=_AUTH,
            json=_configured_payload(api_key="supersecretvalue"),
        )
        response = await client.get("/api/llm/settings", headers=_AUTH)

    assert response.status_code == 200
    assert "supersecretvalue" not in response.text
    body = response.json()
    assert body["api_key"] == {"configured": True, "preview": "…alue"}
    assert body["configured"] is True


@pytest.mark.asyncio
async def test_settings_default_to_unconfigured(monkeypatch):
    _auth(monkeypatch)
    async with _client() as client:
        response = await client.get("/api/llm/settings", headers=_AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is False
    assert body["provider"] == ""
    assert body["api_key"] == {"configured": False, "preview": ""}


@pytest.mark.asyncio
async def test_update_requires_admin(monkeypatch):
    monkeypatch.setattr(settings, "admin_password_hash", "")
    monkeypatch.setattr(settings, "admin_totp_secret", "")
    monkeypatch.setattr(settings, "admin_token", "secret")

    async with _client() as client:
        response = await client.put(
            "/api/llm/settings",
            headers={"Authorization": "Bearer wrong"},
            json=_configured_payload(),
        )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_update_rejects_unknown_provider(monkeypatch):
    _auth(monkeypatch)
    async with _client() as client:
        response = await client.put(
            "/api/llm/settings",
            headers=_AUTH,
            json={"provider": "not-a-provider"},
        )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_update_rejects_invalid_base_url(monkeypatch):
    _auth(monkeypatch)
    async with _client() as client:
        response = await client.put(
            "/api/llm/settings",
            headers=_AUTH,
            json={"base_url": "ftp://example.com"},
        )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_update_rejects_empty_model(monkeypatch):
    _auth(monkeypatch)
    async with _client() as client:
        response = await client.put(
            "/api/llm/settings",
            headers=_AUTH,
            json={"model": ""},
        )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_update_rejects_non_positive_timeout(monkeypatch):
    _auth(monkeypatch)
    async with _client() as client:
        response = await client.put(
            "/api/llm/settings",
            headers=_AUTH,
            json={"read_timeout": 0},
        )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_update_persists_and_is_reflected_by_get(monkeypatch):
    _auth(monkeypatch)
    async with _client() as client:
        put = await client.put(
            "/api/llm/settings",
            headers=_AUTH,
            json=_configured_payload(model="gpt-4o"),
        )
        get = await client.get("/api/llm/settings", headers=_AUTH)

    assert put.status_code == 200
    assert get.json()["provider"] == "openai"
    assert get.json()["model"] == "gpt-4o"


@pytest.mark.asyncio
async def test_provider_change_without_key_clears_the_old_key(monkeypatch):
    _auth(monkeypatch)
    async with _client() as client:
        await client.put(
            "/api/llm/settings",
            headers=_AUTH,
            json=_configured_payload(api_key="old-secret-key"),
        )
        response = await client.put(
            "/api/llm/settings",
            headers=_AUTH,
            json={
                "provider": "groq",
                "base_url": "https://api.groq.com/openai/v1",
                "model": "llama-3.3-70b-versatile",
            },
        )

    assert response.status_code == 200
    assert response.json()["api_key"] == {"configured": False, "preview": ""}


@pytest.mark.asyncio
async def test_explicit_empty_key_clears_it(monkeypatch):
    _auth(monkeypatch)
    async with _client() as client:
        await client.put(
            "/api/llm/settings",
            headers=_AUTH,
            json=_configured_payload(api_key="to-be-cleared"),
        )
        response = await client.put(
            "/api/llm/settings",
            headers=_AUTH,
            json={"api_key": ""},
        )

    assert response.status_code == 200
    assert response.json()["api_key"]["configured"] is False


@pytest.mark.asyncio
async def test_audit_record_excludes_the_api_key(monkeypatch):
    _auth(monkeypatch)
    recorded: list[dict] = []

    async def fake_audit(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(llm_api, "record_mutation_isolated", fake_audit)

    async with _client() as client:
        await client.put(
            "/api/llm/settings",
            headers=_AUTH,
            json=_configured_payload(api_key="topsecretkey"),
        )

    assert len(recorded) == 1
    assert recorded[0]["tool"] == "llm_settings_update"
    assert set(recorded[0]["arguments"]) == {"provider", "model", "base_url"}
    assert "topsecretkey" not in str(recorded)


class _FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, response):
        self._response = response
        self.requests: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None):
        self.requests.append({"url": url, "headers": headers})
        return self._response


def _patch_httpx(monkeypatch, response):
    fake = _FakeAsyncClient(response)
    monkeypatch.setattr(llm_api.httpx, "AsyncClient", lambda **kwargs: fake)
    return fake


@pytest.mark.asyncio
async def test_test_connection_unconfigured_returns_400(monkeypatch):
    _auth(monkeypatch)
    async with _client() as client:
        response = await client.post("/api/llm/test", headers=_AUTH)

    assert response.status_code == 400
    assert response.json()["detail"] == llm_config.NOT_CONFIGURED_MESSAGE


@pytest.mark.asyncio
async def test_test_connection_reports_models(monkeypatch):
    _auth(monkeypatch)
    async with _client() as client:
        await client.put(
            "/api/llm/settings",
            headers=_AUTH,
            json=_configured_payload(api_key="k"),
        )
        fake = _patch_httpx(
            monkeypatch,
            _FakeResponse(200, {"data": [{"id": "a"}, {"id": "b"}]}),
        )
        response = await client.post("/api/llm/test", headers=_AUTH)

    assert response.status_code == 200
    assert response.json() == {"ok": True, "status": 200, "models_count": 2}
    assert fake.requests[0]["url"] == "https://api.openai.com/v1/models"
    assert fake.requests[0]["headers"] == {"Authorization": "Bearer k"}


@pytest.mark.asyncio
async def test_test_connection_reports_http_failure(monkeypatch):
    _auth(monkeypatch)
    async with _client() as client:
        await client.put(
            "/api/llm/settings",
            headers=_AUTH,
            json=_configured_payload(),
        )
        _patch_httpx(monkeypatch, _FakeResponse(500))
        response = await client.post("/api/llm/test", headers=_AUTH)

    assert response.status_code == 200
    assert response.json() == {"ok": False, "status": 500, "error": "HTTP 500"}
