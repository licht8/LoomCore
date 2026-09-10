"""Configure WireGuard server — create, validate, write config."""
import re
import subprocess
from ipaddress import ip_network
from pathlib import Path

from rich.console import Console

from ..wireguard.manager import WireGuardManager
from ..firewall.firewalld import FirewalldManager
from ..system.network import NetworkManager
from ..logging_system.logger import LoomLogger

console = Console()
from ..wireguard.server_config import ServerConfig
from ..wireguard.key_manager import KeyManager
from ..wireguard.config_generator import ConfigGenerator
from ..wireguard.interfaces import config_path as interface_config_path

from ..cli.common import selected_interface, clear_screen, section_banner, pause, confirm
from ..commands.install_wireguard import install_wireguard

def configure_server() -> None:
    """Configure WireGuard server."""
    clear_screen()

    section_banner("Configure server", "Create or update the WireGuard server configuration")

    # Check if WireGuard is installed
    wg_manager = WireGuardManager()

    if Path("/etc/wireguard/wg0.conf").exists():
        console.print("[yellow]WireGuard server is already configured at /etc/wireguard/wg0.conf.[/]")
        pause()
        return

    if not wg_manager.is_installed():
        console.print("[yellow]WireGuard is not installed. Install first? (y/n)[/]")

        if confirm():
            install_wireguard()
            return
        else:
            return

    config = prompt_server_config()

    # Validate
    valid, errors = config.validate()
    errors.extend(validate_server_settings(config))
    valid = valid and not errors

    if not valid:
        console.print("[red]Configuration invalid:[/]")

        for error in errors:
            console.print(f"  - {error}")

        pause()
        return

    # Generate keys
    console.print("\n[bold]Generating keys...[/]")

    try:
        key_mgr = KeyManager()
        keypair = key_mgr.generate_keypair()

        config.private_key = keypair.private_key
        config.public_key = keypair.public_key

        console.print("[green]✓ Keys generated[/]")
    except Exception as e:
        console.print(f"[red]✗ Failed to generate keys: {e}[/]")
        pause()
        return

    # Generate configuration
    console.print("[bold]Generating configuration...[/]")

    try:
        gen = ConfigGenerator()
        server_ipv4 = config.get_ipv4_server_address()
        server_ipv6 = config.get_ipv6_server_address()

        conf_content = gen.generate_server_config(
            server_ipv4=server_ipv4,
            server_ipv6=server_ipv6,
            listen_port=config.listen_port,
            private_key=config.private_key,
        )

        # Validate
        valid, errors = gen.validate_config(conf_content)

        server_valid, server_errors = gen.validate_server_config(conf_content)
        if not server_valid:
            valid = False
            errors.extend(server_errors)

        if not valid:
            console.print("[red]Configuration invalid:[/]")

            for error in errors:
                console.print(f"  - {error}")

            pause()
            return

        # Write config
        interface = selected_interface()
        config_path = interface_config_path(interface)

        if not gen.write_config(config_path, conf_content):
            console.print("[red]✗ Failed to write configuration[/]")
            pause()
            return

        console.print("[green]✓ Configuration written[/]")

        # Log
        logger = LoomLogger()
        logger.log_installation(True, f"Server config: {config_path}")

    except Exception as e:
        console.print(f"[red]✗ Failed: {e}[/]")
        pause()
        return

    # Enable firewall
    console.print("\n[bold]Configuring firewall...[/]")

    try:
        firewall = FirewalldManager()

        if not firewall.is_running():
            console.print("[yellow]Starting firewalld...[/]")
            firewall.start()

        if firewall.open_port(config.listen_port):
            console.print("[green]✓ Port opened[/]")
        else:
            console.print("[yellow]⚠ Could not open port[/]")

        if firewall.enable_masquerading():
            console.print("[green]✓ Masquerading enabled[/]")
        else:
            console.print("[yellow]⚠ Could not enable masquerading[/]")
    except Exception as e:
        console.print(f"[yellow]⚠ Firewall configuration failed: {e}[/]")

    # Enable IP forwarding
    console.print("[bold]Enabling IP forwarding...[/]")

    try:
        network = NetworkManager()

        if network.enable_ip_forwarding():
            console.print("[green]✓ IPv4 forwarding enabled[/]")

        if network.enable_ipv6_forwarding():
            console.print("[green]✓ IPv6 forwarding enabled[/]")
    except Exception as e:
        console.print(f"[yellow]⚠ Could not enable forwarding: {e}[/]")

    console.print("\n[green]✓ Server configured successfully![/]")
    pause()




def normalize_wireguard_config(content: str) -> str:
    """Normalize WireGuard config lines to the canonical format accepted by wg setconf."""
    normalized_lines: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            normalized_lines.append(raw_line)
            continue
        if "=" not in line:
            normalized_lines.append(raw_line)
            continue
        key, value = [part.strip() for part in line.split("=", 1)]
        value = re.sub(r',(?=\S)', ', ', value)
        normalized_lines.append(f"{key} = {value}")
    return "\n".join(normalized_lines) + ("\n" if content.endswith("\n") else "")




def repair_wireguard_config_file(config_path: Path) -> str:
    """Repair legacy malformed WireGuard config formatting before live apply."""
    original = config_path.read_text(encoding="utf-8")
    normalized = normalize_wireguard_config(original)
    if normalized != original:
        config_path.write_text(normalized, encoding="utf-8")
        config_path.chmod(0o600)
    return normalized




def prompt_server_config() -> ServerConfig:
    """Collect server network settings, using safe defaults on blank input."""
    defaults = ServerConfig.defaults()
    console.print("[dim]Press Enter to accept the value in brackets.[/dim]")

    def value(label: str, default: str) -> str:
        try:
            value = input(f"{label} [{default}]: ").strip()
        except (EOFError, KeyboardInterrupt, OSError):
            console.print("[red]Input interrupted.[/]")
            pause()
            return None
        return value or default


    port_text = value("Listening UDP port", str(defaults.listen_port))
    try:
        port = int(port_text)
    except ValueError:
        port = 0
    config = ServerConfig(
        wg_interface=defaults.wg_interface,
        listen_port=port,
        ipv4_network=value("VPN IPv4 network", defaults.ipv4_network),
        ipv6_network=value("VPN IPv6 network", defaults.ipv6_network),
        dns_primary=value("Primary DNS", defaults.dns_primary),
        dns_secondary=value("Secondary DNS", defaults.dns_secondary),
        allowed_ips=value("Client Allowed IPs", defaults.allowed_ips),
    )
    return config




def validate_server_settings(config: ServerConfig) -> list[str]:
    """Reject unsafe server settings before touching the configuration file."""
    errors: list[str] = []
    try:
        routes = subprocess.run(["ip", "-o", "route", "show"], capture_output=True, text=True, timeout=5, check=False)
        for line in routes.stdout.splitlines():
            destination = line.split()[0] if line.split() else ""
            if destination in {"default", "broadcast", "local"}:
                continue
            try:
                if config.get_ipv4_network().overlaps(ip_network(destination, strict=False)):
                    errors.append(f"VPN IPv4 network overlaps existing route {destination}")
                    break
            except ValueError:
                continue
    except OSError:
        pass
    try:
        result = subprocess.run(["ss", "-lun"], capture_output=True, text=True, timeout=5, check=False)
        marker = f":{config.listen_port}"
        if result.returncode == 0 and marker in result.stdout:
            errors.append(f"UDP port {config.listen_port} is already in use")
    except OSError:
        pass
    return errors




