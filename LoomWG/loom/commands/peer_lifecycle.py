"""Peer lifecycle (enable/disable/revoke/rotate/remove)."""
import os
import subprocess
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
console = Console()

from ..wireguard.manager import WireGuardManager
from ..wireguard.peer_manager import Peer, PeerManager
from ..wireguard.key_manager import KeyManager
from ..wireguard.config_generator import ConfigGenerator
from ..wireguard.client_config import ClientConfigStore
from ..logging_system.logger import LoomLogger
from ..wireguard.server_config import ServerConfig
from ..wireguard.ip_allocator import IPAllocator
from ..wireguard.interfaces import config_path as interface_config_path
from ..views.peer_views import show_peer_selection
from ..system.info import SystemDetector

from ..cli.common import clear_screen, section_banner, pause, confirm, selected_interface, display_peer_qr_code, prompt_for_qr_code

def disable_peer() -> None:
    """Disable a peer."""
    clear_screen()
    section_banner("Disable peer", f"Disable a client on {selected_interface()}")

    try:
        peer_mgr = PeerManager()
        show_peer_selection(peer_mgr)

        try:
            name = input("Peer name: ").strip()

        except (EOFError, KeyboardInterrupt, OSError):
            console.print("[red]Input interrupted.[/]")
            pause()
            return


        if not name:
            return

        peer = peer_mgr.get_peer(name)
        interface = selected_interface()
        config_path = interface_config_path(interface)
        generator = ConfigGenerator()
        runtime_ok = WireGuardManager().remove_peer_from_interface(interface, peer.public_key) if peer else False
        config_ok = generator.remove_peer_from_server_config(config_path, peer.public_key) if peer and config_path.exists() else True
        if runtime_ok and config_ok and peer_mgr.disable_peer(name):
            console.print(f"[green]✓ Peer '{name}' disabled[/]")

            logger = LoomLogger()
            logger.info(f"Peer '{name}' disabled", "peer")
        else:
            console.print("[red]✗ Failed to disable peer[/]")

    except Exception as e:
        console.print(f"[red]Error: {e}[/]")

    pause()




def enable_peer() -> None:
    """Enable a peer."""
    clear_screen()
    section_banner("Enable peer", f"Enable a client on {selected_interface()}")

    try:
        peer_mgr = PeerManager()
        show_peer_selection(peer_mgr)

        try:
            name = input("Peer name: ").strip()

        except (EOFError, KeyboardInterrupt, OSError):
            console.print("[red]Input interrupted.[/]")
            pause()
            return


        if not name:
            return

        peer = peer_mgr.get_peer(name)
        if not peer:
            console.print("[red]✗ Peer not found[/]")
            pause()
            return
        if peer.revoked_at:
            console.print("[red]✗ This peer has been revoked and cannot be re-enabled.[/]")
            pause()
            return
        if peer.expires_at and datetime.fromisoformat(peer.expires_at) <= datetime.now():
            console.print("[red]✗ Peer access has expired. Set a new expiry before enabling it.[/]")
            pause()
            return
        interface = selected_interface()
        config_path = interface_config_path(interface)
        generator = ConfigGenerator()
        config_ok = generator.append_peer_to_server_config(config_path, peer.public_key, peer.ipv4_address, peer.ipv6_address, peer.preshared_key or None)
        manager = WireGuardManager()
        runtime_ok = (not manager.is_interface_active(interface)) or manager.add_peer_to_interface(interface, peer.public_key, peer.ipv4_address, client_ipv6=peer.ipv6_address)
        if config_ok and runtime_ok and peer_mgr.enable_peer(name):
            console.print(f"[green]✓ Peer '{name}' enabled[/]")
            if not manager.is_interface_active(interface):
                console.print(f"[yellow]{interface} is down; the peer will be applied when the interface starts.[/]")

            logger = LoomLogger()
            logger.info(f"Peer '{name}' enabled", "peer")
        else:
            console.print("[red]✗ Failed to enable peer[/]")

    except Exception as e:
        console.print(f"[red]Error: {e}[/]")

    pause()




def revoke_peer() -> None:
    """Revoke a peer while preserving its historical metadata and audit trail."""
    clear_screen()

    try:
        peer_mgr = PeerManager()
        show_peer_selection(peer_mgr)
        try:
            name = input("Peer name: ").strip()

        except (EOFError, KeyboardInterrupt, OSError):
            console.print("[red]Input interrupted.[/]")
            pause()
            return

        peer = peer_mgr.get_peer(name)
        if not peer:
            console.print("[red]Peer not found[/]")
            pause()
            return
        if peer.revoked_at:
            console.print("[yellow]This peer is already revoked.[/]")
            pause()
            return

        console.print(f"\n[bold]Revoke Peer[/]")
        console.print(f"Name: {peer.name}")
        console.print(f"Public Key: {peer.public_key}")
        if not confirm(f"Revoke peer '{name}' and disable its access immediately?"):
            return

        interface = selected_interface()
        config_path = interface_config_path(interface)
        generator = ConfigGenerator()
        manager = WireGuardManager()
        original_config = config_path.read_text(encoding="utf-8") if config_path.exists() else None
        original_peer = Peer(**peer.to_dict())

        try:
            runtime_ok = manager.remove_peer_from_interface(interface, peer.public_key) if manager.is_interface_active(interface) else True
            config_ok = generator.remove_peer_from_server_config(config_path, peer.public_key) if config_path.exists() else True
            if not (runtime_ok and config_ok):
                raise RuntimeError("Failed to remove peer from the live interface or server configuration")
            if not peer_mgr.revoke_peer(name):
                raise RuntimeError("Failed to mark the peer as revoked in the database")
            logger = LoomLogger()
            logger.log_peer_revoked(name, peer.public_key)
            console.print(f"[green]✓ Peer '{name}' revoked[/]")
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/]")
            if config_path.exists() and original_config is not None:
                config_path.write_text(original_config, encoding="utf-8")
                config_path.chmod(0o600)
            peer_mgr.update_peer(name, original_peer)
            if manager.is_interface_active(interface):
                manager.add_peer_to_interface(interface, original_peer.public_key, original_peer.ipv4_address, client_ipv6=original_peer.ipv6_address)
            console.print("[red]✗ Revocation failed and changes were rolled back.[/]")

    except Exception as e:
        console.print(f"[red]Error: {e}[/]")

    pause()




def rotate_peer_keys() -> None:
    """Rotate a peer's WireGuard keys without creating a duplicate peer record."""
    clear_screen()

    try:
        peer_mgr = PeerManager()
        show_peer_selection(peer_mgr)
        try:
            name = input("Peer name: ").strip()

        except (EOFError, KeyboardInterrupt, OSError):
            console.print("[red]Input interrupted.[/]")
            pause()
            return

        peer = peer_mgr.get_peer(name)
        if not peer:
            console.print("[red]Peer not found[/]")
            pause()
            return
        if peer.revoked_at:
            console.print("[red]Revoked peers cannot have their keys rotated.[/]")
            pause()
            return

        console.print(f"\n[bold]Rotate Peer Keys[/]")
        console.print(f"Name: {peer.name}")
        console.print(f"Public Key: {peer.public_key}")
        if not confirm(f"Generate and apply a new keypair for '{name}'?"):
            return

        keypair = KeyManager.generate_keypair()
        original_peer = Peer(**peer.to_dict())
        interface = selected_interface()
        config_path = interface_config_path(interface)
        original_config = config_path.read_text(encoding="utf-8") if config_path.exists() else None
        manager = WireGuardManager()
        generator = ConfigGenerator()

        try:
            if config_path.exists():
                temp_path = config_path.with_suffix(".rotate.tmp")
                temp_path.write_text(original_config, encoding="utf-8")
                generator.remove_peer_from_server_config(temp_path, peer.public_key)
                generator.append_peer_to_server_config(temp_path, keypair.public_key, peer.ipv4_address, peer.ipv6_address)
                candidate = temp_path.read_text(encoding="utf-8")
                valid, errors = generator.validate_config(candidate)
                server_valid, server_errors = generator.validate_server_config(candidate)
                if not (valid and server_valid):
                    raise RuntimeError("; ".join(errors + server_errors))
                temp_path.unlink(missing_ok=True)

            if manager.is_interface_active(interface) and not manager.remove_peer_from_interface(interface, peer.public_key):
                raise RuntimeError("Failed to remove the old peer key from the live interface")

            if config_path.exists():
                generator.remove_peer_from_server_config(config_path, peer.public_key)
                generator.append_peer_to_server_config(config_path, keypair.public_key, peer.ipv4_address, peer.ipv6_address)

            updated_peer = Peer(
                name=peer.name,
                ipv4_address=peer.ipv4_address,
                ipv6_address=peer.ipv6_address,
                public_key=keypair.public_key,
                private_key=keypair.private_key,
                preshared_key=peer.preshared_key,
                created_at=peer.created_at,
                enabled=peer.enabled,
                description=peer.description,
                endpoint=peer.endpoint,
                latest_handshake=peer.latest_handshake,
                transfer_rx=peer.transfer_rx,
                transfer_tx=peer.transfer_tx,
                expires_at=peer.expires_at,
                revoked_at=peer.revoked_at,
                traffic_history=list(peer.traffic_history),
            )
            if not peer_mgr.rotate_peer_keys(name, updated_peer):
                raise RuntimeError("Failed to update the peer database with the new public key")

            server_cfg = ServerConfig.from_file(config_path) if config_path.exists() else ServerConfig.defaults()
            client_store = ClientConfigStore()
            peer_conf = ConfigGenerator().generate_peer_config(
                peer_ipv4=peer.ipv4_address,
                peer_ipv6=peer.ipv6_address,
                private_key=keypair.private_key,
                server_public_key=server_cfg.public_key or "",
                server_endpoint=SystemDetector().detect().public_ip or "YOUR_SERVER_IP",
                server_port=server_cfg.listen_port,
                dns_primary=server_cfg.dns_primary,
                dns_secondary=server_cfg.dns_secondary,
            )
            client_store.save_peer_config(name, peer_conf)
            client_store.save_qr_code(name, peer_conf)

            if manager.is_interface_active(interface) and not manager.add_peer_to_interface(interface, keypair.public_key, peer.ipv4_address, client_ipv6=peer.ipv6_address):
                raise RuntimeError("Failed to add the rotated key to the live interface")

            logger = LoomLogger()
            logger.log_peer_key_rotated(name, original_peer.public_key, keypair.public_key)
            console.print(f"[green]✓ Peer '{name}' key rotation completed[/]")
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/]")
            if config_path.exists() and original_config is not None:
                config_path.write_text(original_config, encoding="utf-8")
                config_path.chmod(0o600)
            peer_mgr.update_peer(name, original_peer)
            if manager.is_interface_active(interface):
                try:
                    manager.remove_peer_from_interface(interface, keypair.public_key)
                    manager.add_peer_to_interface(interface, original_peer.public_key, original_peer.ipv4_address, client_ipv6=original_peer.ipv6_address)
                except Exception as exc:
                    console.print(f"[red]Error: {exc}[/]")
                    pass
            console.print(f"[red]✗ Rotation failed and the prior state was restored: {exc}[/]")

    except Exception as e:
        console.print(f"[red]Error: {e}[/]")

    pause()




def remove_peer() -> None:
    """Remove a peer."""
    clear_screen()
    section_banner("Remove peer", f"Remove a client from {selected_interface()}")

    try:
        peer_mgr = PeerManager()
        show_peer_selection(peer_mgr)

        try:
            name = input("Peer name: ").strip()

        except (EOFError, KeyboardInterrupt, OSError):
            console.print("[red]Input interrupted.[/]")
            pause()
            return


        if not name:
            return

        peer = peer_mgr.get_peer(name)

        if not peer:
            console.print("[red]Peer not found[/]")
            pause()
            return

        if confirm(f"Remove peer '{name}'? This cannot be undone."):
            interface = selected_interface()
            config_path = interface_config_path(interface)
            wg_manager = WireGuardManager()
            generator = ConfigGenerator()

            # 1. Remove from live WireGuard interface
            runtime_ok = wg_manager.remove_peer_from_interface(interface, peer.public_key) if wg_manager.is_interface_active(interface) else True

            # 2. Remove from wg0.conf file
            config_ok = generator.remove_peer_from_server_config(config_path, peer.public_key) if config_path.exists() else True

            if not (runtime_ok and config_ok):
                console.print("[red]✗ Failed to remove peer from live interface or config[/]")
                pause()
                return

            # 3. Remove from database
            if peer_mgr.remove_peer(name):
                logger = LoomLogger()
                logger.log_peer_removed(name)
                console.print(f"[green]✓ Peer '{name}' removed[/]")
            else:
                console.print("[red]✗ Failed to remove peer from database[/]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/]")

    pause()




