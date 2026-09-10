"""Tests for WireGuard status parsing."""
import pytest
from loom.wireguard.status import StatusParser, PeerStatus


class TestStatusParser:
    """Test StatusParser."""

    def test_parse_wg_show_empty(self):
        """Test parsing empty wg show output."""
        parser = StatusParser()

        output = ""

        interfaces = parser.parse_wg_show(output)

        assert len(interfaces) == 0

    def test_parse_peer_online_status(self):
        """Test peer online status detection."""
        import time
        from datetime import datetime

        peer = PeerStatus(
            public_key="key",
            endpoint="1.2.3.4:51820",
            allowed_ips="10.66.66.2/32",
            latest_handshake=datetime.now(),
            transfer_rx=1000,
            transfer_tx=2000,
        )

        assert peer.is_online()

    def test_parse_peer_offline_status(self):
        """Test peer offline status detection."""
        from datetime import datetime, timedelta

        peer = PeerStatus(
            public_key="key",
            endpoint="1.2.3.4:51820",
            allowed_ips="10.66.66.2/32",
            latest_handshake=datetime.now() - timedelta(hours=2),
            transfer_rx=1000,
            transfer_tx=2000,
        )

        assert not peer.is_online()

    def test_format_bytes(self):
        """Test byte formatting."""
        peer = PeerStatus(
            public_key="key",
            endpoint="1.2.3.4:51820",
            allowed_ips="10.66.66.2/32",
            latest_handshake=None,
            transfer_rx=1024,
            transfer_tx=1048576,
        )

        assert "KB" in peer.transfer_rx_str()
        assert "MB" in peer.transfer_tx_str()
