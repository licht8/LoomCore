"""Firewall menu."""
from rich.console import Console
console = Console()

from ..cli.common import THEME, show_banner
from ..views.log_views import show_firewall_status
from ..commands.firewall_commands import start_firewall, enable_firewall, open_wg_port
from ..cli.common import show_header_info

from ..firewall.firewalld import FirewalldManager
from ..wireguard.manager import WireGuardManager
from ..wireguard.server_config import ServerConfig
from ..wireguard.interfaces import config_path

from ..logging_system.logger import LoomLogger
from ..cli.common import clear_screen, section_banner, menu_option, pause, show_header_info


def firewall_menu() -> None:
    """Firewall management menu."""
    while True:
        show_banner()
        show_header_info()

        section_banner("Firewall Menu")
        menu_option(1, "Show status", "View firewalld state and VPN port")
        print()
        menu_option(2, "Start firewalld", "Start the firewall service", "systemctl start firewalld")
        menu_option(3, "Open WireGuard port", "Allow the configured UDP port", "firewall-cmd --add-port …")
        print()
        menu_option(4, "Enable on boot", "Start firewalld automatically", "systemctl enable firewalld")
        print()
        menu_option(0, "Back")

        console.print()
        try:
            choice = input("Select option: ").strip()
        except (EOFError, KeyboardInterrupt, OSError, UnicodeDecodeError):
            console.print(f"[{THEME['ERROR']}]Input interrupted.[/]")
            pause()
            return

        if choice == "1":
            show_firewall_status()
        elif choice == "2":
            start_firewall()
        elif choice == "3":
            open_wg_port()
        elif choice == "4":
            enable_firewall()
        elif choice == "0":
            break
        else:
            console.print(f"[{THEME['WARNING']}]Invalid option.[/{THEME['WARNING']}]")
            pause()


