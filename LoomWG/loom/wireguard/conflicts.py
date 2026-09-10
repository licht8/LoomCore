"""Detect conflicts between ``peers.json`` and the on-disk WireGuard config.

Imports of server peers must never silently overwrite an existing peer
managed by the application, and a new peer created through the menu must
never collide with an IP already present in ``wg0.conf``. This module
provides the read-only helpers used by both flows.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config_generator import ConfigGenerator
from .peer_manager import PeerManager


# Pre-configured IP families to keep the parser simple.
_IPV4_MARKER = "."
_IPV6_MARKER = ":"


@dataclass(frozen=True)
class Conflict:
    """A single conflict between two peer sources.

    Attributes:
        kind: Free-form category (``"wg_vs_db_ip"``, ``"wg_internal_ip"``,
            ``"wg_vs_db_pubkey"``). Callers can switch on this to render a
            message.
        ipv4: IPv4 address with prefix (``/32``) when relevant, else empty.
        ipv6: IPv6 address with prefix (``/128``) when relevant, else empty.
        wg_public_key: Public key found in ``wg0.conf`` (12-char prefix for
            readability when rendered).
        db_name: Peer name in the database, when the conflict involves a
            known peer.
        message: Human-readable single-line summary.
    """

    kind: str
    ipv4: str
    ipv6: str
    wg_public_key: str
    db_name: str
    message: str


def _split_allowed_ips(allowed_ips: str) -> tuple[str, str]:
    """Return ``(ipv4_with_prefix, ipv6_with_prefix)`` from a config value.

    WireGuard ``AllowedIPs`` lines look like
    ``"10.66.66.2/32, fd42:42:42::2/128"``. Returns empty strings when a
    family is not present so callers can decide what to do.
    """
    ipv4 = ""
    ipv6 = ""
    for item in (part.strip() for part in allowed_ips.split(",")):
        if not item:
            continue
        if _IPV6_MARKER in item:
            ipv6 = item
        elif _IPV4_MARKER in item:
            ipv4 = item
    return ipv4, ipv6


def parse_wg_conf_ips(path: Path) -> tuple[set[str], set[str]]:
    """Return ``(ipv4_set, ipv6_set)`` for every peer block in ``wg0.conf``.

    The parser reuses :func:`ConfigGenerator.parse_server_peers` so the
    set of recognized blocks stays consistent with the rest of the
    codebase. Returns two empty sets if the file does not exist.
    """
    ipv4: set[str] = set()
    ipv6: set[str] = set()
    for entry in ConfigGenerator.parse_server_peers(path):
        entry_ipv4, entry_ipv6 = _split_allowed_ips(entry.get("allowed_ips", ""))
        if entry_ipv4:
            ipv4.add(entry_ipv4)
        if entry_ipv6:
            ipv6.add(entry_ipv6)
    return ipv4, ipv6


def parse_wg_conf_public_keys(path: Path) -> set[str]:
    """Return the set of public keys declared in ``wg0.conf`` peer blocks."""
    return {
        entry["public_key"]
        for entry in ConfigGenerator.parse_server_peers(path)
        if entry.get("public_key")
    }


def conflicts_between(
    db: PeerManager, wg_path: Path
) -> list[Conflict]:
    """Compute the conflicts between ``db`` and the on-disk config.

    The function is read-only; the caller is responsible for reacting to
    the result. Three categories are reported:

    1. ``"wg_vs_db_ip"`` — an IP allocated in ``wg0.conf`` collides with a
       peer already known to the database. Importing this peer would
       overwrite the database record.
    2. ``"wg_internal_ip"`` — the same IP appears twice inside
       ``wg0.conf`` itself. Rotation cannot resolve this safely.
    3. ``"wg_vs_db_pubkey"`` — the public key is already known to the
       database; this is informational and lets callers skip silently.
    """
    wg_ipv4, wg_ipv6 = parse_wg_conf_ips(wg_path)
    db_ipv4, db_ipv6 = db.list_allocated_ips()

    # Build name lookup for friendlier messages.
    db_by_ipv4: dict[str, str] = {
        peer.ipv4_address: peer.name
        for peer in db.list_peers()
        if peer.ipv4_address
    }
    db_by_ipv6: dict[str, str] = {
        peer.ipv6_address: peer.name
        for peer in db.list_peers()
        if peer.ipv6_address
    }

    conflicts: list[Conflict] = []

    # 1. Cross-source IP collisions.
    # When the wg0.conf block and the database peer share the same
    # public key for the same IP, the record is *consistent* (this is
    # what create_peer legitimately produces) and the IP-level
    # collision is suppressed. The wg_vs_db_pubkey branch below still
    # reports the already-imported fact for the "skip already imported"
    # code path. Edge case (TZ 030 §4.2): when wg_key is empty (malformed
    # block) we keep treating the entry as a conflict to preserve the
    # safety net.
    shared_ipv4 = wg_ipv4 & db_ipv4
    shared_ipv6 = wg_ipv6 & db_ipv6
    for ipv4 in sorted(shared_ipv4):
        wg_key = wg_key_by_ip(wg_path, ipv4)
        db_peer = db_peer_by_ip(db, ipv4)
        if wg_key and db_peer is not None and db_peer.public_key == wg_key:
            continue
        conflicts.append(
            Conflict(
                kind="wg_vs_db_ip",
                ipv4=ipv4,
                ipv6="",
                wg_public_key=_lookup_wg_key_for_ip(wg_path, ipv4),
                db_name=db_by_ipv4.get(ipv4, "?"),
                message=(
                    f"WG entry collides with peer '{db_by_ipv4.get(ipv4, '?')}' "
                    f"on {ipv4}"
                ),
            )
        )
    for ipv6 in sorted(shared_ipv6):
        wg_key = wg_key_by_ip(wg_path, "", ipv6)
        db_peer = db_peer_by_ip(db, "", ipv6)
        if wg_key and db_peer is not None and db_peer.public_key == wg_key:
            continue
        conflicts.append(
            Conflict(
                kind="wg_vs_db_ip",
                ipv4="",
                ipv6=ipv6,
                wg_public_key=_lookup_wg_key_for_ip(wg_path, "", ipv6),
                db_name=db_by_ipv6.get(ipv6, "?"),
                message=(
                    f"WG entry collides with peer '{db_by_ipv6.get(ipv6, '?')}' "
                    f"on {ipv6}"
                ),
            )
        )

    # 2. Duplicates inside wg0.conf.
    seen_ipv4: dict[str, str] = {}
    seen_ipv6: dict[str, str] = {}
    for entry in ConfigGenerator.parse_server_peers(wg_path):
        pubkey = entry.get("public_key", "")
        e_ipv4, e_ipv6 = _split_allowed_ips(entry.get("allowed_ips", ""))
        if e_ipv4:
            if e_ipv4 in seen_ipv4:
                conflicts.append(
                    Conflict(
                        kind="wg_internal_ip",
                        ipv4=e_ipv4,
                        ipv6="",
                        wg_public_key=pubkey[:12] if pubkey else "",
                        db_name="",
                        message=(
                            f"WG has two peer blocks sharing {e_ipv4} "
                            f"(also in {seen_ipv4[e_ipv4]})"
                        ),
                    )
                )
            else:
                seen_ipv4[e_ipv4] = pubkey[:12] if pubkey else ""
        if e_ipv6:
            if e_ipv6 in seen_ipv6:
                conflicts.append(
                    Conflict(
                        kind="wg_internal_ip",
                        ipv4="",
                        ipv6=e_ipv6,
                        wg_public_key=pubkey[:12] if pubkey else "",
                        db_name="",
                        message=(
                            f"WG has two peer blocks sharing {e_ipv6} "
                            f"(also in {seen_ipv6[e_ipv6]})"
                        ),
                    )
                )
            else:
                seen_ipv6[e_ipv6] = pubkey[:12] if pubkey else ""

    # 3. Already-imported public keys.
    wg_keys = parse_wg_conf_public_keys(wg_path)
    for key in sorted(wg_keys):
        peer = db.get_peer_by_public_key(key)
        if peer is not None:
            conflicts.append(
                Conflict(
                    kind="wg_vs_db_pubkey",
                    ipv4="",
                    ipv6="",
                    wg_public_key=key[:12] if key else "",
                    db_name=peer.name,
                    message=f"Peer '{peer.name}' is already imported",
                )
            )

    return conflicts


def _lookup_wg_key_for_ip(
    wg_path: Path, ipv4: str = "", ipv6: str = ""
) -> str:
    """Return the 12-char public key prefix for the first block using ``ipv4``/``ipv6``."""
    for entry in ConfigGenerator.parse_server_peers(wg_path):
        e_ipv4, e_ipv6 = _split_allowed_ips(entry.get("allowed_ips", ""))
        if ipv4 and e_ipv4 == ipv4:
            return entry.get("public_key", "")[:12]
        if ipv6 and e_ipv6 == ipv6:
            return entry.get("public_key", "")[:12]
    return ""


def wg_key_by_ip(wg_path: Path, ipv4: str = "", ipv6: str = "") -> str:
    """Return the full public key for the first ``wg0.conf`` block using ``ipv4``/``ipv6``.

    Returns an empty string when the IP is not present. Used by the
    IP-level collision check to compare keys across the two sources.
    """
    for entry in ConfigGenerator.parse_server_peers(wg_path):
        e_ipv4, e_ipv6 = _split_allowed_ips(entry.get("allowed_ips", ""))
        if ipv4 and e_ipv4 == ipv4:
            return entry.get("public_key", "")
        if ipv6 and e_ipv6 == ipv6:
            return entry.get("public_key", "")
    return ""


def db_peer_by_ip(
    db: PeerManager, ipv4: str = "", ipv6: str = ""
):
    """Return the database peer that owns ``ipv4`` or ``ipv6``, else ``None``.

    Used by the cross-source IP collision check (TZ 030 §4.1) to
    compare the public key of the database peer against the public
    key in ``wg0.conf`` for the same IP.
    """
    for peer in db.list_peers():
        if ipv4 and peer.ipv4_address == ipv4:
            return peer
        if ipv6 and peer.ipv6_address == ipv6:
            return peer
    return None
