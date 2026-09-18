"""Tests for chat artifact link building and artifact downloads."""

import base64
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request as StarletteRequest

from src.api import artifacts as artifacts_api
from src.config import settings
from src.interfaces.websocket import _build_artifacts


def test_build_artifacts_from_single_client_result():
    result = _build_artifacts(
        {"data": {"name": "alice", "client_config": "/x/alice.conf", "qr_code": "/x/alice.png"}}
    )

    assert result == [
        {"label": "Configuration: alice", "url": "/api/artifacts/alice/config"},
        {"label": "QR code: alice", "url": "/api/artifacts/alice/qr"},
    ]


def test_build_artifacts_none_without_artifacts():
    assert _build_artifacts({"data": {"foo": "bar"}}) is None
    assert _build_artifacts("not-a-dict") is None
    assert _build_artifacts({"data": None}) is None
    assert _build_artifacts({"data": {"name": "alice"}}) is None


def test_build_artifacts_from_batch_clients():
    result = _build_artifacts(
        {
            "data": {
                "clients": [
                    {"name": "peer-abc", "config_ready": True, "qr_ready": True},
                    {"name": "peer-def", "config_ready": True, "qr_ready": False},
                ]
            }
        }
    )

    assert result == [
        {"label": "Configuration: peer-abc", "url": "/api/artifacts/peer-abc/config"},
        {"label": "QR code: peer-abc", "url": "/api/artifacts/peer-abc/qr"},
        {"label": "Configuration: peer-def", "url": "/api/artifacts/peer-def/config"},
    ]


def test_build_artifacts_quotes_names():
    result = _build_artifacts(
        {"data": {"name": "peer/a b", "client_config": "x"}}
    )

    assert result == [
        {"label": "Configuration: peer/a b", "url": "/api/artifacts/peer%2Fa%20b/config"}
    ]
    assert _build_artifacts({"data": {"clients": []}}) is None


def _patch_auth(monkeypatch):
    monkeypatch.setattr(
        artifacts_api, "require_admin_token_from_request", lambda *a, **k: None
    )


def _make_admin_request(token=None):
    headers = []
    if token is not None:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    return StarletteRequest(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/artifacts/alice/config",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("test", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )


@pytest.mark.asyncio
async def test_download_artifact_requires_admin_token(monkeypatch):
    # Unlike the tests above, auth is NOT patched here: the real guard must
    # reject an unauthenticated request before any plugin is consulted.
    monkeypatch.setattr(settings, "auth_mode", "token")
    monkeypatch.setattr(settings, "admin_token", "secret")

    async def _boom(*args, **kwargs):
        raise AssertionError("plugin_manager must not be touched without auth")

    monkeypatch.setattr(
        artifacts_api, "plugin_manager", SimpleNamespace(execute_internal_tool=_boom)
    )

    with pytest.raises(HTTPException) as exc:
        await artifacts_api.download_artifact(
            "alice", "config", _make_admin_request(token=None)
        )

    assert exc.value.status_code in {401, 403}


@pytest.mark.asyncio
async def test_download_artifact_returns_file(monkeypatch):
    class _FakeManager:
        async def execute_internal_tool(self, tool_name, arguments):
            assert tool_name == "wg_get_client_artifact"
            assert arguments == {"name": "alice", "kind": "config"}
            return {
                "data": {
                    "filename": "alice.conf",
                    "media_type": "text/plain",
                    "content_base64": base64.b64encode(b"[Interface]\n").decode(),
                }
            }

    _patch_auth(monkeypatch)
    monkeypatch.setattr(artifacts_api, "plugin_manager", _FakeManager())

    response = await artifacts_api.download_artifact("alice", "config", None)

    assert response.status_code == 200
    assert response.body == b"[Interface]\n"
    assert response.media_type == "text/plain"


@pytest.mark.asyncio
async def test_download_artifact_reports_reason(monkeypatch):
    class _FailingManager:
        async def execute_internal_tool(self, tool_name, arguments):
            raise ValueError("Client 'alice' not found")

    _patch_auth(monkeypatch)
    monkeypatch.setattr(artifacts_api, "plugin_manager", _FailingManager())

    response = await artifacts_api.download_artifact("alice", "config", None)

    assert response.status_code == 404
    assert json.loads(response.body)["reason"] == "Client 'alice' not found"
