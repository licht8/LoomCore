"""Secure client configuration generation for WireGuard."""

from __future__ import annotations

import base64
import contextlib
import os
import re
import secrets
import subprocess
import time
import urllib.error
import urllib.request
from ipaddress import ip_address, ip_interface, ip_network
from pathlib import Path

from keys import generate_keypair
from wireguard import CommandError, run_command

from config import add_peer, config_path

CLIENTS_DIR = Path("/etc/wireguard/clients")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
ARTIFACT_TTL_SECONDS = 600
_artifacts: dict[str, tuple[str, float]] = {}


def _format_endpoint(endpoint: str, port: int) -> str:
    """Format endpoint, bracketing IPv6 addresses per WireGuard spec."""
    try:
        ip_address(endpoint)
    except ValueError:
        pass
    else:
        if ":" in endpoint:
            endpoint = f"[{endpoint}]"
    return f"{endpoint}:{port}"


def clients_directory() -> Path:
    return CLIENTS_DIR


def _safe_name(value: object) -> str:
    name = str(value or "").strip()
    if not NAME_RE.fullmatch(name):
        raise ValueError("Имя клиента содержит недопустимые символы")
    return name


def _detect_endpoint() -> str:
    """Detect the public address visible from the Internet."""
    for service in ("https://api64.ipify.org", "https://api.ipify.org"):
        try:
            with urllib.request.urlopen(service, timeout=5) as response:
                value = response.read().decode("ascii").strip()
            address = ip_address(value)
            return str(address)
        except (OSError, ValueError, urllib.error.URLError):
            continue
    raise ValueError(
        "Не удалось определить публичный адрес сервера. "
        "Укажите endpoint или настройте LOOMWG_ENDPOINT."
    )


def _server_settings(interface: str) -> tuple[str, int, list[str], list[str]]:
    path = config_path(interface)
    if not path.is_file():
        raise FileNotFoundError(f"Конфигурация {path} не найдена")
    private_key = ""
    listen_port = 51820
    server_networks: list[str] = []
    server_addresses: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if not separator:
            continue
        if key.strip() == "PrivateKey":
            private_key = value.strip()
        elif key.strip() == "ListenPort":
            listen_port = int(value.strip())
        elif key.strip() == "Address":
            for raw_address in value.split(","):
                address = ip_interface(raw_address.strip())
                server_networks.append(str(address.network))
                server_addresses.append(str(address.ip))
    if not private_key:
        raise ValueError("В конфигурации сервера отсутствует PrivateKey")
    public_key = run_command(["wg", "pubkey"], input_text=private_key)
    return public_key, listen_port, server_networks, server_addresses


def _used_addresses(interface: str) -> set:
    path = config_path(interface)
    used = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() == "AllowedIPs":
            for raw_network in value.split(","):
                network = ip_network(raw_network.strip(), strict=False)
                if network.prefixlen in (32, 128):
                    used.add(network.network_address)
    try:
        output = run_command(["wg", "show", interface, "dump"])
    except CommandError:
        output = ""
    for line in output.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) >= 4:
            for raw_network in parts[3].split(","):
                network = ip_network(raw_network.strip(), strict=False)
                if network.prefixlen in (32, 128):
                    used.add(network.network_address)
    return used


def _allocate_addresses(
    interface: str,
    server_networks: list[str],
    server_addresses: list[str],
) -> list[str]:
    used = _used_addresses(interface)
    used.update(ip_address(address) for address in server_addresses)
    networks = [ip_network(raw_network) for raw_network in server_networks]
    ipv4_networks = [network for network in networks if network.version == 4]
    ipv6_networks = [network for network in networks if network.version == 6]

    for ipv4_network in ipv4_networks:
        for ipv4 in ipv4_network.hosts():
            if ipv4 in used:
                continue
            host_offset = int(ipv4) - int(ipv4_network.network_address)
            for ipv6_network in ipv6_networks:
                ipv6 = ip_address(int(ipv6_network.network_address) + host_offset)
                if ipv6 in ipv6_network and ipv6 not in used:
                    return [f"{ipv4}/32", f"{ipv6}/128"]
            if not ipv6_networks:
                return [f"{ipv4}/32"]
    raise ValueError("В WireGuard-сетях нет свободной пары IPv4/IPv6 для нового клиента")


def _client_addresses(arguments: dict, interface: str, server_networks: list[str], server_addresses: list[str]) -> list[str]:
    raw = arguments.get("addresses") or arguments.get("allowed_ips")
    if raw is None:
        return _allocate_addresses(interface, server_networks, server_addresses)
    if not isinstance(raw, list) or not raw:
        raise ValueError("addresses должен быть непустым массивом адресов")
    result = []
    for value in raw:
        try:
            address = ip_interface(str(value))
        except ValueError as exc:
            raise ValueError(f"Некорректный адрес клиента: {value}") from exc
        if address.network.prefixlen == 0:
            raise ValueError("Адрес клиента не может быть маршрутом по умолчанию")
        if address.network.prefixlen not in (32, 128):
            raise ValueError("Адрес клиента должен быть /32 или /128")
        result.append(str(address))
    if len(result) != len(set(result)):
        raise ValueError("addresses содержит дубликаты")
    return result


def _write_secret(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def remove_client_artifacts(public_key: str) -> list[str]:
    """Remove generated client files whose key matches a server peer."""
    removed: list[str] = []
    directory = clients_directory()
    if not directory.is_dir():
        return removed

    for client_path in directory.glob("*.conf"):
        private_key = ""
        for line in client_path.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() == "PrivateKey":
                private_key = value.strip()
                break
        if not private_key:
            continue
        client_public_key = run_command(["wg", "pubkey"], timeout=5, input_text=private_key)
        if client_public_key != public_key:
            continue

        name = client_path.stem
        client_path.unlink()
        qr_path = directory / f"{name}.png"
        if qr_path.exists():
            qr_path.unlink()
        for token, (artifact_name, _) in list(_artifacts.items()):
            if artifact_name == name:
                _artifacts.pop(token, None)
        removed.append(name)
    return removed


def create_client(arguments: dict) -> dict:
    interface = str(arguments.get("interface", "wg0"))
    name = _safe_name(arguments.get("name"))
    endpoint = str(arguments.get("endpoint") or os.environ.get("LOOMWG_ENDPOINT", "")).strip()
    if not endpoint:
        endpoint = _detect_endpoint()
    if any(char.isspace() for char in endpoint):
        raise ValueError("endpoint не должен содержать пробелы")
    server_public_key, listen_port, server_networks, server_addresses = _server_settings(interface)
    addresses = _client_addresses(arguments, interface, server_networks, server_addresses)
    used_addresses = _used_addresses(interface)
    for address in addresses:
        if not any(ip_interface(address).ip in ip_network(network) for network in server_networks):
            raise ValueError(f"Адрес клиента {address} не входит в сеть WireGuard")
        if ip_interface(address).ip in used_addresses or ip_interface(address).ip in {
            ip_address(value) for value in server_addresses
        }:
            raise ValueError(f"Адрес клиента {address} уже занят")
    keypair = generate_keypair()
    preshared_key = run_command(["wg", "genpsk"])
    client_path = clients_directory() / f"{name}.conf"
    qr_path = clients_directory() / f"{name}.png"
    if client_path.exists() or qr_path.exists():
        raise ValueError(f"Клиент '{name}' уже существует")

    client_config = "\n".join(
        [
            "[Interface]",
            f"Address = {', '.join(addresses)}",
            f"PrivateKey = {keypair['private_key']}",
            "DNS = 1.1.1.1, 1.0.0.1",
            "",
            "[Peer]",
            f"PublicKey = {server_public_key}",
            f"PresharedKey = {preshared_key}",
            f"Endpoint = {_format_endpoint(endpoint, listen_port)}",
            "AllowedIPs = 0.0.0.0/0, ::/0",
            "PersistentKeepalive = 25",
            "",
        ]
    )

    try:
        add_peer(interface, keypair["public_key"], addresses, preshared_key)
        run_command(
            [
                "wg",
                "set",
                interface,
                "peer",
                keypair["public_key"],
                "allowed-ips",
                ",".join(addresses),
                "preshared-key",
                "/dev/stdin",
            ],
            input_text=preshared_key,
        )
        _write_secret(client_path, client_config)
        qrencode = subprocess.run(
            ["qrencode", "-o", str(qr_path), "-t", "png"],
            input=client_config,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if qrencode.returncode != 0:
            raise CommandError(qrencode.stderr.strip() or "qrencode завершился с ошибкой")
        os.chmod(qr_path, 0o600)
    except BaseException:
        with contextlib.suppress(CommandError):
            run_command(["wg", "set", interface, "peer", keypair["public_key"], "remove"])
        for path in (client_path, qr_path):
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
        from config import remove_peer

        with contextlib.suppress(FileNotFoundError, ValueError):
            remove_peer(interface, keypair["public_key"])
        raise

    config_token = secrets.token_urlsafe(32)
    qr_token = secrets.token_urlsafe(32)
    expires_at = time.time() + ARTIFACT_TTL_SECONDS
    _artifacts[config_token] = (name, expires_at)
    _artifacts[qr_token] = (name, expires_at)
    return {
        "name": name,
        "interface": interface,
        "public_key": keypair["public_key"],
        "client_config": str(client_path),
        "qr_code": str(qr_path),
        "secret_persisted": True,
        "config_token": config_token,
        "qr_token": qr_token,
        "artifact_expires_in": ARTIFACT_TTL_SECONDS,
    }


def get_artifact(arguments: dict) -> dict:
    token = str(arguments.get("token", ""))
    kind = str(arguments.get("kind", ""))
    record = _artifacts.pop(token, None)
    if record is None:
        raise ValueError("Ссылка на артефакт недействительна или уже использована")
    name, expires_at = record
    if time.time() >= expires_at:
        raise ValueError("Срок действия ссылки истёк")
    if kind not in {"config", "qr"}:
        raise ValueError("Недопустимый тип артефакта")
    path = clients_directory() / f"{name}.{'conf' if kind == 'config' else 'png'}"
    if not path.is_file():
        raise FileNotFoundError("Файл артефакта не найден")
    raw = path.read_bytes()
    return {
        "filename": path.name,
        "media_type": "text/plain" if kind == "config" else "image/png",
        "content_base64": base64.b64encode(raw).decode("ascii"),
    }
