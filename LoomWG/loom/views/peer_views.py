"""Auto-extracted from cli/__init__.py"""
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ..wireguard.peer_manager import Peer, PeerManager
from ..wireguard.client_config import ClientConfigStore
from ..cli.common import clear_screen, pause, menu_option, selected_interface, confirm, THEME

console = Console()


def list_peers() -> None:
    """List all peers."""
    clear_screen()

    try:
        peer_mgr = PeerManager()
        peers = peer_mgr.list_peers()

        if not peers:
            console.print(f"[yellow]No peers configured[/]")
            pause()
            return

        console.print(peer_table(peers))
    except Exception as e:
        console.print(f"[{THEME['ERROR']}]Error: {e}[/]")

    pause()


def peer_table(peers: list[Peer]) -> Table:
    """Build a concise peer table used before peer selection actions."""
    table = Table(title="WireGuard Peers", border_style=THEME['PRIMARY'])

    table.add_column("Name", style=THEME['INFO'])
    table.add_column("IPv4", style=THEME['PRIMARY'])
    table.add_column("IPv6", style=THEME['PRIMARY'])
    table.add_column("RX", style=THEME['SUCCESS'])
    table.add_column("TX", style=THEME['SUCCESS'])
    table.add_column("Status", style=THEME['WARNING'])
    table.add_column("Created", style=THEME['DIM'])

    for peer in peers:
        status = f"[{THEME['SUCCESS']}]Enabled[/]" if peer.enabled else f"[{THEME['WARNING']}]Disabled[/]"
        created = peer.created_at.split("T")[0]
        rx = f"{peer.transfer_rx} B"
        tx = f"{peer.transfer_tx} B"

        table.add_row(peer.name, peer.ipv4_address, peer.ipv6_address, rx, tx, status, created)
    return table


def show_peer_selection(peer_mgr: PeerManager) -> None:
    """Display all peers before prompting for a peer name."""
    peers = peer_mgr.list_peers()
    if peers:
        console.print(peer_table(peers))
        console.print()
    else:
        console.print(f"[yellow]No peers configured[/]")


def _format_bytes(value: int) -> str:
    """Format bytes to human-readable."""
    if value < 1024:
        return f"{value} B"
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024
        if value < 1024:
            return f"{value:.1f} {unit}"
    return f"{value:.1f} PB"


def show_peer() -> None:
    """Show peer details as a structured tree view."""
    clear_screen()

    try:
        peer_mgr = PeerManager()
        show_peer_selection(peer_mgr)

        try:
            name = input("Peer name: ").strip()

        except (EOFError, KeyboardInterrupt, OSError, UnicodeDecodeError):
            console.print(f"[{THEME['ERROR']}]Input interrupted.[/]")
            pause()
            return

        if not name:
            return

        peer = peer_mgr.get_peer(name)

        if not peer:
            console.print(f"[{THEME['ERROR']}]Peer not found[/]")
            pause()
            return

        from rich.tree import Tree
        from rich.text import Text

        status_color = THEME['SUCCESS'] if peer.enabled else THEME['WARNING']
        tree = Tree(
            Text.assemble(
                "Peer: ", (peer.name, f"bold {THEME['PRIMARY']}"),
                " | ",
                (f"{'Enabled' if peer.enabled else 'Disabled'}", status_color),
            )
        )

        # Network section
        net_branch = tree.add(f"[bold {THEME['PRIMARY']}]Network[/bold {THEME['PRIMARY']}]")
        net_branch.add(f"IPv4: {peer.ipv4_address}")
        net_branch.add(f"IPv6: {peer.ipv6_address}")
        if peer.endpoint:
            net_branch.add(f"Endpoint: {peer.endpoint}")
        net_branch.add(f"Public Key: {peer.public_key}")

        # Traffic section
        traffic_branch = tree.add(f"[bold {THEME['PRIMARY']}]Traffic[/bold {THEME['PRIMARY']}]")
        traffic_branch.add(f"RX: {_format_bytes(peer.transfer_rx)}")
        traffic_branch.add(f"TX: {_format_bytes(peer.transfer_tx)}")

        # Status section
        status_branch = tree.add(f"[bold {THEME['PRIMARY']}]Status[/bold {THEME['PRIMARY']}]")
        status_branch.add(f"Created: {peer.created_at.split('T')[0]}")
        status_branch.add(f"Expiry: {peer.expires_at or 'Never'}")
        status_branch.add(f"Revoked: {peer.revoked_at or 'No'}")

        # Handshake
        if peer.latest_handshake:
            status_branch.add(f"Last Handshake: {peer.latest_handshake}")

        # Traffic history
        if peer.traffic_history:
            history_branch = tree.add(f"[bold {THEME['PRIMARY']}]Traffic History[/bold {THEME['PRIMARY']}] ({len(peer.traffic_history)} samples)")
            for sample in peer.traffic_history[-5:]:
                history_branch.add(
                    f"{sample['timestamp'][:19]}: RX {_format_bytes(sample['rx'])} / TX {_format_bytes(sample['tx'])}"
                )

        console.print(tree)
    except Exception as e:
        console.print(f"[{THEME['ERROR']}]Error: {e}[/]")

    pause()


