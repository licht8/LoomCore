"""Auto-extracted from cli/__init__.py"""
from ..wireguard.lifecycle import WireGuardLifecycle
from ..system.services import ServiceManager
from ..logging_system.logger import LoomLogger
from ..system.packages import PackageManager
from ..cli.common import clear_screen, section_banner, pause, confirm
from ..commands.install_wireguard import install_wireguard

from rich.console import Console

console = Console()

def remove_wireguard() -> None:
    """Remove LoomWG-managed WireGuard state after explicit confirmation."""
    clear_screen()
    section_banner("Remove WireGuard", "Remove the LoomWG-managed WireGuard installation")
    console.print("This removes wg0, its service, LoomWG forwarding settings, and WireGuard packages.")
    if confirm("Continue with removal?"):
        result = WireGuardLifecycle().remove()
        console.print(f"[green]✓ {result.message}[/]" if result.success else f"[red]✗ {result.message}[/]")
    pause()




def reinstall_wireguard() -> None:
    """Perform a complete removal followed by a fresh installation."""
    clear_screen()
    section_banner("Reinstall WireGuard", "Remove and recreate the LoomWG WireGuard installation")
    console.print("This first removes the existing LoomWG WireGuard setup.")
    if not confirm("Continue with reinstall?"):
        return
    result = WireGuardLifecycle().remove()
    if not result.success:
        console.print(f"[red]✗ Reinstall stopped: {result.message}[/]")
        pause()
        return
    console.print(f"[green]✓ {result.message}[/]")
    install_wireguard()




