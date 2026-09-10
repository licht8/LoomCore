"""Auto-extracted from cli/__init__.py"""
import os
import re
import subprocess

from ..wireguard.installer import WireGuardInstaller
from ..system.packages import PackageManager
from ..system.services import ServiceManager
from ..system.info import SystemDetector
from ..wireguard.server_config import ServerConfig
from ..wireguard.config_generator import ConfigGenerator
from ..wireguard.key_manager import KeyManager
from ..wireguard.interfaces import config_path as interface_config_path
from ..wireguard.manager import WireGuardManager

from ..firewall.firewalld import FirewalldManager
from ..system.network import NetworkManager
from ..logging_system.logger import LoomLogger
from ..cli.common import clear_screen, section_banner, pause, selected_interface, confirm

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn
from rich.panel import Panel
from rich.text import Text

console = Console()

def install_wireguard() -> None:
    """Install WireGuard and configure the default server."""
    clear_screen()
    interface = selected_interface()

    console.print("[bold]Installing WireGuard[/]\n")

    detector = SystemDetector()
    checks = detector.check()

    console.print("[bold]System Checks:[/]\n")

    for check in checks:
        status = "[green]✓[/]" if check.passed else "[red]✗[/]"
        console.print(f"{status} {check.name}: {check.message}")

    critical_failed = any(
        not check.passed
        and check.name
        in ("Operating system", "Architecture", "Root privileges", "Init system")
        for check in checks
    )

    if critical_failed:
        console.print("\n[red]System does not meet requirements[/]")
        pause()
        return

    if not confirm("\nContinue with installation?"):
        return

    installer = WireGuardInstaller()

    console.print("\n[bold cyan]Installing WireGuard...[/]\n")
    result = installer.install(interface)

    if result.success:
        console.print(Panel(
            Text.assemble("✓ Installation completed: ", (result.message, "green")),
            border_style="green",
        ))
        console.print(f"[green]✓ {result.message}\n[/]")
    else:
        console.print(Panel(
            Text.assemble("✗ Installation failed: ", (result.message, "red")),
            border_style="red",
        ))
        if result.details:
            console.print(f"[yellow]Debug: {result.details}[/]")
        LoomLogger().log_installation(False, result.message)
        pause()
        return

    console.print(f"[green]✓ {result.message}[/]\n")

    # Lazy import to break circular dependency with configure_server
    from ..commands.configure_server import prompt_server_config, validate_server_settings

    try:
        console.print("\n[bold]Creating server configuration...[/]")
        config = prompt_server_config()
        config_valid, config_errors = config.validate()
        config_errors.extend(validate_server_settings(config))
        if not config_valid or config_errors:
            console.print("[red]Configuration invalid:[/]")
            for error in config_errors:
                console.print(f"  - {error}")
            pause()
            return
        key_mgr = KeyManager()
        keypair = key_mgr.generate_keypair()
        config.private_key = keypair.private_key
        config.public_key = keypair.public_key

        generator = ConfigGenerator()
        server_conf = generator.generate_server_config(
            server_ipv4=config.get_ipv4_server_address(),
            server_ipv6=config.get_ipv6_server_address(),
            listen_port=config.listen_port,
            private_key=config.private_key,
        )

        valid, errors = generator.validate_config(server_conf)
        server_valid, server_errors = generator.validate_server_config(server_conf)
        if not server_valid:
            valid = False
            errors.extend(server_errors)

        if not valid:
            console.print("[red]Configuration validation failed:[/]")
            for error in errors:
                console.print(f"  - {error}")
            pause()
            return

        config_path = interface_config_path(selected_interface())
        if not generator.write_config(config_path, server_conf):
            console.print("[red]✗ Failed to write WireGuard configuration[/]")
            pause()
            return

        console.print("[green]✓ Server configuration created[/]")

        firewall = FirewalldManager()
        if not firewall.is_running():
            firewall.start()
        firewall.open_port(config.listen_port)
        firewall.enable_masquerading()
        console.print("[green]✓ Firewall rules configured[/]")

        network = NetworkManager()
        network.enable_ip_forwarding()
        network.enable_ipv6_forwarding()
        console.print("[green]✓ IP forwarding enabled[/]")

        wg_manager = WireGuardManager()
        start_result = wg_manager.start_with_result(interface)
        runtime_ok = start_result.success

        if runtime_ok:
            if start_result.already_running:
                console.print(f"[green]✓ {interface} is already running[/]")
            else:
                console.print("[green]✓ WireGuard interface started successfully[/]")
        else:
            console.print(f"[red]✗ Failed to start WireGuard interface {interface}[/]")
            console.print(f"Return code: {start_result.return_code}")
            console.print(f"stdout: {start_result.stdout or '<empty>'}")
            console.print(f"stderr: {start_result.stderr or '<empty>'}")
            console.print("\n[bold]Runtime verification:[/]")
            console.print(
                f"wg show interfaces: {interface if start_result.wg_interface_exists else f'{interface} not present'}"
            )
            console.print(
                f"ip link show {interface}: {'present' if start_result.link_exists else 'not present'}"
            )
            console.print("\n[yellow]Installation completed with errors.[/]")
            console.print("[yellow]WireGuard is NOT currently running.[/]")
            console.print("Run: wg show interfaces")
            console.print(f"Run: wg show {interface}")
            console.print(f"Run: ip link show {interface}")

        boot_enabled = ServiceManager().enable(f"wg-quick@{interface}")
        if boot_enabled:
            console.print("[green]✓ WireGuard enabled on boot[/]")
        else:
            console.print("[yellow]⚠ Could not enable WireGuard at boot[/]")

        installation_ok = runtime_ok and boot_enabled
        LoomLogger().log_installation(installation_ok, str(config_path))
        if installation_ok:
            console.print("\n[green]✓ WireGuard installation completed successfully[/]")
        elif runtime_ok:
            console.print("\n[yellow]Installation completed with errors.[/]")
    except Exception as exc:
        console.print(f"[red]✗ Installation failed: {exc}[/]")
        LoomLogger().log_installation(False, str(exc))

    pause()