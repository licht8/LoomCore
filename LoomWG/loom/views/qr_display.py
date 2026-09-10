"""View functions for QR code display."""
from rich.console import Console

from ..wireguard.client_config import ClientConfigStore
from ..wireguard.peer_manager import PeerManager
from ..wireguard.config_generator import ConfigGenerator
from ..cli.common import clear_screen, display_peer_qr_code, pause, section_banner
from ..views.peer_views import show_peer_selection

console = Console()


def show_qr_code() -> None:
    """Display a saved peer config as a terminal QR code."""
    clear_screen()
    section_banner("Show QR Code", "Display a saved peer configuration as QR code")

    peer_mgr = PeerManager()
    show_peer_selection(peer_mgr)
    try:
        name = input("Peer name: ").strip()

    except (EOFError, KeyboardInterrupt, OSError):
        console.print("[red]Input interrupted.[/]")
        pause()
        return

    if not name:
        return
    peer = peer_mgr.get_peer(name)
    if not peer:
        console.print("[red]Peer not found[/]")
        pause()
        return
    config_path = ClientConfigStore().base_dir / f"{name}.conf"
    if not config_path.exists():
        console.print("[yellow]No saved client config exists for this peer.[/]")
        pause()
        return
    display_peer_qr_code(name, config_path.read_text(encoding="utf-8"))
    pause()