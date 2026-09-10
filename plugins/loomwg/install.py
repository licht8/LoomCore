"""Host package installation for WireGuard prerequisites."""

from __future__ import annotations

import subprocess
import os
import re
import shutil
import tempfile
from ipaddress import ip_network
from pathlib import Path

from config import config_path
from keys import generate_keypair
from wireguard import run_command


def _host_shell(command: str, timeout: int = 300) -> str:
    if shutil.which("nsenter") and shutil.which("chroot"):
        command = ["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "-p", "--", "chroot", "/host", "/bin/sh", "-c", command]
    else:
        command = ["/bin/sh", "-c", command]
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        details = result.stderr.strip() or result.stdout.strip() or "нет деталей"
        raise RuntimeError(f"Установка WireGuard завершилась с кодом {result.returncode}: {details}")
    return result.stdout.strip()


def install_wireguard(_: dict) -> dict:
    try:
        _host_shell("command -v wg >/dev/null")
    except RuntimeError:
        pass
    else:
        return {
            "installed": True,
            "changed": False,
            "package_manager": None,
            "message": "WireGuard уже установлен",
        }

    manager = _host_shell(
        "if command -v apt-get >/dev/null; then printf apt; "
        "elif command -v dnf >/dev/null; then printf dnf; "
        "elif command -v yum >/dev/null; then printf yum; "
        "elif command -v apk >/dev/null; then printf apk; "
        "else printf unsupported; fi"
    )
    if manager == "unsupported":
        raise RuntimeError("Не найден поддерживаемый пакетный менеджер на хосте")
    commands = {
        "apt": "export DEBIAN_FRONTEND=noninteractive; apt-get update && apt-get install -y wireguard-tools iproute2",
        "dnf": "dnf install -y wireguard-tools iproute",
        "yum": "yum install -y wireguard-tools iproute",
        "apk": "apk add --no-cache wireguard-tools iproute2",
    }
    _host_shell(commands[manager])
    run_command(["wg", "--version"])
    return {
        "installed": True,
        "changed": True,
        "package_manager": manager,
        "message": f"WireGuard установлен через {manager}. Конфигурация wg0 не создавалась.",
    }


def setup_server(arguments: dict) -> dict:
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
    content = "\n".join(
        [
            "[Interface]",
            f"Address = {address_v4}, {address_v6}",
            f"PrivateKey = {keypair['private_key']}",
            f"ListenPort = {listen_port}",
            f"DNS = {', '.join(str(item).strip() for item in dns)}",
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

    return {
        "interface": interface,
        "config_path": str(path),
        "server_public_key": keypair["public_key"],
        "ipv4_network": str(v4),
        "ipv6_network": str(v6),
        "listen_port": listen_port,
        "dns": dns,
        "message": f"Серверная конфигурация {interface} создана. Интерфейс ещё не запускался.",
    }
