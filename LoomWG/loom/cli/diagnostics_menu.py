"""Diagnostics menu."""
from rich.console import Console
console = Console()

from ..cli.common import THEME, show_banner
from ..diagnostics import FirewallDiagnostics, NetworkDiagnostics, SystemDiagnostics, WireGuardDiagnostics
from ..wireguard.manager import WireGuardManager
from ..wireguard.server_config import ServerConfig
from ..wireguard.interfaces import config_path

from ..commands.diagnostics_commands import (
    run_full_diagnostics,
    run_system_diagnostics,
    run_network_diagnostics,
    run_wireguard_diagnostics,
    run_firewall_diagnostics,
)

from ..cli.common import clear_screen, section_banner, menu_option, pause, show_header_info


def diagnostics_menu() -> None:
    """Diagnostics menu."""
    while True:
        show_banner()
        show_header_info()

        section_banner("Diagnostics Menu")
        menu_option(1, "Full health check", "Run every diagnostic check")
        print()
        menu_option(2, "System diagnostics", "Check OS and service prerequisites")
        menu_option(3, "Network diagnostics", "Check routes and connectivity")
        menu_option(4, "WireGuard diagnostics", "Check the VPN interface")
        menu_option(5, "Firewall diagnostics", "Check firewall access and NAT")
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
            run_full_diagnostics()
        elif choice == "2":
            run_system_diagnostics()
        elif choice == "3":
            run_network_diagnostics()
        elif choice == "4":
            run_wireguard_diagnostics()
        elif choice == "5":
            run_firewall_diagnostics()
        elif choice == "0":
            break
        else:
            console.print(f"[{THEME['WARNING']}]Invalid option.[/{THEME['WARNING']}]")
            pause()


