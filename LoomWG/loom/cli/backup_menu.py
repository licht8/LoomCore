"""Backup menu."""
from rich.console import Console
console = Console()

from ..cli.common import THEME, show_banner
from ..commands.backup_commands import create_backup, restore_backup, delete_backup
from ..views.backup_views import list_backups
from ..cli.common import show_header_info

from ..backup.manager import BackupManager
from ..logging_system.logger import LoomLogger
from ..cli.common import clear_screen, section_banner, menu_option, pause, show_header_info


def backup_menu() -> None:
    """Backup and restore menu."""
    while True:
        show_banner()
        show_header_info()

        section_banner("Backup & Restore Menu", "Protect or recover LoomWG data")
        menu_option(1, "Create backup", "Save current LoomWG data")
        menu_option(2, "Restore backup", "Recover saved LoomWG data")
        menu_option(3, "Delete backup", "Permanently delete a backup")
        print()
        menu_option(4, "List backups", "Show available backup files")
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
            create_backup()
        elif choice == "2":
            restore_backup()
        elif choice == "3":
            delete_backup()
        elif choice == "4":
            list_backups()
        elif choice == "0":
            break
        else:
            console.print(f"[{THEME['WARNING']}]Invalid option.[/{THEME['WARNING']}]")
            pause()


