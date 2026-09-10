"""Auto-extracted from cli/__init__.py"""
from rich.console import Console
from rich.table import Table
from rich.rule import Rule
from rich.text import Text

from ..wireguard.manager import WireGuardManager
from ..backup.manager import BackupManager
from ..cli.common import clear_screen, section_banner, pause, THEME

console = Console()


def list_backups() -> None:
    """List available backups with summary."""
    clear_screen()

    try:
        backup_mgr = BackupManager()
        backups = backup_mgr.list_backups()

        if not backups:
            console.print(f"[yellow]No backups found[/]")
            pause()
            return

        # Summary line
        console.print(f"[bold]Found {len(backups)} backup(s)[/]\n")
        console.print(Rule(style=THEME['SECONDARY']))

        table = Table(title="Available Backups", border_style=THEME['PRIMARY'], show_header=True)
        table.add_column("#", style=THEME['DIM'], width=3, no_wrap=True)
        table.add_column("Filename", style=THEME['INFO'])
        table.add_column("Created", style=THEME['WARNING'])

        for i, (filename, created) in enumerate(backups, 1):
            table.add_row(
                str(i),
                filename,
                created.strftime("%Y-%m-%d %H:%M:%S"),
            )

        console.print(table)
        console.print(Rule())

    except Exception as e:
        console.print(f"[{THEME['ERROR']}]Error: {e}[/]")

    pause()


