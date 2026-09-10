"""System info dashboard."""
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
console = Console()

from ..cli.common import THEME
from ..wireguard.server_config import ServerConfig
from ..wireguard.interfaces import config_path as interface_config_path
from ..wireguard.manager import WireGuardManager
from ..wireguard.peer_manager import PeerManager
from ..firewall.firewalld import FirewalldManager
from ..system.network import NetworkManager
from ..system.services import ServiceManager
from ..views.server_status import _wg_runtime_dashboard

import sys
import subprocess
from pathlib import Path

from ..system.info import SystemDetector
from ..cli.common import clear_screen, section_banner, pause, show_banner


def system_info_menu() -> None:
    """Display a comprehensive, read-only server and VPN information dashboard."""
    clear_screen()
    detector = SystemDetector()
    info = detector.detect()
    def command(*args: str) -> str:
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=5, check=False)
            return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else "N/A"
        except OSError:
            return "N/A"

    def section(title: str, rows: list[tuple[str, object]]) -> None:
        """Print aligned labels, preserving readable wrapping for long values."""
        table = Table.grid(padding=(0, 1), expand=True)
        table.add_column(style=f"bold {THEME['PRIMARY']}", width=22, no_wrap=True)
        table.add_column()
        for label, value in rows:
            table.add_row(f"{label}:", str(value) if value not in (None, "") else "N/A")
        console.print(f"\n[bold]{title}[/]")
        console.print(table)

    config_path = Path("/etc/wireguard/wg0.conf")
    config = ServerConfig.from_file(config_path)
    firewall = FirewalldManager()
    wg = WireGuardManager()
    dashboard = _wg_runtime_dashboard() if wg.is_interface_active("wg0") else None
    console.print(f"[bold]System Information[/]\n")
    section("Server", [("Hostname", info.hostname), ("OS", info.os_name), ("Kernel", info.kernel), ("Architecture", info.architecture), ("Init system", info.init_system), ("Package manager", info.package_manager), ("Uptime", command("uptime", "-p")), ("Boot time", command("uptime", "-s")), ("Date / timezone", command("date", "+%F %T %Z"))])
    section("Resources", [("CPU", command("sh", "-c", "lscpu | awk -F: '/Model name/ {print $2; exit}'")), ("CPU usage", command("sh", "-c", "top -bn1 | awk '/Cpu\\(s\\)/ {print $2 \"%\"; exit}'")), ("Memory", command("sh", "-c", "free -h | awk 'NR==2 {print \"total \" $2 \", used \" $3 \", available \" $7}'")), ("Swap", command("sh", "-c", "free -h | awk 'NR==3 {print \"total \" $2 \", used \" $3}'")), ("Disk /", command("sh", "-c", "df -h / | awk 'NR==2 {print \"used \" $3 \" of \" $2 \" (\" $5 \")\"}'")), ("Load average", command("sh", "-c", "cut -d' ' -f1-3 /proc/loadavg"))])
    section("Network", [("Public IPv4", info.public_ip or "N/A"), ("Default interface", info.default_interface or "N/A"), ("IPv4 gateway", command("sh", "-c", "ip route | awk '/default/ {print $3; exit}'")), ("IPv6 gateway", command("sh", "-c", "ip -6 route | awk '/default/ {print $3; exit}'")), ("DNS servers", command("sh", "-c", "awk '/^nameserver/ {print $2}' /etc/resolv.conf | paste -sd, -"))])
    section("Firewall & Security", [("firewalld installed", "YES" if firewall.is_installed() else "NO"), ("firewalld running", "YES" if firewall.is_running() else "NO"), ("Active zone", command("firewall-cmd", "--get-active-zones") if firewall.is_running() else "N/A"), ("IPv4 forwarding", "ENABLED" if NetworkManager().is_ipv4_forwarding_enabled() else "DISABLED"), ("SELinux", command("getenforce")), ("Current user", command("id", "-un")), ("Root privileges", "YES" if info.is_root else "NO")])
    section("WireGuard", [("Installed", "YES" if wg.is_installed() else "NO"), ("Tools version", command("wg", "--version")), ("Interfaces", ", ".join(wg.list_interfaces()) or "None"), ("Status", "RUNNING" if wg.is_interface_active("wg0") else "DOWN"), ("Listening port", config.listen_port if config_path.exists() else "N/A"), ("Server addresses", f"{config.get_ipv4_server_address()} / {config.get_ipv6_server_address()}"), ("Peers", dashboard["total"] if dashboard else 0), ("Active peers", dashboard["online"] if dashboard else 0), ("Traffic RX / TX", f"{dashboard['rx']} / {dashboard['tx']}" if dashboard else "0 B / 0 B")])
    section("WireGuard Service", [("Active", "YES" if ServiceManager().is_active("wg-quick@wg0") else "NO"), ("Enabled on boot", "YES" if ServiceManager().is_enabled("wg-quick@wg0") else "NO"), ("Configuration", config_path), ("Config exists", "YES" if config_path.exists() else "NO"), ("Config permissions", oct(config_path.stat().st_mode & 0o777) if config_path.exists() else "N/A")])
    section("VPN Network", [("IPv4 subnet", config.ipv4_network if config_path.exists() else "N/A"), ("IPv6 subnet", config.ipv6_network if config_path.exists() else "N/A"), ("IPv4 forwarding", "ENABLED" if NetworkManager().is_ipv4_forwarding_enabled() else "DISABLED"), ("IPv6 forwarding", "ENABLED" if NetworkManager().is_ipv6_forwarding_enabled() else "DISABLED"), ("NAT", "ENABLED" if firewall.is_masquerading_enabled() else "DISABLED")])
    section("LoomWG", [("Version", "0.1.0"), ("Python", sys.version.split()[0]), ("Executable", sys.executable), ("Working directory", Path.cwd()), ("Stored peers", len(PeerManager().list_peers()))])
    console.print("\n[bold]Runtime detail[/]")
    for title, details in [
        ("Interfaces", command("ip", "-brief", "address")),
        ("Routing table", command("ip", "route")),
        ("IPv6 routing table", command("ip", "-6", "route")),
        ("Listening TCP ports", command("ss", "-ltn")),
        ("Listening UDP ports", command("ss", "-lun")),
        ("WireGuard runtime", command("wg", "show", "wg0")),
        ("WireGuard service", command("systemctl", "status", "wg-quick@wg0", "--no-pager")),
    ]:
        console.print(Panel(details, title=title, border_style=f"dim {THEME['DIM']}"))

    pause()


def version_menu() -> None:
    """Show the installed LoomWG version and the most recent changelog entry."""
    clear_screen()
    version = "0.1.0"
    changelog = Path(__file__).resolve().parents[1] / "CHANGELOG.md"
    latest = "N/A"
    try:
        sections = changelog.read_text(encoding="utf-8").split("## [")
        if len(sections) > 1:
            latest = "## [" + sections[1].split("## [", 1)[0].strip()
    except OSError:
        pass
    console.print(f"[bold {THEME['PRIMARY']}]LoomWG {version}[/]\n\n[bold]Latest changes[/]\n{latest}")
    pause()


