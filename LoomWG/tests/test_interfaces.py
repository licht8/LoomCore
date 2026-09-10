"""Tests for multi-interface isolation and validation."""

from loom.wireguard.client_config import ClientConfigStore
from loom.wireguard.interfaces import networks_overlap, validate_interface_name
from loom.wireguard.peer_manager import PeerManager


def test_interface_names_are_restricted_to_safe_wireguard_names():
    assert validate_interface_name("wg0")
    assert validate_interface_name("wg12")
    assert not validate_interface_name("wg-0")
    assert not validate_interface_name("a" * 16)


def test_interface_network_overlap_is_version_aware():
    assert networks_overlap("10.66.66.0/24", "10.66.66.0/25")
    assert not networks_overlap("10.66.66.0/24", "10.77.77.0/24")
    assert not networks_overlap("10.66.66.0/24", "fd42:42:42::/64")


def test_non_default_interface_uses_isolated_peer_and_client_storage(tmp_path):
    peers = PeerManager(
        interface_name="wg1",
        peers_db_path=tmp_path / "interfaces" / "wg1" / "peers.json",
    )
    clients = ClientConfigStore(
        base_dir=tmp_path / "interfaces" / "wg1" / "clients",
        qr_dir=tmp_path / "interfaces" / "wg1" / "qr_codes",
        interface_name="wg1",
    )

    assert peers.db_path == tmp_path / "interfaces" / "wg1" / "peers.json"
    assert clients.base_dir == tmp_path / "interfaces" / "wg1" / "clients"
    assert clients.qr_dir == tmp_path / "interfaces" / "wg1" / "qr_codes"
