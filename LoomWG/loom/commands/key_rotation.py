"""Auto-extracted from cli/__init__.py"""
import re
import shutil
from datetime import datetime
from pathlib import Path

from ..wireguard.manager import WireGuardManager
from ..wireguard.server_config import ServerConfig
from ..wireguard.key_manager import KeyManager
from ..wireguard.config_generator import ConfigGenerator
from ..wireguard.client_config import ClientConfigStore
from ..wireguard.peer_manager import PeerManager
from ..wireguard.interfaces import config_path as interface_config_path

from ..backup.manager import BackupManager
from ..commands.configure_server import normalize_wireguard_config
from ..commands.configure_server import repair_wireguard_config_file
from ..logging_system.logger import LoomLogger
from ..cli.common import selected_interface, clear_screen, section_banner, pause, confirm

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()

def rotate_server_keys() -> None:
    """Rotate the server keypair with validation, backup, and rollback protection."""
    clear_screen()

    interface = selected_interface()
    config_path = interface_config_path(interface)
    wg_manager = WireGuardManager()

    if not config_path.exists():
        console.print("[yellow]No WireGuard configuration exists for wg0.[/]")
        pause()
        return

    repaired_config = repair_wireguard_config_file(config_path)
    current_cfg = ServerConfig.from_file(config_path)
    peer_mgr = PeerManager()
    configured_peers = len(peer_mgr.list_peers())
    active_peers = len(peer_mgr.list_enabled_peers())
    current_key = current_cfg.public_key or (KeyManager.generate_public_key(current_cfg.private_key) if current_cfg.private_key else "<unknown>")

    section_banner("Rotate Server Keys", "Safely replace the server keypair")
    console.print(f"Interface: {interface}")
    console.print(f"Current server public key: {current_key}")
    console.print(f"Configured peers: {configured_peers}")
    console.print(f"Active peers: {active_peers}")
    console.print(f"Configuration file: {config_path}")
    console.print()
    console.print("[bold red]WARNING:[/bold red] Rotating the server key changes the server public key and makes existing client configurations outdated.")
    try:
        confirmation = input("Type ROTATE to continue: ").strip()

    except (EOFError, KeyboardInterrupt, OSError):
        console.print("[red]Input interrupted.[/]")
        pause()
        return

    if confirmation != "ROTATE":
        console.print("[yellow]Server key rotation cancelled.[/]")
        pause()
        return

    backup_mgr = BackupManager()
    backup_file = backup_mgr.create_backup(description="Server key rotation pre-checkpoint")
    if backup_file is None:
        console.print("[red]✗ Failed to create a backup before rotating the server key.[/]")
        pause()
        return

    try:
        new_keypair = KeyManager.generate_keypair()
        if not KeyManager.validate_key(new_keypair.private_key):
            raise RuntimeError("Generated server private key is invalid")
        if not wg_manager.is_interface_active(interface):
            raise RuntimeError(f"WireGuard interface {interface} is not active")

        original_config = normalize_wireguard_config(config_path.read_text(encoding="utf-8"))
        candidate_config = re.sub(
            r"(?m)^PrivateKey\s*=\s*.*$",
            f"PrivateKey = {new_keypair.private_key}",
            original_config,
            count=1,
        )
        candidate_config = normalize_wireguard_config(candidate_config)
        if "PrivateKey = " not in candidate_config:
            raise RuntimeError("The generated server configuration does not contain a private key")

        valid, errors = ConfigGenerator.validate_config(candidate_config)
        server_valid, server_errors = ConfigGenerator.validate_server_config(candidate_config)
        if not valid or not server_valid:
            raise RuntimeError("; ".join(errors + server_errors))

        peer_entries = ConfigGenerator.parse_server_peers(config_path)
        if any(not entry.get("public_key") for entry in peer_entries):
            raise RuntimeError("One or more existing peers cannot be represented in the rotated configuration")

        config_path.write_text(normalize_wireguard_config(candidate_config), encoding="utf-8")
        config_path.chmod(0o600)

        if not wg_manager.sync(interface):
            raise RuntimeError("Failed to apply the new server key to the live interface")

        client_store = ClientConfigStore()
        client_names = [path.stem for path in client_store.base_dir.glob("*.conf") if path.is_file()]
        regenerated, regeneration_failed = client_store.regenerate_after_server_key_rotation(
            new_keypair.public_key,
            client_names,
        )
        if regeneration_failed:
            regen_marker = client_store.mark_server_key_rotation_required(regeneration_failed)
        else:
            regen_marker = None

        current_cfg.private_key = new_keypair.private_key
        current_cfg.public_key = new_keypair.public_key

        logger = LoomLogger()
        logger.log_server_key_rotated(
            interface,
            current_key if current_key != "<unknown>" else "unknown",
            new_keypair.public_key,
            str(backup_file),
            configured_peers,
        )

        # Build summary details
        lines = [f"New server public key: {new_keypair.public_key}", f"Preserved peers: {configured_peers}", f"Client configs regenerated: {len(regenerated)}"]
        if regeneration_failed:
            lines.append(f"Manual regeneration required: {len(regeneration_failed)}")
        lines.append(f"Backup location: {backup_file}")
        lines.append(f"Rotation timestamp: {datetime.now().isoformat()}")
        if regen_marker:
            lines.append(f"Regeneration marker: {regen_marker}")

        console.print(Panel(
            "\n".join(lines),
            title=Text("✓ Server key rotation completed", style="bold green"),
            border_style="green",
        ))

    except Exception as exc:
        config_path.write_text(normalize_wireguard_config(original_config), encoding="utf-8")
        config_path.chmod(0o600)
        if wg_manager.is_interface_active(interface):
            try:
                wg_manager.sync(interface)
            except Exception:
                pass
        console.print(f"[red]✗ Server key rotation failed and the previous state was restored: {exc}[/]")
        LoomLogger().error("Server key rotation failed and was rolled back", "server", details=str(exc))

    pause()




