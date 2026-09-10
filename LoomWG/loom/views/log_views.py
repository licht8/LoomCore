"""Auto-extracted from cli/__init__.py"""
import re
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.rule import Rule
from rich.text import Text
from rich.panel import Panel

from ..logging_system.logger import LoomLogger
from ..cli.common import clear_screen, section_banner, pause, confirm, THEME
from ..wireguard.manager import WireGuardManager
from ..wireguard.server_config import ServerConfig
from ..firewall.firewalld import FirewalldManager

console = Console()


def show_firewall_status() -> None:
    """Show firewall status."""
    clear_screen()
    section_banner("Show status", "View firewalld and WireGuard port status")

    try:
        firewall = FirewalldManager()

        console.print(f"[bold]Firewall Status[/]\n")
        running_text = f"[{THEME['SUCCESS']}]Yes[/]" if firewall.is_running() else f"[{THEME['ERROR']}]No[/]"
        enabled_text = f"[{THEME['SUCCESS']}]Yes[/]" if firewall.is_enabled() else f"[{THEME['ERROR']}]No[/]"
        console.print(f"Running: {running_text}")
        console.print(f"Enabled: {enabled_text}")

        config = ServerConfig.defaults()

        if firewall.is_running():
            port_open = firewall.is_port_open(config.listen_port)
            masq = firewall.is_masquerading_enabled()

            port_text = f"[{THEME['SUCCESS']}]Open[/]" if port_open else f"[{THEME['ERROR']}]Closed[/]"
            masq_text = f"[{THEME['SUCCESS']}]Enabled[/]" if masq else f"[{THEME['WARNING']}]Disabled[/]"
            console.print(f"Port {config.listen_port}/UDP: {port_text}")
            console.print(f"Masquerading: {masq_text}")
    except Exception as e:
        console.print(f"[{THEME['ERROR']}]Error: {e}[/]")

    pause()


def view_logs() -> None:
    """View recent logs grouped by severity."""
    clear_screen()

    try:
        logger = LoomLogger()
        logs = logger.list_recent(50)

        if not logs:
            console.print(f"[yellow]No logs found[/]")
            pause()
            return

        # Group logs by level
        by_level = {"ERROR": [], "WARNING": [], "INFO": []}
        for log in logs:
            level = log.get("level", "INFO")
            if level in by_level:
                by_level[level].append(log)

        # Color mapping for severity
        color_map = {
            "ERROR": THEME["ERROR"],
            "WARNING": THEME["WARNING"],
            "INFO": THEME["SUCCESS"],
        }

        # Icon mapping (raw text, no f-string tags)
        icon_map = {
            "ERROR": "\u2717",
            "WARNING": "\u26a0",
            "INFO": "\u2713",
        }

        # Print each group in a styled panel
        for level, level_logs in by_level.items():
            if not level_logs:
                continue

            color = color_map.get(level, THEME["TEXT"])
            icon = icon_map.get(level, "\u2022")

            # Single f-string title with ONE close tag
            title_text = f"[bold {color}]{icon} {level} ({len(level_logs)})[/]"

            lines = []
            for log in level_logs[-10:]:  # Show last 10 per level
                ts = log.get("timestamp", "")[:19]
                msg = log.get("message", "")
                cat = log.get("category", "")
                lines.append(f"  [dim]{ts}[/] [{cat}] {msg}")

            console.print(Panel(
                "\n".join(lines),
                title=title_text,
                border_style=color,
                padding=(0, 1),
            ))
            console.print()

    except Exception as e:
        console.print(f"[red]Error: {e}[/]")

    pause()



def clear_logs() -> None:
    """Clear all logs."""
    if confirm("Clear all logs? This cannot be undone."):
        try:
            logger = LoomLogger()

            if logger.clear_logs():
                console.print(f"[{THEME['SUCCESS']}]Logs cleared[/]")
            else:
                console.print(f"[{THEME['ERROR']}]Failed to export logs[/]")

        except Exception as e:
            console.print(f"[{THEME['ERROR']}]Error: {e}[/]")

    pause()


def export_logs() -> None:
    """Export logs to file."""
    clear_screen()

    try:
        try:
            filename = input("Export filename (default: loomwg_logs.json): ").strip()

        except (EOFError, KeyboardInterrupt, OSError):
            console.print(f"[{THEME['ERROR']}]Input interrupted.[/]")
            pause()
            return


        if not filename:
            filename = "loomwg_logs.json"

        logger = LoomLogger()
        export_path = Path(filename)

        if logger.export_logs(export_path):
            console.print(f"[{THEME['SUCCESS']}]Logs exported to {filename}[/]")
        else:
            console.print(f"[{THEME['ERROR']}]Failed to export logs[/]")

    except Exception as e:
        console.print(f"[{THEME['ERROR']}]Error: {e}[/]")

    pause()


