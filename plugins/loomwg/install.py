"""Install helpers for WireGuard prerequisites.

WARNING: install_wireguard() is a NO-OP inside the container.
Package installation must be done on the host via deploy/install.sh
before starting the container.

setup_server() now runs entirely inside the container using
cap_add NET_ADMIN for firewall/NAT and sysctl configuration.
"""

from __future__ import annotations

import os
import subprocess
import re
import shutil
import tempfile
from ipaddress import ip_network
from pathlib import Path

from config import config_path
from keys import generate_keypair
from wireguard import run_command


def _container_shell(command: str, timeout: int = 300) -> str:
    """Выполнить команду внутри контейнера (без nsenter/chroot).

    Контейнер имеет NET_ADMIN capability, поэтому команды
    iptables/nft/sysctl выполняются напрямую.
    """
    result = subprocess.run(
        ["/bin/sh", "-c", command],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        details = result.stderr.strip() or result.stdout.strip() or "нет деталей"
        raise RuntimeError(
            f"Команда завершилась с кодом {result.returncode}: {details}"
        )
    return result.stdout.strip()


def _detect_host() -> dict[str, str]:
    """Определить ОС, внешний интерфейс и firewall внутри контейнера."""
    return {
        "os": _container_shell(
            "test -r /etc/os-release && . /etc/os-release && "
            "printf '%s %s' \"${ID:-unknown}\" \"${VERSION_ID:-unknown}\""
        ),
        "external_interface": _container_shell(
            "ip -4 route show default | awk 'NR==1 {print $5}'"
        ),
        "firewall": _container_shell(
            "if command -v firewall-cmd >/dev/null; then printf firewalld; "
            "elif command -v ufw >/dev/null; then printf ufw; "
            "elif command -v nft >/dev/null; then printf nftables; "
            "elif command -v iptables >/dev/null; then printf iptables; "
            "else printf none; fi"
        ),
    }


def _configure_firewall(
    firewall: str,
    interface: str,
    external_interface: str,
    listen_port: int,
    ipv4_network: str,
) -> str:
    """Настроить firewall внутри контейнера (без nsenter/chroot)."""
    if firewall == "firewalld":
        _container_shell(
            f"firewall-cmd --permanent --add-port={listen_port}/udp && "
            "firewall-cmd --permanent --add-masquerade && firewall-cmd --reload"
        )
    elif firewall == "ufw":
        _container_shell(f"ufw allow {listen_port}/udp")
        _container_shell("ufw route allow in on " + interface + " out on " + external_interface)
    elif firewall == "nftables":
        _container_shell(
            "nft add table inet loomwg 2>/dev/null || true; "
            "nft 'add chain inet loomwg input { type filter hook input priority 0; policy accept; }' 2>/dev/null || true; "
            f"nft add rule inet loomwg input udp dport {listen_port} accept 2>/dev/null || true; "
            "nft 'add chain inet loomwg postrouting { type nat hook postrouting priority 100; }' 2>/dev/null || true; "
            f"nft add rule inet loomwg postrouting oifname \"{external_interface}\" masquerade 2>/dev/null || true"
        )
    elif firewall == "iptables":
        _container_shell(
            f"iptables -C INPUT -p udp --dport {listen_port} -j ACCEPT 2>/dev/null || "
            f"iptables -I INPUT -p udp --dport {listen_port} -j ACCEPT; "
            f"iptables -t nat -C POSTROUTING -s {ipv4_network} -o {external_interface} -j MASQUERADE 2>/dev/null || "
            f"iptables -t nat -A POSTROUTING -s {ipv4_network} -o {external_interface} -j MASQUERADE"
        )
    else:
        raise RuntimeError(
            "Не найден поддерживаемый firewall. Откройте UDP-порт и настройте NAT вручную."
        )
    return firewall


def _enable_forwarding() -> None:
    """Включить forwarding через sysctl.d внутри контейнера."""
    _container_shell(
        "printf 'net.ipv4.ip_forward=1\\nnet.ipv6.conf.all.forwarding=1\\n' > "
        "/etc/sysctl.d/99-loomwg-forwarding.conf && sysctl --system"
    )


def install_wireguard(_: dict) -> dict:
    """Проверить, установлен ли WireGuard.

    Это NO-OP внутри контейнера — установка пакетов выполняется
    на хосте через deploy/install.sh.
    """
    try:
        run_command(["wg", "--version"])
    except RuntimeError:
        pass
    else:
        return {
            "installed": True,
            "changed": False,
            "package_manager": None,
            "message": "WireGuard уже установлен внутри контейнера.",
        }
    # wg не найден — установка должна быть выполнена на хосте
    return {
        "installed": False,
        "changed": False,
        "package_manager": None,
        "message": "WireGuard не найден. Установите пакеты на хосте через deploy/install.sh.",
    }


def setup_server(arguments: dict) -> dict:
    """Настроить WireGuard-сервер внутри контейнера.

    Работает полностью внутри контейнера:
    - Конфиг записывается в /etc/wireguard (смонтирован rw)
    - Firewall/NAT настраивается через iptables/nft (cap_add NET_ADMIN)
    - Forwarding включается через /etc/sysctl.d
    """
    interface = str(arguments.get("interface", "wg0")).strip()
    if not re.fullmatch(r"[A-Za-z0-9]{1,15}", interface):
        raise ValueError("Некорректное имя WireGuard-интерфейса")

    ipv4_network = str(arguments.get("ipv4_network", "10.66.66.0/24")).strip()
    ipv6_network = str(arguments.get("ipv6_network", "fd42:42:42::/64")).strip()
    try:
        v4 = ip_network(ipv4_network)
        v6 = ip_network(ipv6_network)
    except ValueError as exc:
        raise ValueError("Некорректная IPv4 или IPv6 сеть") from exc
    if v4.version != 4 or v6.version != 6:
        raise ValueError("Нужно указать IPv4-сеть и IPv6-сеть")

    listen_port = int(arguments.get("listen_port", 51820))
    if not 1 <= listen_port <= 65535:
        raise ValueError("Порт должен быть в диапазоне 1-65535")
    dns = arguments.get("dns", ["1.1.1.1", "1.0.0.1"])
    if not isinstance(dns, list) or not dns or any(not str(item).strip() for item in dns):
        raise ValueError("dns должен быть непустым массивом адресов")

    path = config_path(interface)
    if path.exists():
        raise ValueError(f"Конфигурация {path} уже существует")

    run_command(["wg", "--version"])

    keypair = generate_keypair()
    address_v4 = f"{v4.network_address + 1}/{v4.prefixlen}"
    address_v6 = f"{v6.network_address + 1}/{v6.prefixlen}"
    host = _detect_host()
    external_interface = str(arguments.get("external_interface") or host["external_interface"]).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,32}", external_interface):
        raise ValueError("Не удалось определить внешний сетевой интерфейс")
    content = "\n".join(
        [
            "[Interface]",
            f"Address = {address_v4}, {address_v6}",
            f"PrivateKey = {keypair['private_key']}",
            f"ListenPort = {listen_port}",
            f"DNS = {', '.join(str(item).strip() for item in dns)}",
            "PostUp = iptables -A FORWARD -i %i -j ACCEPT; iptables -A FORWARD -o %i -j ACCEPT; iptables -t nat -A POSTROUTING -o "
            + external_interface
            + " -j MASQUERADE; ip6tables -A FORWARD -i %i -j ACCEPT; ip6tables -A FORWARD -o %i -j ACCEPT",
            "PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -D FORWARD -o %i -j ACCEPT; iptables -t nat -D POSTROUTING -o "
            + external_interface
            + " -j MASQUERADE; ip6tables -D FORWARD -i %i -j ACCEPT; ip6tables -D FORWARD -o %i -j ACCEPT",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise

    _enable_forwarding()
    firewall = _configure_firewall(
        host["firewall"],
        interface,
        external_interface,
        listen_port,
        str(v4),
    )

    return {
        "interface": interface,
        "config_path": str(path),
        "server_public_key": keypair["public_key"],
        "ipv4_network": str(v4),
        "ipv6_network": str(v6),
        "listen_port": listen_port,
        "dns": dns,
        "external_interface": external_interface,
        "firewall": firewall,
        "forwarding_enabled": True,
        "message": f"Серверная конфигурация {interface} создана. Интерфейс ещё не запускался.",
    }
