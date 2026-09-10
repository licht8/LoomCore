"""Server management menu."""
from rich.console import Console
console = Console()

from ..cli.common import THEME, show_banner
from ..views.server_status import show_server_status, show_server_config
from ..commands.configure_server import configure_server
from ..commands.lifecycle import remove_wireguard, reinstall_wireguard
from ..commands.key_rotation import rotate_server_keys
from ..cli.common import manage_interfaces

from ..wireguard.manager import WireGuardManager
from ..wireguard.server_config import ServerConfig
from ..wireguard.interfaces import config_path

from ..system.services import ServiceManager
from ..logging_system.logger import LoomLogger
from ..cli.common import clear_screen, section_banner, show_header_info, menu_option, pause, confirm, selected_interface


def server_menu() -> None:
    """Server management menu."""
    wg_manager = WireGuardManager()
    logger = LoomLogger()

    while True:
        show_banner()
        show_header_info()

        section_banner("Server Menu", f"Selected: {selected_interface()}")
        menu_option(1, "Server status", "Live WireGuard runtime activity")
        menu_option(2, "Configure server", "Create the initial wg0 configuration")
        menu_option(3, "View configuration", "Display the saved wg0.conf file")
        print()
        menu_option(4, "Start WireGuard", "Bring up the VPN interface", "wg-quick up wg0")
        menu_option(5, "Stop WireGuard", "Bring down the VPN interface", "wg-quick down wg0")
        menu_option(6, "Restart WireGuard", "Restart the VPN interface", "wg-quick down/up wg0")
        print()
        menu_option(7, "Enable on boot", "Start WireGuard automatically", "systemctl enable wg-quick@wg0")
        menu_option(8, "Remove WireGuard", "Remove LoomWG-managed WireGuard state", "systemctl disable --now wg-quick@wg0")
        menu_option(9, "Reinstall WireGuard", "Remove and install a fresh setup", "dnf remove/install wireguard-tools")
        print()
        menu_option(10, "Rotate server keys", "Replace the server keypair safely with backup and validation")
        menu_option(11, "Manage interfaces", "Create, select, or delete WireGuard interfaces")
        print()
        menu_option(0, "Back")

        console.print()
        try:
            choice = input("Select option: ").strip()
        except (EOFError, KeyboardInterrupt, OSError, UnicodeDecodeError):
            console.print(f"[{THEME['ERROR']}]Input interrupted.[/]")
            pause()
            return

        interface = selected_interface()
        if choice == "1":
            show_server_status()
        elif choice == "2":
            configure_server()
        elif choice == "3":
            show_server_config()
        elif choice == "4":
            if wg_manager.start(interface):
                console.print(f"[{THEME['SUCCESS']}]WireGuard started[/{THEME['SUCCESS']}]")
                logger.info("WireGuard started", "server")
            else:
                console.print(f"[{THEME['ERROR']}]Failed to start WireGuard[/{THEME['ERROR']}]")
                logger.error("Failed to start WireGuard", "server")

            pause()
        elif choice == "5":
            if wg_manager.stop(interface):
                console.print(f"[{THEME['SUCCESS']}]WireGuard stopped[/{THEME['SUCCESS']}]")
                logger.info("WireGuard stopped", "server")
            else:
                console.print(f"[{THEME['ERROR']}]Failed to stop WireGuard[/{THEME['ERROR']}]")
                logger.error("Failed to stop WireGuard", "server")

            pause()
        elif choice == "6":
            if wg_manager.restart(interface):
                console.print(f"[{THEME['SUCCESS']}]WireGuard restarted[/{THEME['SUCCESS']}]")
                logger.info("WireGuard restarted", "server")
            else:
                console.print(f"[{THEME['ERROR']}]Failed to restart WireGuard[/{THEME['ERROR']}]")
                logger.error("Failed to restart WireGuard", "server")

            pause()
        elif choice == "7":
            services = ServiceManager()

            if services.enable(f"wg-quick@{interface}"):
                console.print(f"[{THEME['SUCCESS']}]Enabled on boot[/{THEME['SUCCESS']}]")
                logger.info("WireGuard enabled on boot", "server")
            else:
                console.print(f"[{THEME['ERROR']}]Failed to enable on boot[/{THEME['ERROR']}]")

            pause()
        elif choice == "8":
            remove_wireguard()
        elif choice == "9":
            reinstall_wireguard()
        elif choice == "10":
            rotate_server_keys()
        elif choice == "11":
            manage_interfaces()
        elif choice == "0":
            break
        else:
            console.print(f"[{THEME['WARNING']}]Invalid option.[/{THEME['WARNING']}]")
            pause()


