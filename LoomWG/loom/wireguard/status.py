"""WireGuard status parsing and management."""
import re
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class PeerStatus:
    """Status of a WireGuard peer."""

    public_key: str
    endpoint: str | None
    allowed_ips: str
    latest_handshake: datetime | None
    transfer_rx: int
    transfer_tx: int

    def is_online(self, timeout_seconds: int = 120) -> bool:
        """Check if peer is online based on handshake."""
        if self.latest_handshake is None:
            return False

        age = datetime.now() - self.latest_handshake
        return age.total_seconds() < timeout_seconds

    def handshake_age_str(self) -> str:
        """Get human-readable handshake age."""
        if self.latest_handshake is None:
            return "Never"

        age = datetime.now() - self.latest_handshake

        if age.total_seconds() < 60:
            return f"{int(age.total_seconds())}s ago"

        if age.total_seconds() < 3600:
            return f"{int(age.total_seconds() / 60)}m ago"

        if age.total_seconds() < 86400:
            return f"{int(age.total_seconds() / 3600)}h ago"

        return f"{int(age.total_seconds() / 86400)}d ago"

    def transfer_rx_str(self) -> str:
        """Get human-readable RX transfer."""
        return self._format_bytes(self.transfer_rx)

    def transfer_tx_str(self) -> str:
        """Get human-readable TX transfer."""
        return self._format_bytes(self.transfer_tx)

    @staticmethod
    def _format_bytes(num_bytes: int) -> str:
        """Format bytes to human-readable."""
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if num_bytes < 1024:
                return f"{num_bytes:.1f} {unit}"

            num_bytes /= 1024

        return f"{num_bytes:.1f} PB"


@dataclass
class InterfaceStatus:
    """Status of a WireGuard interface."""

    interface: str
    public_key: str
    listen_port: int
    peers: list[PeerStatus]

    def online_peer_count(self, timeout_seconds: int = 120) -> int:
        """Count online peers."""
        return sum(1 for p in self.peers if p.is_online(timeout_seconds))

    def offline_peer_count(self, timeout_seconds: int = 120) -> int:
        """Count offline peers."""
        return len(self.peers) - self.online_peer_count(timeout_seconds)


class StatusParser:
    """Parse WireGuard status output."""

    @staticmethod
    def parse_wg_show(output: str) -> list[InterfaceStatus]:
        """Parse output from 'wg show'."""
        interfaces = []
        current_interface = None
        current_peers: list[PeerStatus] = []

        for line in output.split("\n"):
            line = line.rstrip()

            if not line:
                # Empty line indicates end of interface
                if current_interface and current_peers:
                    interfaces.append(current_interface)
                current_interface = None
                current_peers = []
                continue

            # Interface line
            if not line.startswith("\t"):
                if current_interface:
                    interfaces.append(current_interface)

                parts = line.split()

                if len(parts) >= 3:
                    current_interface = InterfaceStatus(
                        interface=parts[0],
                        public_key=parts[1],
                        listen_port=int(parts[2]),
                        peers=[],
                    )
                    current_peers = []

            # Peer line (starts with tab)
            else:
                parts = line.strip().split()

                if len(parts) >= 5:
                    public_key = parts[0]
                    endpoint = parts[1] if parts[1] != "(none)" else None
                    allowed_ips = parts[2]
                    latest_handshake = parts[3]
                    transfer_rx = int(parts[4])
                    transfer_tx = int(parts[5]) if len(parts) > 5 else 0

                    # Parse latest handshake
                    handshake_dt = None

                    if latest_handshake != "(none)":
                        try:
                            ts = int(latest_handshake)
                            handshake_dt = datetime.fromtimestamp(ts)
                        except ValueError:
                            pass

                    peer = PeerStatus(
                        public_key=public_key,
                        endpoint=endpoint,
                        allowed_ips=allowed_ips,
                        latest_handshake=handshake_dt,
                        transfer_rx=transfer_rx,
                        transfer_tx=transfer_tx,
                    )

                    current_peers.append(peer)

                    if current_interface:
                        current_interface.peers = current_peers

        # Add final interface
        if current_interface:
            current_interface.peers = current_peers
            interfaces.append(current_interface)

        return interfaces
