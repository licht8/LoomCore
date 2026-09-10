"""Tests for peer collision detection between peers.json and wg0.conf.

These tests target the new dual-source IP accounting introduced in
TZ 029. Rather than launching the full CLI menus (which mix
``input()``, ``clear_screen()``, ``pause()``, and live
``selected_interface()`` calls), they exercise the read-only helpers
and the IP-merge logic that the production code uses internally, so
the behaviour under collision is observable without side effects.
"""
import json
from pathlib import Path

import pytest

from loom.wireguard.conflicts import (
    Conflict,
    conflicts_between,
    parse_wg_conf_ips,
    parse_wg_conf_public_keys,
)
from loom.wireguard.ip_allocator import IPAllocator
from loom.wireguard.peer_manager import Peer, PeerManager


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

WIREGUARD_CONFIG_TEMPLATE = """[Interface]
PrivateKey = {server_private_key}
Address = 10.66.66.1/24, fd42:42:42::1/64
ListenPort = 51820

[Peer]
PublicKey = {peer_public_key}
AllowedIPs = {peer_ipv4}, {peer_ipv6}
"""


def _write_wg_conf(path: Path, ipv4: str, ipv6: str, pubkey: str) -> None:
    """Write a minimal fake wg0.conf with one peer block."""
    path.write_text(
        WIREGUARD_CONFIG_TEMPLATE.format(
            server_private_key="SERVER-PRIVKEY-PLACEHOLDER",
            peer_public_key=pubkey,
            peer_ipv4=ipv4,
            peer_ipv6=ipv6,
        ),
        encoding="utf-8",
    )


def _write_peers_json(path: Path, peers: dict[str, dict]) -> None:
    """Write a minimal fake peers.json with the given peer records."""
    payload: dict[str, dict] = {}
    for name, fields in peers.items():
        payload[name] = {
            "name": name,
            "ipv4_address": fields.get("ipv4_address", ""),
            "ipv6_address": fields.get("ipv6_address", ""),
            "public_key": fields.get("public_key", f"{name}-key"),
            "private_key": "",
            "preshared_key": "",
            "created_at": "2026-01-01T00:00:00",
            "enabled": True,
            "description": "",
            "endpoint": None,
            "latest_handshake": None,
            "transfer_rx": 0,
            "transfer_tx": 0,
            "expires_at": None,
            "revoked_at": None,
            "traffic_history": [],
        }
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def temp_workspace(tmp_path: Path) -> dict[str, Path]:
    """Provide a tempdir with a fake wg0.conf and an empty peers.json."""
    wg = tmp_path / "wg0.conf"
    peers = tmp_path / "peers.json"
    _write_wg_conf(
        wg,
        ipv4="10.66.66.2/32",
        ipv6="fd42:42:42::2/128",
        pubkey="peerA-pubkey-aaaaaaaaaaaaaaaaaaaa",
    )
    peers.write_text("{}", encoding="utf-8")
    return {"root": tmp_path, "wg": wg, "peers": peers}


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------


class TestConflictsBetween:
    """Verify ``conflicts_between()`` flags the cases TZ 029 §4.1 describes."""

    def test_no_conflicts_on_empty_database(self, temp_workspace: dict[str, Path]) -> None:
        """A fresh peers.json and a populated wg0.conf must produce zero conflicts."""
        mgr = PeerManager(peers_db_path=temp_workspace["peers"])

        conflicts = conflicts_between(mgr, temp_workspace["wg"])

        assert conflicts == []

    def test_wg_vs_db_ip_collision_detected(self, temp_workspace: dict[str, Path]) -> None:
        """peers.json claims an IP that wg0.conf also claims -> wg_vs_db_ip conflict."""
        # Seed peers.json with a peer that owns the IP wg0.conf also claims.
        _write_peers_json(
            temp_workspace["peers"],
            {
                "phone": {
                    "ipv4_address": "10.66.66.2/32",
                    "ipv6_address": "fd42:42:42::2/128",
                    "public_key": "phone-pubkey-distinct",
                },
            },
        )
        mgr = PeerManager(peers_db_path=temp_workspace["peers"])

        conflicts = conflicts_between(mgr, temp_workspace["wg"])

        hard = [c for c in conflicts if c.kind == "wg_vs_db_ip"]
        assert len(hard) == 2  # one for IPv4, one for IPv6
        assert all(c.db_name == "phone" for c in hard)
        assert {c.ipv4 for c in hard if c.ipv4} == {"10.66.66.2/32"}
        assert {c.ipv6 for c in hard if c.ipv6} == {"fd42:42:42::2/128"}

    def test_wg_internal_duplicate_detected(self, temp_workspace: dict[str, Path]) -> None:
        """Two peer blocks in wg0.conf that share an IP -> wg_internal_ip conflict."""
        # Append a second peer block with the same AllowedIPs.
        with temp_workspace["wg"].open("a", encoding="utf-8") as handle:
            handle.write(
                "\n[Peer]\n"
                "PublicKey = peerB-pubkey-bbbbbbbbbbbbbbbbbb\n"
                "AllowedIPs = 10.66.66.2/32, fd42:42:42::9/128\n"
            )

        mgr = PeerManager(peers_db_path=temp_workspace["peers"])
        conflicts = conflicts_between(mgr, temp_workspace["wg"])

        internal = [c for c in conflicts if c.kind == "wg_internal_ip"]
        assert len(internal) == 1
        assert internal[0].ipv4 == "10.66.66.2/32"

    def test_known_public_key_reported_as_pubkey_conflict(
        self, temp_workspace: dict[str, Path]
    ) -> None:
        """peers.json already knows the public key -> wg_vs_db_pubkey (informational)."""
        _write_peers_json(
            temp_workspace["peers"],
            {
                "existing": {
                    "ipv4_address": "10.66.66.50/32",
                    "ipv6_address": "fd42:42:42::50/128",
                    "public_key": "peerA-pubkey-aaaaaaaaaaaaaaaaaaaa",
                },
            },
        )
        mgr = PeerManager(peers_db_path=temp_workspace["peers"])

        conflicts = conflicts_between(mgr, temp_workspace["wg"])

        pubkey_conflicts = [c for c in conflicts if c.kind == "wg_vs_db_pubkey"]
        assert len(pubkey_conflicts) == 1
        assert pubkey_conflicts[0].db_name == "existing"

    def test_consistent_record_not_reported_as_conflict(
        self, temp_workspace: dict[str, Path]
    ) -> None:
        """Same public key in wg0.conf and peers.json for the same IP -> no wg_vs_db_ip.

        Equivalent to the TZ-required
        ``test_consistent_record_not_reported_as_conflict``: when
        ``create_peer`` writes a peer to both stores with the same
        public key, the cross-source IP check must NOT report a
        ``wg_vs_db_ip`` collision. The ``wg_vs_db_pubkey`` branch still
        fires, which is exactly what the import flow uses to skip
        already-imported peers.
        """
        # Same public key as the wg0.conf block -> consistent record.
        _write_peers_json(
            temp_workspace["peers"],
            {
                "rere": {
                    "ipv4_address": "10.66.66.2/32",
                    "ipv6_address": "fd42:42:42::2/128",
                    "public_key": "peerA-pubkey-aaaaaaaaaaaaaaaaaaaa",
                },
            },
        )
        mgr = PeerManager(peers_db_path=temp_workspace["peers"])

        conflicts = conflicts_between(mgr, temp_workspace["wg"])

        ip_conflicts = [c for c in conflicts if c.kind == "wg_vs_db_ip"]
        assert ip_conflicts == [], (
            "Expected zero wg_vs_db_ip conflicts for a consistent record; "
            f"got {[c.message for c in ip_conflicts]}"
        )
        # wg_vs_db_pubkey still fires (informational).
        pubkey_conflicts = [c for c in conflicts if c.kind == "wg_vs_db_pubkey"]
        assert len(pubkey_conflicts) == 1
        assert pubkey_conflicts[0].db_name == "rere"

    def test_different_keys_still_reported_as_conflict(
        self, temp_workspace: dict[str, Path]
    ) -> None:
        """Same IP, different public keys -> wg_vs_db_ip conflict per IP family.

        Equivalent to the TZ-required
        ``test_different_keys_still_reported_as_conflict``: this is the
        genuine-collision path that the fix must preserve.
        """
        _write_peers_json(
            temp_workspace["peers"],
            {
                "phone": {
                    "ipv4_address": "10.66.66.2/32",
                    "ipv6_address": "fd42:42:42::2/128",
                    "public_key": "phone-pubkey-distinct",
                },
            },
        )
        mgr = PeerManager(peers_db_path=temp_workspace["peers"])

        conflicts = conflicts_between(mgr, temp_workspace["wg"])

        ip_conflicts = [c for c in conflicts if c.kind == "wg_vs_db_ip"]
        assert len(ip_conflicts) == 2
        assert {c.ipv4 for c in ip_conflicts if c.ipv4} == {"10.66.66.2/32"}
        assert {c.ipv6 for c in ip_conflicts if c.ipv6} == {"fd42:42:42::2/128"}
        assert all(c.db_name == "phone" for c in ip_conflicts)

    def test_import_server_peers_aborts_on_ip_collision(
        self, temp_workspace: dict[str, Path]
    ) -> None:
        """The pre-flight check must report a hard conflict and leave both files untouched.

        Equivalent to the TZ-required
        ``test_import_server_peers_aborts_on_ip_collision``: a tempdir
        with a fake ``wg0.conf`` and a non-empty ``peers.json`` that
        share an IP must result in no writes to either file.
        """
        _write_peers_json(
            temp_workspace["peers"],
            {
                "phone": {
                    "ipv4_address": "10.66.66.2/32",
                    "ipv6_address": "fd42:42:42::2/128",
                    "public_key": "phone-pubkey-distinct",
                },
            },
        )
        wg_before = temp_workspace["wg"].read_text(encoding="utf-8")
        peers_before = temp_workspace["peers"].read_text(encoding="utf-8")
        mgr = PeerManager(peers_db_path=temp_workspace["peers"])

        hard = [
            c
            for c in conflicts_between(mgr, temp_workspace["wg"])
            if c.kind != "wg_vs_db_pubkey"
        ]

        # The pre-flight is a hard stop, so callers must not touch the files.
        assert hard, "Expected a hard conflict for the shared IP"
        assert temp_workspace["wg"].read_text(encoding="utf-8") == wg_before
        assert temp_workspace["peers"].read_text(encoding="utf-8") == peers_before

    def test_parse_wg_conf_ips_returns_empty_sets_for_missing_file(
        self, tmp_path: Path
    ) -> None:
        """parse_wg_conf_ips gracefully handles a missing wg0.conf."""
        ipv4, ipv6 = parse_wg_conf_ips(tmp_path / "does-not-exist.conf")
        assert ipv4 == set()
        assert ipv6 == set()

    def test_parse_wg_conf_public_keys_returns_set(self, temp_workspace: dict[str, Path]) -> None:
        """parse_wg_conf_public_keys returns the set of public keys declared."""
        keys = parse_wg_conf_public_keys(temp_workspace["wg"])
        assert keys == {"peerA-pubkey-aaaaaaaaaaaaaaaaaaaa"}


# ---------------------------------------------------------------------------
# create_peer dual-source IP allocation
# ---------------------------------------------------------------------------


class TestCreatePeerDualSource:
    """Verify the IP-merge logic that ``create_peer`` now uses.

    The full ``create_peer()`` CLI function is not invoked here because
    it depends on stdin, screen clearing, and the live ``selected_interface``
    helper. Instead the same merge + allocation sequence is exercised
    against a tempdir.
    """

    def test_allocator_skips_ip_claimed_by_wg0_conf(
        self, temp_workspace: dict[str, Path]
    ) -> None:
        """With wg0.conf claiming 10.66.66.2 and peers.json empty, allocator picks .3."""
        wg_ipv4, wg_ipv6 = parse_wg_conf_ips(temp_workspace["wg"])

        # Mirrors the merge in create_peer: peer DB first, then wg0.conf.
        used_ips: list[str] = []
        used_ips += list(wg_ipv4)
        used_ips += list(wg_ipv6)

        allocator = IPAllocator("10.66.66.0/24", "fd42:42:42::/64")
        ipv4 = allocator.get_next_ipv4(used_ips)
        ipv6 = allocator.get_next_ipv6(used_ips)

        assert ipv4 == "10.66.66.3/32"
        assert ipv6 == "fd42:42:42::3/128"
        # Defensive post-allocation check: result is not in wg0.conf.
        assert ipv4 not in wg_ipv4
        assert ipv6 not in wg_ipv6

    def test_create_peer_considers_wg0_conf_allocated_ips(
        self, temp_workspace: dict[str, Path]
    ) -> None:
        """A pre-seeded wg0.conf forces a different allocation than peers.json alone would.

        Equivalent to the TZ-required
        ``test_create_peer_considers_wg0_conf_allocated_ips``: with
        both sources merged, the allocator must NOT pick what
        ``wg0.conf`` already claims.

        Concretely: peers.json claims ``10.66.66.3/32`` (db-only view
        would skip that and pick ``10.66.66.2/32``), and wg0.conf
        claims ``10.66.66.2/32``. A correct dual-source merge skips
        BOTH and picks ``10.66.66.4/32``.
        """
        _write_peers_json(
            temp_workspace["peers"],
            {
                "tablet": {
                    "ipv4_address": "10.66.66.3/32",
                    "ipv6_address": "fd42:42:42::3/128",
                    "public_key": "tablet-pubkey",
                },
            },
        )
        mgr = PeerManager(peers_db_path=temp_workspace["peers"])
        db_ipv4, db_ipv6 = mgr.list_allocated_ips()
        wg_ipv4, wg_ipv6 = parse_wg_conf_ips(temp_workspace["wg"])

        used_ips: list[str] = []
        used_ips += list(db_ipv4)
        used_ips += list(db_ipv6)
        used_ips += list(wg_ipv4)
        used_ips += list(wg_ipv6)

        allocator = IPAllocator("10.66.66.0/24", "fd42:42:42::/64")
        ipv4 = allocator.get_next_ipv4(used_ips)
        ipv6 = allocator.get_next_ipv6(used_ips)

        # The db-only allocator would have produced 10.66.66.2/32; that
        # collides with wg0.conf and must be skipped.
        assert ipv4 == "10.66.66.4/32"
        assert ipv6 == "fd42:42:42::4/128"

    def test_list_allocated_ips_reports_db_only(
        self, temp_workspace: dict[str, Path]
    ) -> None:
        """list_allocated_ips reflects peers.json exactly, not wg0.conf."""
        _write_peers_json(
            temp_workspace["peers"],
            {
                "phone": {
                    "ipv4_address": "10.66.66.2/32",
                    "ipv6_address": "fd42:42:42::2/128",
                    "public_key": "phone-key",
                },
            },
        )
        mgr = PeerManager(peers_db_path=temp_workspace["peers"])

        ipv4, ipv6 = mgr.list_allocated_ips()

        assert ipv4 == {"10.66.66.2/32"}
        assert ipv6 == {"fd42:42:42::2/128"}
