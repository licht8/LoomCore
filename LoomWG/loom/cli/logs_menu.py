"""Logs menu."""
from rich.console import Console
console = Console()

from ..cli.common import THEME, show_banner
from ..views.log_views import view_logs, clear_logs, export_logs

from ..logging_system.logger import LoomLogger
from ..cli.common import clear_screen, section_banner, menu_option, pause, show_header_info


def logs_menu() -> None:
    """Logs menu."""
    while True:
        show_banner()
        show_header_info()

        section_banner("Logs Menu")
        menu_option(1, "View recent logs", "Show the latest LoomWG activity")
        menu_option(2, "Clear logs", "Permanently remove saved log entries")
        menu_option(3, "Export logs", "Save logs to a JSON file")
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
            view_logs()
        elif choice == "2":
            clear_logs()
        elif choice == "3":
            export_logs()
        elif choice == "0":
            break
        else:
            console.print(f"[{THEME['WARNING']}]Invalid option.[/{THEME['WARNING']}]")
            pause()


