"""Common UI utilities used across all LoomWG menus."""

import os

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text
from rich.columns import Columns

from ..system.info import SystemDetector
from ..wireguard.interfaces import get_selected_interface, set_selected_interface, configured_interfaces

console = Console()

# ─── Purple/Lavender Theme ───────────────────────────────────────
THEME = {
    "PRIMARY": "#7c3aed",        # Deep purple
    "SECONDARY": "#a78bfa",      # Medium purple
    "ACCENT": "#c4b5fd",         # Lavender
    "MUTED": "#8b7cf6",          # Purple gray
    "SUCCESS": "#34d399",        # Soft green
    "ERROR": "#f87171",          # Soft red
    "WARNING": "#fbbf24",        # Soft amber
    "INFO": "#67e8f9",           # Soft cyan
    "TEXT": "#ffffff",           # White
    "DIM": "#a78bfa",            # Purple muted
}


# ─── Banner (direct ASCII art, gradient-colored per line) ─────────────
# Public constant: change this string to swap the banner art without
# touching the rendering code in ``show_banner()`` or ``_gradient_color()``.
BANNER_TEXT: str = """

                    ██╗      ██████╗  ██████╗ ███╗   ███╗██╗    ██╗ ██████╗ 
                    ██║     ██╔═══██╗██╔═══██╗████╗ ████║██║    ██║██╔════╝ 
                    ██║     ██║   ██║██║   ██║██╔████╔██║██║ █╗ ██║██║  ███╗
                    ██║     ██║   ██║██║   ██║██║╚██╔╝██║██║███╗██║██║   ██║
                    ███████╗╚██████╔╝╚██████╔╝██║ ╚═╝ ██║╚███╔███╔╝╚██████╔╝
                    ╚══════╝ ╚═════╝  ╚═════╝ ╚═╝     ╚═╝ ╚══╝╚══╝  ╚═════╝  

"""


def t(color: str, text: str, bold: bool = False) -> str:
    """Format text with theme color."""
    bold_str = "bold " if bold else ""
    return f"[{bold_str}{color}]{text}[/{bold_str}{color}]"


def primary_text(text: str, bold: bool = False):
    """Purple primary heading text."""
    return t("PRIMARY", text, bold)


def secondary_text(text: str):
    """Medium purple secondary text."""
    return t("SECONDARY", text)


def success_text(text: str):
    """Soft green success text."""
    return t("SUCCESS", text)


def error_text(text: str):
    """Soft red error text."""
    return t("ERROR", text)


def warning_text(text: str):
    """Soft amber warning text."""
    return t("WARNING", text)


def info_text(text: str):
    """Soft cyan info text."""
    return t("INFO", text)


def muted_text(text: str):
    """Purple muted secondary text."""
    return t("DIM", text)

__all__ = [
    "clear_screen",
    "pause",
    "confirm",
    "menu_option",
    "show_banner",
    "check_root",
    "show_header_info",
    "section_banner",
    "selected_interface",
    "select_interface",
]


def selected_interface() -> str:
    """Return the interface selected for the current CLI workflow."""
    return get_selected_interface()


def select_interface() -> None:
    """Select a configured WireGuard interface for subsequent operations."""
    from ..wireguard.interfaces import configured_interfaces, set_selected_interface

    interfaces = configured_interfaces()
    console.print("\n[bold]WireGuard Interfaces[/]")
    for index, name in enumerate(interfaces, 1):
        marker = " (selected)" if name == selected_interface() else ""
        console.print(f"{index}. {name}{marker}")
    console.print(f"{len(interfaces) + 1}. Create Interface")
    console.print(f"{len(interfaces) + 2}. Back")

    try:
        console.print("[bold #7c3aed]Select interface:[/] ", end="")
        choice = input().strip()
    except (EOFError, KeyboardInterrupt, OSError, UnicodeDecodeError):
        console.print("[red]Input interrupted.[/]")
        return

    try:
        index = int(choice)
    except ValueError:
        console.print("[red]Invalid selection.[/]")
        pause()
        return

    if 1 <= index <= len(interfaces):
        set_selected_interface(interfaces[index - 1])
        console.print(f"[green]✓ Selected {selected_interface()}[/]")
        pause()
    elif index == len(interfaces) + 1:
        create_interface()
    elif index != len(interfaces) + 2:
        console.print("[red]Invalid selection.[/]")
        pause()


def create_interface() -> None:
    """Create a new isolated WireGuard interface configuration."""
    import re
    from pathlib import Path

    from ..wireguard.config_generator import ConfigGenerator
    from ..wireguard.key_manager import KeyManager
    from ..wireguard.server_config import ServerConfig
    from ..wireguard.interfaces import (
        configured_interfaces,
        validate_interface_name,
        config_path as interface_config_path,
    )

    try:
        name = input("Interface name: ").strip()
    except (EOFError, KeyboardInterrupt, OSError, UnicodeDecodeError):
        console.print("[red]Input interrupted.[/]")
        pause()
        return

    if not validate_interface_name(name) or name == "wg0":
        console.print("[red]Invalid or duplicate interface name.[/]")
        pause()
        return
    path = interface_config_path(name)
    if path.exists():
        console.print("[red]Interface already exists.[/]")
        pause()
        return

    defaults = ServerConfig.defaults(interface=name)
    match = re.fullmatch(r"wg([1-9][0-9]*)", name)
    if match:
        interface_number = int(match.group(1))
        subnet_octet = 66 + (interface_number * 11)
        if subnet_octet <= 254:
            defaults.listen_port = 51820 + interface_number
            defaults.ipv4_network = f"10.{subnet_octet}.{subnet_octet}.0/24"
            defaults.ipv6_network = f"fd42:{subnet_octet:x}:{subnet_octet:x}::/64"
    console.print("[dim]Press Enter to accept the value in brackets.[/dim]")
    try:
        port_text = input(f"Listening UDP port [{defaults.listen_port}]: ").strip()
    except (EOFError, KeyboardInterrupt, OSError, UnicodeDecodeError):
        console.print("[red]Input interrupted.[/]")
        pause()
        return None
    try:
        ipv4_input = input(f"VPN IPv4 network [{defaults.ipv4_network}]: ").strip()
    except (EOFError, KeyboardInterrupt, OSError, UnicodeDecodeError):
        console.print("[red]Input interrupted.[/]")
        pause()
        return None
    ipv4 = ipv4_input or defaults.ipv4_network
    try:
        ipv6_input = input(f"VPN IPv6 network [{defaults.ipv6_network}]: ").strip()
    except (EOFError, KeyboardInterrupt, OSError, UnicodeDecodeError):
        console.print("[red]Input interrupted.[/]")
        pause()
        return None
    ipv6 = ipv6_input or defaults.ipv6_network
    try:
        port = int(port_text) if port_text else defaults.listen_port
        candidate = ServerConfig(
            wg_interface=name,
            listen_port=port,
            ipv4_network=ipv4,
            ipv6_network=ipv6,
            dns_primary=defaults.dns_primary,
            dns_secondary=defaults.dns_secondary,
            allowed_ips=defaults.allowed_ips,
        )
        valid, errors = candidate.validate()
        for existing in configured_interfaces():
            if existing == name:
                errors.append(f"Interface {name} already exists")
                continue
            existing_path = interface_config_path(existing)
            if not existing_path.exists():
                continue
            existing_cfg = ServerConfig.from_file(existing_path)
            if candidate.get_ipv4_network().overlaps(existing_cfg.get_ipv4_network()):
                errors.append(f"IPv4 network overlaps interface {existing}")
            if candidate.get_ipv6_network().overlaps(existing_cfg.get_ipv6_network()):
                errors.append(f"IPv6 network overlaps interface {existing}")
        if errors:
            console.print("[red]Interface configuration invalid:[/]")
            for error in errors:
                console.print(f"  - {error}")
            pause()
            return
        keypair = KeyManager.generate_keypair()
        content = ConfigGenerator.generate_server_config(
            candidate.get_ipv4_server_address(),
            candidate.get_ipv6_server_address(),
            candidate.listen_port,
            keypair.private_key,
        )
        if not ConfigGenerator.write_config(path, content):
            raise RuntimeError("Failed to write interface configuration")
        set_selected_interface(name)
        console.print(f"[green]✓ Interface {name} created and selected[/]")
    except (RuntimeError, ValueError) as exc:
        console.print(f"[red]✗ Failed to create interface: {exc}[/]")
    pause()


def clear_screen() -> None:
    """Clear terminal screen."""
    os.system("cls" if os.name == "nt" else "clear")


def section_banner(title: str, subtitle: str | None = None) -> None:
    """Display a consistent purple banner for a CLI section."""
    # NOTE: We do NOT call clear_screen() here — the banner drawn by
    # show_banner() must remain visible on screen.  Each menu file is
    # responsible for clearing before entering its loop.
    
    panel_title = Text(title, style=f"bold {THEME['PRIMARY']}")
    if subtitle:
        panel_title.append("\n")
        panel_title.append(subtitle, style=f"dim {THEME['DIM']}")
    
    console.print(Panel(panel_title, border_style=THEME["PRIMARY"], padding=(1, 2)))
    console.print()


def pause() -> None:
    """Pause and wait for user input."""
    try:
        input("\nPress Enter to continue...")
    except (EOFError, KeyboardInterrupt, OSError, UnicodeDecodeError):
        pass



def confirm(prompt: str = "Continue?") -> bool:
    """Ask for confirmation."""
    while True:
        try:
            response = input(f"\n{prompt} (y/n): ").strip().lower()
        except (UnicodeDecodeError, EOFError, OSError):
            console.print("[red]Invalid response.[/]")
            continue

        if response in ("y", "yes"):
            return True

        if response in ("n", "no"):
            return False

        console.print("Invalid response. Please enter 'y' or 'n'.")


def _gradient_color(index: int, total: int) -> str:
    """Return a hex color interpolated between ``THEME['ACCENT']`` and ``THEME['PRIMARY']``.

    Used to color each line of the banner with a top-to-bottom
    gradient. The parameter ``index`` is the 0-based line number and
    ``total`` is the line count. With ``total > 1`` the helper
    interpolates linearly between ``THEME['ACCENT']`` (lavender) at
    ``index=0`` and ``THEME['PRIMARY']`` (deep purple) at
    ``index=total-1`` using ``t = index / (total - 1)``.

    Boundary cases:

    * ``total <= 0`` collapses to ``ACCENT`` (degenerate empty input).
    * ``index < 0`` is clamped to ``0`` so a programming error
      never produces a negative component.
    * ``index >= total`` extrapolates to ``PRIMARY``, matching the
      spec in TZ 031 §5 / test 3: ``_gradient_color(1, 1)``
      returns ``PRIMARY``.
    * The interpolation formula ``t = index / (total - 1)`` with the
      degenerate single-line substitution ``t = index / max(total - 1, 1)``
      ensures ``_gradient_color(0, 1)`` returns ``ACCENT`` and
      ``_gradient_color(1, 1)`` returns ``PRIMARY``.

    Split out from ``show_banner()`` so the math is unit-testable
    without spinning up a Rich ``Console``.
    """
    if total <= 0:
        return THEME["ACCENT"]
    if total == 1:
        return THEME["PRIMARY"]
    safe_index = max(0, index)
    t_value = safe_index / (total - 1)
    accent = int(THEME["ACCENT"][1:3], 16), int(THEME["ACCENT"][3:5], 16), int(THEME["ACCENT"][5:7], 16)
    primary = int(THEME["PRIMARY"][1:3], 16), int(THEME["PRIMARY"][3:5], 16), int(THEME["PRIMARY"][5:7], 16)
    r = int(accent[0] * (1 - t_value) + primary[0] * t_value)
    g = int(accent[1] * (1 - t_value) + primary[1] * t_value)
    b = int(accent[2] * (1 - t_value) + primary[2] * t_value)
    return f"#{r:02x}{g:02x}{b:02x}"


def show_banner() -> None:
    """Display the LoomWG banner — no panel, no rule, raw ASCII art.

    The banner is taken directly from ``BANNER_TEXT`` (no pyfiglet) so the
    output is exact and predictable.  Each line is colored with a per-line
    gradient from ``THEME['ACCENT']`` (lavender) to ``THEME['PRIMARY']``
    (deep purple) via the ``_gradient_color`` helper.
    """
    clear_screen()

    banner_lines = BANNER_TEXT.split("\n")
    total = len(banner_lines)

    colored_lines = [
        f"[{_gradient_color(i, total)}]{line}[/{_gradient_color(i, total)}]"
        for i, line in enumerate(banner_lines)
    ]
    console.print("\n".join(colored_lines))
    console.print()




def display_peer_qr_code(peer_name: str, config_content: str) -> None:
    """Display terminal QR output using the system qrencode feature when available."""
    from ..wireguard.client_config import ClientConfigStore
    qr_text = ClientConfigStore.render_qr_ansi(config_content)
    if qr_text is None:
        console.print("[yellow]Warning: QR generation is unavailable because qrencode is not installed.[/]")
        return
    console.print(f"\n[bold]QR code for {peer_name}[/]\n")
    print(qr_text)




def prompt_for_qr_code(peer_name: str | None = None) -> bool:
    """Prompt the user to show a QR code for a generated client config."""
    prompt = "Show QR code for this peer? (Y/n): "
    while True:
        try:
            response = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt, OSError, UnicodeDecodeError):
            console.print("[red]Input interrupted.[/]")
            return False

        if response in ("", "y", "yes"):
            return True
        if response in ("n", "no"):
            return False
        print("Invalid response. Please enter 'y' or 'n'.")

def check_root() -> bool:
    """Check if running as root."""
    if not SystemDetector().detect().is_root:
        console.print(
            Panel(
                "[red]ERROR[/]\n\n"
                "LoomWG must be run as root.\n"
                "Please run again with sudo.",
                title="[bold]Permission Denied[/]"
            )
        )
        return False
    return True


def show_header_info() -> None:
    """Show system header information as a vertical list (no panel)."""
    from ..system.info import SystemDetector
    from ..wireguard.manager import WireGuardManager
    
    detector = SystemDetector()
    info = detector.detect()
    wg_manager = WireGuardManager()
    
    server_text = f"[bold {THEME['INFO']}]{info.hostname}[/]"
    console.print(f"  Server: {server_text}")
    console.print(f"  OS: {info.os_name.split()[0]} {info.os_version or ''}".strip())
    console.print(f"  Kernel: {info.kernel or 'N/A'}")
    
    if wg_manager.is_installed():
        interfaces = wg_manager.get_interfaces()
        status = f"{len(interfaces)} interface(s)" if interfaces else "no interfaces"
        console.print(f"  WireGuard: [bold {THEME['SUCCESS']}]Installed ({status})[/]")
    else:
        console.print(f"  WireGuard: [bold {THEME['WARNING']}]Not installed[/]")
    
    console.print()


def menu_option(
    number: int, title: str, description: str = "", command: str | None = None
) -> None:
    """Render a menu item with two-tab indentation."""
    primary = THEME["PRIMARY"]
    dim = THEME["DIM"]
    
    num = f"[bold {primary}] {number}.[/]"
    title_text = f"[bold white]{title}[/]"
    
    line = f"  {num} {title_text}"
    
    # Two tabs (8 spaces) after title, then dim description
    if description:
        line += f"        [dim]{description}[/]"
    if command:
        line += f"        [dim]({command})[/]"
    
    console.print(line)

def manage_interfaces() -> None:
    """Manage interface selection, creation, and deletion."""
    while True:
        show_banner()
        interfaces = configured_interfaces()
        
        section_banner("Manage Interfaces", "WireGuard interface management")
        
        if not interfaces:
            console.print(f"[{THEME['WARNING']}]No interfaces configured.[/]")
            pause()
            return
        
        for index, name in enumerate(interfaces, 1):
            marker = f" ([dim {THEME['DIM']}]selected[/])" if name == selected_interface() else ""
            if name == selected_interface():
                style_text = f"[{THEME['SUCCESS']}] {index}.[/]"
            else:
                style_text = f"[bold] {index}.[/]"
            console.print(f"  {style_text} {name}{marker}")
        
        console.print()
        console.print(f"  [bold {THEME['PRIMARY']}] ci.[/] Create Interface")
        console.print(f"  [bold {THEME['PRIMARY']}] di.[/] Delete selected interface")
        console.print()
        console.print(f"  [bold {THEME['PRIMARY']}] 0.[/] Back")
        console.print()
        
        console.print(f"[bold {THEME['PRIMARY']}]Select option:[/] ", end="")
        try:
            choice = input().strip()
        except (EOFError, KeyboardInterrupt, OSError, UnicodeDecodeError):
            console.print(f"[{THEME['ERROR']}]Input interrupted.[/]")
            return
        
        if choice.lower() == "ci":
            create_interface()
        elif choice.lower() == "di":
            delete_interface()
        elif choice == "0":
            return
        else:
            try:
                index = int(choice)
            except ValueError:
                console.print(f"[{THEME['WARNING']}]Invalid selection.[/]")
                pause()
                continue
            
            if not 1 <= index <= len(interfaces):
                console.print(f"[{THEME['WARNING']}]Invalid selection.[/]")
                pause()
                continue
            set_selected_interface(interfaces[index - 1])
            console.print(f"[{THEME['SUCCESS']}]Selected {selected_interface()}[/]")
            pause()



def delete_interface() -> None:
    """Delete a non-default interface and its isolated state after confirmation."""
    from ..wireguard.manager import WireGuardManager
    from ..system.services import ServiceManager
    from ..logging_system.logger import LoomLogger
    from ..wireguard.interfaces import config_path as interface_config_path

    interface = selected_interface()
    if interface == "wg0":
        console.print("[yellow]The default wg0 interface cannot be deleted here.[/]")
        pause()
        return

    path = interface_config_path(interface)
    if not path.exists():
        console.print(f"[red]Interface {interface} does not exist.[/]")
        pause()
        return

    console.print(f"[bold red]Delete Interface {interface}[/bold red]")
    console.print(f"Configuration: {path}")
    console.print("This removes the interface configuration, peer database, and client configs.")
    try:
        confirmation = input(f"Type DELETE {interface} to continue: ").strip()
    except (EOFError, KeyboardInterrupt, OSError, UnicodeDecodeError):
        console.print("[red]Input interrupted.[/]")
        pause()
        return
    if confirmation != f"DELETE {interface}":
        console.print("[yellow]Interface deletion cancelled.[/]")
        pause()
        return

    manager = WireGuardManager()
    service = ServiceManager()
    if manager.is_interface_active(interface) and not manager.stop(interface):
        console.print(f"[red]Could not stop {interface}; nothing was deleted.[/]")
        pause()
        return
    if service.is_enabled(f"wg-quick@{interface}") and not service.disable(f"wg-quick@{interface}"):
        console.print(f"[red]Could not disable wg-quick@{interface}; nothing was deleted.[/]")
        pause()
        return

    from pathlib import Path
    project_root = Path(__file__).resolve().parents[2]
    paths = [
        path,
        project_root / "data" / "interfaces" / interface,
    ]
    try:
        import shutil
        for target in paths:
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
        set_selected_interface("wg0")
        LoomLogger().info(f"WireGuard interface '{interface}' deleted", "server")
        console.print(f"[green]✓ Interface {interface} deleted[/]")
    except OSError as exc:
        console.print(f"[red]Failed to delete interface {interface}: {exc}[/]")
    pause()
