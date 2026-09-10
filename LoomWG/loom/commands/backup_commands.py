"""Backup operations."""
from pathlib import Path
import shutil

from rich.console import Console
console = Console()

from ..backup.manager import BackupManager
from ..wireguard.manager import WireGuardManager
from ..system.services import ServiceManager
from ..logging_system.logger import LoomLogger
from ..cli.common import clear_screen, section_banner, pause, confirm


def create_backup() -> None:
    """Create a backup."""
    clear_screen()
    section_banner("Create backup", "Save the current LoomWG and WireGuard state")

    try:
        try:
            description = input("Backup description (optional): ").strip()

        except (EOFError, KeyboardInterrupt, OSError):
            console.print("[red]Input interrupted.[/]")
            pause()
            return


        console.print("\n[bold]Creating backup...[/]")

        backup_mgr = BackupManager()
        backup_file = backup_mgr.create_backup(description)

        if backup_file:
            console.print(f"[green]✓ Backup created: {backup_file}[/]")

            logger = LoomLogger()
            logger.info(f"Backup created: {backup_file}", "backup")
        else:
            console.print("[red]✗ Failed to create backup[/]")

    except Exception as e:
        console.print(f"[red]Error: {e}[/]")

    pause()




def restore_backup() -> None:
    """Restore from backup."""
    clear_screen()
    section_banner("Restore backup", "Recover a saved LoomWG state")

    try:
        backup_mgr = BackupManager()
        backups = backup_mgr.list_backups()

        if not backups:
            console.print("[yellow]No backups available[/]")
            pause()
            return

        console.print("[bold]Available Backups[/]\n")

        for i, (filename, created) in enumerate(backups, 1):
            print(f"  {i}) {filename} ({created.strftime('%Y-%m-%d %H:%M:%S')})")

        try:
            choice = input("\nSelect backup (number): ").strip()

        except (EOFError, KeyboardInterrupt, OSError):
            console.print("[red]Input interrupted.[/]")
            pause()
            return


        try:
            idx = int(choice) - 1

            if idx < 0 or idx >= len(backups):
                console.print("[red]Invalid selection[/]")
                pause()
                return

            backup_filename, _ = backups[idx]
            backup_file = backup_mgr.backup_dir / backup_filename

            if confirm("Restore from this backup? Current configuration will be backed up."):
                console.print("\n[bold]Restoring...[/]")

                if backup_mgr.restore_backup(backup_file):
                    console.print("[green]✓ Restore successful[/]")

                    logger = LoomLogger()
                    logger.info(f"Backup restored: {backup_filename}", "backup")
                else:
                    console.print("[red]✗ Restore failed[/]")

        except ValueError:
            console.print("[red]Invalid input[/]")

    except Exception as e:
        console.print(f"[red]Error: {e}[/]")

    pause()




def delete_backup() -> None:
    """Delete a backup."""
    clear_screen()
    section_banner("Delete backup", "Permanently remove a saved backup")

    try:
        backup_mgr = BackupManager()
        backups = backup_mgr.list_backups()

        if not backups:
            console.print("[yellow]No backups available[/]")
            pause()
            return

        console.print("[bold]Available Backups[/]\n")

        for i, (filename, created) in enumerate(backups, 1):
            print(f"  {i}) {filename} ({created.strftime('%Y-%m-%d %H:%M:%S')})")

        try:
            choice = input("\nSelect backup to delete (number): ").strip()

        except (EOFError, KeyboardInterrupt, OSError):
            console.print("[red]Input interrupted.[/]")
            pause()
            return


        try:
            idx = int(choice) - 1

            if idx < 0 or idx >= len(backups):
                console.print("[red]Invalid selection[/]")
                pause()
                return

            backup_filename, _ = backups[idx]
            backup_file = backup_mgr.backup_dir / backup_filename

            if confirm(f"Delete {backup_filename}? This cannot be undone."):
                if backup_mgr.delete_backup(backup_file):
                    console.print("[green]✓ Backup deleted[/]")

                    logger = LoomLogger()
                    logger.info(f"Backup deleted: {backup_filename}", "backup")
                else:
                    console.print("[red]✗ Failed to delete backup[/]")

        except ValueError:
            console.print("[red]Invalid input[/]")

    except Exception as e:
        console.print(f"[red]Error: {e}[/]")

    pause()




