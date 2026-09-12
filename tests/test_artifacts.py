"""Tests for chat artifact link building."""

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
