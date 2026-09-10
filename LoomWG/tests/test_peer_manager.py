"""Tests for peer management."""
import json
import pytest
import tempfile
from pathlib import Path
from loom.wireguard.peer_manager import Peer, PeerManager


class TestPeerManager:
    """Test PeerManager."""

    @pytest.fixture
    def temp_db(self):
        """Create temporary database file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            db_path = Path(f.name)

        yield db_path

        if db_path.exists():
            db_path.unlink()

    def test_add_peer(self, temp_db):
        """Test adding a peer."""
        mgr = PeerManager(temp_db)

        peer = Peer(
            name="test_peer",
            ipv4_address="10.66.66.2/32",
            ipv6_address="fd42:42:42::2/128",
            public_key="test_public_key",
        )

        result = mgr.add_peer(peer)

        assert result
        assert mgr.peer_exists("test_peer")

    def test_get_peer(self, temp_db):
        """Test retrieving a peer."""
        mgr = PeerManager(temp_db)

        peer = Peer(
            name="test_peer",
            ipv4_address="10.66.66.2/32",
            ipv6_address="fd42:42:42::2/128",
            public_key="test_public_key",
        )

        mgr.add_peer(peer)

        retrieved = mgr.get_peer("test_peer")

        assert retrieved is not None
        assert retrieved.name == "test_peer"
        assert retrieved.ipv4_address == "10.66.66.2/32"

    def test_remove_peer(self, temp_db):
        """Test removing a peer."""
        mgr = PeerManager(temp_db)

        peer = Peer(
            name="test_peer",
            ipv4_address="10.66.66.2/32",
            ipv6_address="fd42:42:42::2/128",
            public_key="test_public_key",
        )

        mgr.add_peer(peer)

        assert mgr.peer_exists("test_peer")

        mgr.remove_peer("test_peer")

        assert not mgr.peer_exists("test_peer")

    def test_enable_disable_peer(self, temp_db):
        """Test enabling/disabling a peer."""
        mgr = PeerManager(temp_db)

        peer = Peer(
            name="test_peer",
            ipv4_address="10.66.66.2/32",
            ipv6_address="fd42:42:42::2/128",
            public_key="test_public_key",
        )

        mgr.add_peer(peer)

        assert mgr.get_peer("test_peer").enabled

        mgr.disable_peer("test_peer")

        assert not mgr.get_peer("test_peer").enabled

        mgr.enable_peer("test_peer")

        assert mgr.get_peer("test_peer").enabled

    def test_ip_used(self, temp_db):
        """Test IP usage checking."""
        mgr = PeerManager(temp_db)

        peer = Peer(
            name="test_peer",
            ipv4_address="10.66.66.2/32",
            ipv6_address="fd42:42:42::2/128",
            public_key="test_public_key",
        )

        mgr.add_peer(peer)

        assert mgr.ip_used(ipv4="10.66.66.2/32")
        assert mgr.ip_used(ipv6="fd42:42:42::2/128")
        assert not mgr.ip_used(ipv4="10.66.66.3/32")

    def test_persistence(self, temp_db):
        """Test that peers persist across manager instances."""
        mgr1 = PeerManager(temp_db)

        peer = Peer(
            name="test_peer",
            ipv4_address="10.66.66.2/32",
            ipv6_address="fd42:42:42::2/128",
            public_key="test_public_key",
        )

        mgr1.add_peer(peer)

        # Create new manager and verify peer is loaded
        mgr2 = PeerManager(temp_db)

        assert mgr2.peer_exists("test_peer")
        retrieved = mgr2.get_peer("test_peer")

        assert retrieved.ipv4_address == "10.66.66.2/32"

    def test_revoke_peer_marks_peer_as_revoked(self, temp_db):
        """A revoked peer should stay in the database but be excluded from active peers."""
        mgr = PeerManager(temp_db)

        peer = Peer(
            name="revoked_peer",
            ipv4_address="10.66.66.10/32",
            ipv6_address="fd42:42:42::10/128",
            public_key="revoked_key",
        )

        mgr.add_peer(peer)
        assert mgr.revoke_peer("revoked_peer")

        stored = mgr.get_peer("revoked_peer")
        assert stored is not None
        assert stored.revoked_at is not None
        assert stored.enabled is False
        assert all(item.name != "revoked_peer" for item in mgr.list_enabled_peers())
        assert not mgr.enable_peer("revoked_peer")
