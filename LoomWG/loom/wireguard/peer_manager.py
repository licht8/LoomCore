"""WireGuard peer management."""
from rich.console import Console
console = Console()
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .validation import AllowedIPValidationManager
from .interfaces import DEFAULT_INTERFACE, get_selected_interface


def _project_root() -> Path:
    """Return the workspace root containing this app."""
    return Path(__file__).resolve().parents[2]


@dataclass
class Peer:
    """A WireGuard peer."""

    name: str
    ipv4_address: str
    ipv6_address: str
    public_key: str
    private_key: str = ""  # Should not persist normally
    preshared_key: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    enabled: bool = True
    description: str = ""
    endpoint: str | None = None
    latest_handshake: str | None = None
    transfer_rx: int = 0
    transfer_tx: int = 0
    expires_at: str | None = None
    revoked_at: str | None = None
    traffic_history: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary (without private key)."""
        d = asdict(self)
        d.pop("private_key", None)  # Never serialize private key
        return d


class PeerManager:
    """Manage WireGuard peers."""

    def __init__(self, peers_db_path: Path | None = None, interface_name: str | None = None):
        """Initialize peer manager."""
        interface_name = interface_name or get_selected_interface()
        if peers_db_path is None:
            if interface_name == DEFAULT_INTERFACE:
                peers_db_path = _project_root() / "data" / "peers.json"
            else:
                peers_db_path = (
                    _project_root()
                    / "data"
                    / "interfaces"
                    / interface_name
                    / "peers.json"
                )

        self.db_path = peers_db_path
        self.peers: dict[str, Peer] = {}
        self.load()

    def load(self) -> None:
        """Load peers from database."""
        if not self.db_path.exists():
            self.peers = {}
            return

        try:
            data = json.loads(self.db_path.read_text())

            self.peers = {}

            for name, peer_data in data.items():
                # Preserve private key when in-memory use is needed for config export.
                self.peers[name] = Peer(**peer_data)
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/]")
            self.peers = {}

    def save(self) -> bool:
        """Save peers to database."""
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

            data = {name: peer.to_dict() for name, peer in self.peers.items()}

            self.db_path.write_text(json.dumps(data, indent=2))
            self.db_path.chmod(0o600)

            return True
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/]")
            return False

    def add_peer(self, peer: Peer) -> bool:
        """Add a new peer."""
        if peer.name in self.peers:
            return False

        self.peers[peer.name] = peer
        return self.save()

    def get_peer(self, name: str) -> Peer | None:
        """Get a peer by name."""
        return self.peers.get(name)

    def remove_peer(self, name: str) -> bool:
        """Remove a peer."""
        if name not in self.peers:
            return False

        del self.peers[name]
        return self.save()

    def list_peers(self) -> list[Peer]:
        """Get all peers."""
        return list(self.peers.values())

    def list_enabled_peers(self) -> list[Peer]:
        """Get enabled peers that are not revoked."""
        return [p for p in self.peers.values() if p.enabled and not p.revoked_at]

    def list_active_peers(self) -> list[Peer]:
        """Get peers that are enabled and not revoked."""
        return [p for p in self.peers.values() if p.enabled and not p.revoked_at]

    def list_allocated_ips(self) -> tuple[set[str], set[str]]:
        """Return ``(ipv4_set, ipv6_set)`` for every peer in the database.

        Each entry includes the network prefix (``/32`` for IPv4, ``/128`` for
        IPv6) so callers can compare directly against the values stored in
        ``peers.json``. Peers missing either address are skipped for that
        family.
        """
        ipv4: set[str] = set()
        ipv6: set[str] = set()
        for peer in self.peers.values():
            if peer.ipv4_address:
                ipv4.add(peer.ipv4_address)
            if peer.ipv6_address:
                ipv6.add(peer.ipv6_address)
        return ipv4, ipv6

    def update_peer(self, name: str, peer: Peer) -> bool:
        """Update an existing peer."""
        if name not in self.peers:
            return False

        self.peers[name] = peer
        return self.save()

    def enable_peer(self, name: str) -> bool:
        """Enable a peer."""
        if name not in self.peers:
            return False

        if self.peers[name].revoked_at is not None:
            return False

        self.peers[name].enabled = True
        return self.save()

    def disable_peer(self, name: str) -> bool:
        """Disable a peer without revoking it."""
        if name not in self.peers:
            return False

        self.peers[name].enabled = False
        self.peers[name].revoked_at = None
        return self.save()

    def revoke_peer(self, name: str) -> bool:
        """Mark a peer as revoked without deleting its historical metadata."""
        if name not in self.peers:
            return False
        peer = self.peers[name]
        peer.enabled = False
        peer.revoked_at = datetime.now().isoformat()
        return self.save()

    def rotate_peer_keys(self, name: str, new_peer: Peer) -> bool:
        """Replace the peer object while preserving its historical metadata."""
        if name not in self.peers:
            return False
        old_peer = self.peers[name]
        new_peer.created_at = old_peer.created_at
        new_peer.enabled = old_peer.enabled and not old_peer.revoked_at
        new_peer.description = old_peer.description
        new_peer.endpoint = old_peer.endpoint
        new_peer.latest_handshake = old_peer.latest_handshake
        new_peer.transfer_rx = old_peer.transfer_rx
        new_peer.transfer_tx = old_peer.transfer_tx
        new_peer.expires_at = old_peer.expires_at
        new_peer.revoked_at = old_peer.revoked_at
        new_peer.traffic_history = list(old_peer.traffic_history)
        self.peers[name] = new_peer
        return self.save()

    def set_expiry(self, name: str, expires_at: str | None) -> bool:
        if name not in self.peers:
            return False
        self.peers[name].expires_at = expires_at
        return self.save()

    def record_traffic(self, public_key: str, rx: int, tx: int) -> bool:
        peer = self.get_peer_by_public_key(public_key)
        if peer is None:
            return False
        peer.transfer_rx, peer.transfer_tx = rx, tx
        peer.traffic_history.append({"timestamp": datetime.now().isoformat(), "rx": rx, "tx": tx})
        peer.traffic_history = peer.traffic_history[-100:]
        return self.save()

    def peer_exists(self, name: str) -> bool:
        """Check if peer exists."""
        return name in self.peers

    def ip_used(self, ipv4: str | None = None, ipv6: str | None = None) -> bool:
        """Check if an IP is already used."""
        for peer in self.peers.values():
            if ipv4 and peer.ipv4_address == ipv4:
                return True

            if ipv6 and peer.ipv6_address == ipv6:
                return True

        return False

    def get_peer_by_public_key(self, public_key: str) -> Peer | None:
        """Get peer by public key."""
        for peer in self.peers.values():
            if peer.public_key == public_key:
                return peer

        return None

    def update_peer_stats(
        self,
        name: str,
        endpoint: str | None = None,
        latest_handshake: str | None = None,
        transfer_rx: int | None = None,
        transfer_tx: int | None = None,
    ) -> bool:
        """Update peer statistics."""
        if name not in self.peers:
            return False

        peer = self.peers[name]

        if endpoint is not None:
            peer.endpoint = endpoint

        if latest_handshake is not None:
            peer.latest_handshake = latest_handshake

        if transfer_rx is not None:
            peer.transfer_rx = transfer_rx

        if transfer_tx is not None:
            peer.transfer_tx = transfer_tx

        return self.save()

    def validate_peer_configuration(
        self,
        peer: Peer,
        vpn_networks: list[str] | None = None,
        server_addresses: list[str] | None = None,
    ) -> object:
        """Validate a peer against the configured VPN and peer state."""
        existing = [p for p in self.peers.values() if p.name != peer.name]
        manager = AllowedIPValidationManager(vpn_networks or ["10.66.66.0/24", "fd42:42:42::/64"])
        allowed_ips = [peer.ipv4_address, peer.ipv6_address]
        return manager.validate_allowed_ips(
            allowed_ips,
            peer_name=peer.name,
            existing_peers=existing,
            server_networks=vpn_networks,
            server_addresses=server_addresses,
        )
