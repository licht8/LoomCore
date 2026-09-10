"""LoomWG CLI interface."""
import os
import re
import sys
import subprocess
from datetime import datetime
from pathlib import Path
from ipaddress import ip_network

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ..backup.manager import BackupManager
from ..diagnostics import (
    FirewallDiagnostics,
    NetworkDiagnostics,
    SystemDiagnostics,
    WireGuardDiagnostics,
)
from ..firewall.firewalld import FirewalldManager
from ..logging_system.logger import LoomLogger
from ..system.info import SystemDetector
from ..system.network import NetworkManager
from ..system.packages import PackageManager
from ..system.services import ServiceManager
from ..wireguard.client_config import ClientConfigStore
from ..wireguard.config_generator import ConfigGenerator
from ..wireguard.installer import WireGuardInstaller
from ..wireguard.lifecycle import WireGuardLifecycle
from ..wireguard.ip_allocator import IPAllocator
from ..wireguard.key_manager import KeyManager
from ..wireguard.manager import WireGuardManager
from ..wireguard.peer_manager import Peer, PeerManager
from ..wireguard.server_config import ServerConfig
from ..wireguard.status import StatusParser
from ..wireguard.interfaces import (
    config_path as interface_config_path,
    configured_interfaces,
    get_selected_interface,
    set_selected_interface,
    validate_interface_name,
)

console = Console()

from .common import (
    clear_screen,
    section_banner,
    pause,
    confirm,
    menu_option,
    show_banner,
    check_root,
    show_header_info,
    selected_interface,
)


# ── Navigation (CLI menus) ───────────────────────────────────────────
from .router import main_menu
from .server_menu import server_menu
from .peers_menu import peers_menu
from .firewall_menu import firewall_menu
from .diagnostics_menu import diagnostics_menu
from .backup_menu import backup_menu
from .logs_menu import logs_menu
from .system_info_menu import system_info_menu, version_menu

# ── Views (display only) ─────────────────────────────────────────────
from ..views.server_status import show_server_status, _wg_runtime_dashboard
from ..views.peer_views import list_peers, peer_table, show_peer, show_peer_selection
from ..views.backup_views import list_backups
from ..views.log_views import view_logs, clear_logs, export_logs
from ..views.qr_display import show_qr_code

# ── Commands (business logic) ────────────────────────────────────────
from ..commands.configure_server import (
    configure_server,
    normalize_wireguard_config,
    repair_wireguard_config_file,
    prompt_server_config,
    validate_server_settings,
)
from ..commands.key_rotation import rotate_server_keys
from ..commands.lifecycle import remove_wireguard, reinstall_wireguard
from ..commands.install_wireguard import install_wireguard
from ..commands.peer_crud import create_peer
from ..commands.peer_lifecycle import (
    disable_peer,
    enable_peer,
    revoke_peer,
    rotate_peer_keys,
    remove_peer,
)
from ..commands.firewall_commands import start_firewall, enable_firewall, open_wg_port
from ..commands.diagnostics_commands import (
    run_full_diagnostics,
    run_system_diagnostics,
    run_network_diagnostics,
    run_wireguard_diagnostics,
    run_firewall_diagnostics,
)
from ..commands.backup_commands import create_backup, restore_backup, delete_backup
from ..commands.peer_expiry import enforce_expired_peers, download_peer_config, set_peer_expiry
from ..commands.peer_import import import_server_peers
