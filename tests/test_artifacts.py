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


def test_build_artifacts_from_batch_clients():
    result = _build_artifacts(
        {
            "data": {
                "clients": [
                    {"name": "peer-abc", "config_token": "c1", "qr_token": "q1"},
                    {"name": "peer-def", "config_token": "c2", "qr_token": "q2"},
                ]
            }
        }
    )

    assert result == [
        {"label": "Конфигурация: peer-abc", "url": "/api/artifacts/c1/config"},
        {"label": "QR-код: peer-abc", "url": "/api/artifacts/q1/qr"},
        {"label": "Конфигурация: peer-def", "url": "/api/artifacts/c2/config"},
        {"label": "QR-код: peer-def", "url": "/api/artifacts/q2/qr"},
    ]


def test_build_artifacts_batch_partial_tokens():
    result = _build_artifacts(
        {"data": {"clients": [{"name": "peer-abc", "config_token": "c1"}]}}
    )

    assert result == [
        {"label": "Конфигурация: peer-abc", "url": "/api/artifacts/c1/config"}
    ]
    assert _build_artifacts({"data": {"clients": []}}) is None
