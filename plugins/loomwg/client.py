"""Secure client configuration generation for WireGuard."""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import time
import base64
import urllib.error
import urllib.request
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_interface, ip_network
from pathlib import Path

from config import add_peer, config_path
from keys import generate_keypair
from wireguard import CommandError, run_command


CLIENTS_DIR = Path("/etc/wireguard/clients")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
ARTIFACT_TTL_SECONDS = 600
_artifacts: dict[str, tuple[str, float]] = {}


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


def _allocate_address(interface: str, server_networks: list[str], server_addresses: list[str]) -> str:
    used = _used_addresses(interface)
    used.update(ip_address(address) for address in server_addresses)
    for raw_network in server_networks:
        network = ip_network(raw_network)
        if network.version == 4:
            candidates = network.hosts()
        else:
            start = int(network.network_address) + 1
            candidates = (ip_address(value) for value in range(start, min(start + 65536, int(network.broadcast_address))))
        for candidate in candidates:
            if candidate not in used:
                return f"{candidate}/{32 if network.version == 4 else 128}"
    raise ValueError("В WireGuard-сетях нет свободного адреса для нового клиента")


def _client_addresses(arguments: dict, interface: str, server_networks: list[str], server_addresses: list[str]) -> list[str]:
    raw = arguments.get("addresses") or arguments.get("allowed_ips")
    if raw is None:
        return [_allocate_address(interface, server_networks, server_addresses)]
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
    client_path = CLIENTS_DIR / f"{name}.conf"
    qr_path = CLIENTS_DIR / f"{name}.png"
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
            f"Endpoint = {endpoint}:{listen_port}",
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
        try:
            run_command(["wg", "set", interface, "peer", keypair["public_key"], "remove"])
        except CommandError:
            pass
        for path in (client_path, qr_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        from config import remove_peer

        try:
            remove_peer(interface, keypair["public_key"])
        except (FileNotFoundError, ValueError):
            pass
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
    path = CLIENTS_DIR / f"{name}.{'conf' if kind == 'config' else 'png'}"
    if not path.is_file():
        raise FileNotFoundError("Файл артефакта не найден")
    raw = path.read_bytes()
    return {
        "filename": path.name,
        "media_type": "text/plain" if kind == "config" else "image/png",
        "content_base64": base64.b64encode(raw).decode("ascii"),
    }
