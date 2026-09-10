"""Main menu router."""
from rich.console import Console
console = Console()

import sys

from ..cli.common import clear_screen, check_root, show_banner, show_header_info, menu_option, pause, selected_interface, section_banner, THEME
from .server_menu import server_menu
from .peers_menu import peers_menu
from .firewall_menu import firewall_menu
from .diagnostics_menu import diagnostics_menu
from .backup_menu import backup_menu
from .logs_menu import logs_menu
from .system_info_menu import system_info_menu


def main_menu() -> None:
    """Main menu."""
    if not check_root():
        return

    while True:
        show_banner()
        show_header_info()

        section_banner("Main Menu")
        menu_option(1, "Server", "Configure and operate WireGuard")
        print()
        menu_option(2, "Peers", "Create and manage VPN clients")
        print()
        menu_option(3, "Firewall", "Manage firewalld access")
        print()
        menu_option(4, "Diagnostics", "Run health and troubleshooting checks")

        menu_option(5, "Backup & Restore", "Protect or recover LoomWG data")
        menu_option(6, "Logs", "View and export application activity")
        menu_option(7, "System Info", "Read-only server and VPN overview")
        print()
        menu_option(0, "Exit", "Close application")

        console.print()
        try:
            choice = input("Select option: ").strip()
        except (EOFError, KeyboardInterrupt, OSError, UnicodeDecodeError):
            console.print(f"[{THEME['ERROR']}]Input interrupted.[/]")
            pause()
            return


        if choice == "1":
            server_menu()
        elif choice == "2":
            peers_menu()
        elif choice == "3":
            firewall_menu()
        elif choice == "4":
            diagnostics_menu()
        elif choice == "5":
            backup_menu()
        elif choice == "6":
            logs_menu()
        elif choice == "7":
            system_info_menu()
        elif choice == "0":
            console.print(f"[yellow]Goodbye![/]")
            sys.exit(0)
        else:
            console.print(f"[{THEME['WARNING']}]Invalid option. Please try again.[/]")
            pause()


