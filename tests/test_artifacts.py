"""Tests for chat artifact link building."""

from src.interfaces.websocket import _build_artifacts


def test_build_artifacts_from_tool_data():
    result = _build_artifacts({"data": {"config_token": "cfg", "qr_token": "qr"}})

    assert result == [
        {"label": "Скачать конфигурацию", "url": "/api/artifacts/cfg/config"},
        {"label": "Скачать QR-код", "url": "/api/artifacts/qr/qr"},
    ]


def test_build_artifacts_none_without_tokens():
    assert _build_artifacts({"data": {"foo": "bar"}}) is None
    assert _build_artifacts("not-a-dict") is None
    assert _build_artifacts({"data": None}) is None
