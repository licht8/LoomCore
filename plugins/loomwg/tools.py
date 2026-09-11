"""MCP tool definitions and WireGuard tool handlers."""

from __future__ import annotations

from typing import Any

from config import add_peer as save_peer, remove_peer as delete_peer
from client import create_client, get_artifact, remove_client_artifacts
from keys import validate_public_key
from lifecycle import format_lifecycle, restart, start, stop
from install import install_wireguard, setup_server
from wireguard import (
    CONFIG_DIR,
    CommandError,
    active_interfaces,
    configured_interfaces,
    has_link,
    peer_is_online,
    read_interface,
    run_command,
    config_directory,
    validate_interface,
)


def text_result(content: str, **data: Any) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": content}],
        "data": data,
    }


def list_interfaces(arguments: dict[str, Any]) -> dict[str, Any]:
    active = set(active_interfaces())
    configured = set(configured_interfaces())
    names = sorted(active | configured)
    rows = ["Interface | Active | Config", "-" * 31]
    for name in names:
        rows.append(
            f"{name:9} | {'yes' if name in active else 'no':6} | "
            f"{'yes' if name in configured else 'no'}"
        )
    return text_result(
        "\n".join(rows) if names else "WireGuard-интерфейсы не найдены",
        interfaces=[
            {
                "name": name,
                "active": name in active,
                "configured": name in configured,
            }
            for name in names
        ],
    )


def status(arguments: dict[str, Any]) -> dict[str, Any]:
    interface = validate_interface(arguments)
    if interface not in set(active_interfaces()):
        try:
            run_command(["wg-quick", "up", interface], timeout=60)
        except CommandError:
            return text_result(
                f"{interface}: DOWN\nПиров: 0\nАктивных: 0\nНеактивных: 0",
                interface=interface,
                active=False,
                peer_count=0,
                online_peers=0,
            )

    details = read_interface(interface)
    online = sum(peer_is_online(peer) for peer in details["peers"])
    peer_count = len(details["peers"])
    return text_result(
        f"{interface}: UP\n"
        f"Порт: {details['listen_port']}\n"
        f"Пиров: {peer_count}\n"
        f"Активных: {online}\n"
        f"Неактивных: {peer_count - online}",
        **details,
        active=True,
        online_peers=online,
    )


def list_peers(arguments: dict[str, Any]) -> dict[str, Any]:
    interface = validate_interface(arguments)
    if interface not in set(active_interfaces()):
        # Интерфейс настроен, но не запущен — пробуем автозапуск
        try:
            run_command(["wg-quick", "up", interface], timeout=60)
        except CommandError:
            raise CommandError(f"Интерфейс {interface} не активен")

    details = read_interface(interface)
    rows = [f"Интерфейс: {interface}", f"Всего пиров: {len(details['peers'])}", ""]
    for peer in details["peers"]:
        status = "online" if peer_is_online(peer) else "offline"
        rows.extend(
            [
                f"Peer {peer['public_key']}",
                f"  Адреса: {', '.join(peer['allowed_ips']) or '-'}",
                f"  Endpoint: {peer['endpoint'] or '-'}",
                f"  Handshake: {peer['latest_handshake'] or 'нет данных'}",
                f"  Трафик: RX {peer['receive_bytes']} B / TX {peer['transmit_bytes']} B",
                f"  Статус: {status}",
                "",
            ]
        )
    return text_result(
        "\n".join(rows) if details["peers"] else "Пиры не найдены",
        interface=interface,
        peers=details["peers"],
    )


def diagnostics(arguments: dict[str, Any]) -> dict[str, Any]:
    interface = validate_interface(arguments)
    checks: dict[str, Any] = {"interface": interface}
    try:
        checks["wg_installed"] = bool(run_command(["wg", "--version"]))
    except CommandError as exc:
        checks["wg_installed"] = False
        checks["wg_error"] = str(exc)

    try:
        checks["active"] = interface in set(active_interfaces())
    except CommandError as exc:
        checks["active"] = False
        checks["interfaces_error"] = str(exc)

    # Пробуем запустить, если конфиг есть, но интерфейс не активен
    config_exists = (config_directory() / f"{interface}.conf").is_file()
    if config_exists and not checks["active"]:
        try:
            run_command(["wg-quick", "up", interface], timeout=60)
            checks["active"] = True
        except CommandError:
            checks["active"] = False

    checks["link_present"] = has_link(interface)
    checks["config_present"] = config_exists
    checks["setup_required"] = not config_exists
    checks["healthy"] = bool(
        checks["wg_installed"] and checks["active"] and checks["link_present"]
    )
    lines = [
        f"WireGuard установлен: {'yes' if checks['wg_installed'] else 'no'}",
        f"{interface} активен: {'yes' if checks['active'] else 'no'}",
        f"Сетевой link существует: {'yes' if checks['link_present'] else 'no'}",
        f"Конфигурация найдена: {'yes' if checks['config_present'] else 'no'}",
        (
            "Требуется настройка сервера: используйте wg_setup_server "
            "с параметрами по умолчанию или укажите свои"
            if checks["setup_required"]
            else ""
        ),
        f"Итог: {'OK' if checks['healthy'] else 'ПРОБЛЕМА'}",
    ]
    return text_result("\n".join(lines), **checks)


def _allowed_ips(arguments: dict[str, Any]) -> list[str]:
    values = arguments.get("allowed_ips")
    if not isinstance(values, list) or not values:
        raise ValueError("allowed_ips должен быть непустым массивом CIDR")
    normalized = []
    for value in values:
        from ipaddress import ip_network

        try:
            network = ip_network(str(value), strict=False)
        except ValueError as exc:
            raise ValueError(f"Некорректный AllowedIP: {value}") from exc
        normalized.append(str(network))
    if len(normalized) != len(set(normalized)):
        raise ValueError("allowed_ips содержит дубликаты")
    return normalized


def add_peer(arguments: dict[str, Any]) -> dict[str, Any]:
    interface = validate_interface(arguments)
    public_key = validate_public_key(str(arguments.get("public_key", "")))
    allowed_ips = _allowed_ips(arguments)
    preshared_key = arguments.get("preshared_key")
    if preshared_key is not None:
        preshared_key = validate_public_key(str(preshared_key))
    if interface not in set(active_interfaces()):
        raise CommandError(f"Интерфейс {interface} не активен")
    command = [
        "wg",
        "set",
        interface,
        "peer",
        public_key,
        "allowed-ips",
        ",".join(allowed_ips),
    ]
    if preshared_key:
        command.extend(["preshared-key", "/dev/stdin"])
    run_command(command, input_text=preshared_key)
    try:
        save_peer(interface, public_key, allowed_ips, preshared_key)
    except Exception:
        run_command(["wg", "set", interface, "peer", public_key, "remove"])
        raise
    return text_result(
        f"Пир {public_key[:12]}... добавлен в {interface}",
        interface=interface,
        public_key=public_key,
        allowed_ips=allowed_ips,
    )


def remove_peer(arguments: dict[str, Any]) -> dict[str, Any]:
    interface = validate_interface(arguments)
    public_key = validate_public_key(str(arguments.get("public_key", "")))
    if interface not in set(active_interfaces()):
        raise CommandError(f"Интерфейс {interface} не активен")
    run_command(["wg", "set", interface, "peer", public_key, "remove"])
    # Пир может отсутствовать в файле конфигурации (был добавлен напрямую)
    # или файл может быть недоступен из контейнера — игнорируем ошибки
    try:
        delete_peer(interface, public_key)
    except Exception:
        pass
    try:
        removed_clients = remove_client_artifacts(public_key)
    except Exception:
        removed_clients = []
    return text_result(
        f"Пир {public_key[:12]}... удалён из {interface}",
        interface=interface,
        public_key=public_key,
        removed_client_files=removed_clients,
    )


def lifecycle_tool(operation, arguments: dict[str, Any]) -> dict[str, Any]:
    result = operation(arguments)
    return text_result(format_lifecycle(result), **result)


def create_client_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    result = create_client(arguments)
    return text_result(
        f"Клиент '{result['name']}' создан. "
        f"Конфигурация сохранена: {result['client_config']}. "
        f"QR-код сохранён: {result['qr_code']}. "
        "Приватный ключ не отображается и не передаётся в чат.",
        **result,
    )


def get_artifact_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    return get_artifact(arguments)


HANDLERS = {
    "wg_list_interfaces": list_interfaces,
    "wg_status": status,
    "wg_list_peers": list_peers,
    "wg_diagnostics": diagnostics,
    "wg_add_peer": add_peer,
    "wg_remove_peer": remove_peer,
    "wg_start": lambda args: lifecycle_tool(start, args),
    "wg_stop": lambda args: lifecycle_tool(stop, args),
    "wg_restart": lambda args: lifecycle_tool(restart, args),
    "wg_create_client": create_client_tool,
    "wg_install": install_wireguard,
    "wg_setup_server": setup_server,
    "wg_get_client_artifact": get_artifact_tool,
}

TOOLS_SCHEMA = [
    {
        "name": "wg_list_interfaces",
        "description": "Показать WireGuard-интерфейсы, их активность и наличие конфигурации",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "wg_install",
        "description": "Установить wireguard-tools и зависимости на хосте без создания конфигурации",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "wg_setup_server",
        "description": (
            "Создать серверную WireGuard-конфигурацию после подтверждения администратора. "
            "Параметры имеют безопасные значения по умолчанию: wg0, 10.66.66.0/24, "
            "fd42:42:42::/64, порт 51820, DNS 1.1.1.1 и 1.0.0.1. "
            "Для full-tunnel также включает forwarding, NAT и правило firewall."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "interface": {"type": "string", "default": "wg0"},
                "ipv4_network": {"type": "string", "default": "10.66.66.0/24"},
                "ipv6_network": {"type": "string", "default": "fd42:42:42::/64"},
                "listen_port": {"type": "integer", "default": 51820},
                "dns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": ["1.1.1.1", "1.0.0.1"],
                },
                "external_interface": {
                    "type": "string",
                    "description": "Необязательно: внешний интерфейс; иначе определяется по default route",
                },
            },
            "required": [],
        },
    },
    {
        "name": "wg_status",
        "description": "Показать реальный статус WireGuard-интерфейса и активность пиров",
        "inputSchema": {
            "type": "object",
            "properties": {"interface": {"type": "string", "default": "wg0"}},
            "required": [],
        },
    },
    {
        "name": "wg_list_peers",
        "description": "Показать реальных WireGuard-пиров, handshake и трафик",
        "inputSchema": {
            "type": "object",
            "properties": {"interface": {"type": "string", "default": "wg0"}},
            "required": [],
        },
    },
    {
        "name": "wg_diagnostics",
        "description": "Проверить установку WireGuard, интерфейс, link и конфигурацию",
        "inputSchema": {
            "type": "object",
            "properties": {"interface": {"type": "string", "default": "wg0"}},
            "required": [],
        },
    },
    {
        "name": "wg_add_peer",
        "description": "Добавить существующий WireGuard public key в интерфейс",
        "inputSchema": {
            "type": "object",
            "properties": {
                "interface": {"type": "string", "default": "wg0"},
                "public_key": {"type": "string"},
                "allowed_ips": {"type": "array", "items": {"type": "string"}},
                "preshared_key": {"type": "string"},
            },
            "required": ["public_key", "allowed_ips"],
        },
    },
    {
        "name": "wg_remove_peer",
        "description": "Удалить WireGuard peer по public key",
        "inputSchema": {
            "type": "object",
            "properties": {
                "interface": {"type": "string", "default": "wg0"},
                "public_key": {"type": "string"},
            },
            "required": ["public_key"],
        },
    },
]

for _name, _description in (
    ("wg_start", "Запустить WireGuard-интерфейс"),
    ("wg_stop", "Остановить WireGuard-интерфейс"),
    ("wg_restart", "Перезапустить WireGuard-интерфейс"),
):
    TOOLS_SCHEMA.append(
        {
            "name": _name,
            "description": _description,
            "inputSchema": {
                "type": "object",
                "properties": {"interface": {"type": "string", "default": "wg0"}},
                "required": [],
            },
        }
    )

TOOLS_SCHEMA.append(
    {
        "name": "wg_create_client",
        "description": (
            "Создать WireGuard-клиента: сгенерировать ключи, добавить peer, "
            "сохранить конфигурацию и QR-код на сервере без вывода private key"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "interface": {"type": "string", "default": "wg0"},
                "name": {"type": "string"},
                "addresses": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Необязательно: адреса клиента. Если не указаны, выбираются автоматически",
                },
                "endpoint": {
                    "type": "string",
                    "description": "Необязательно: публичный адрес сервера; по умолчанию LOOMWG_ENDPOINT",
                },
            },
            "required": ["name"],
        },
    }
)

TOOLS_SCHEMA.append(
    {
        "name": "wg_get_client_artifact",
        "description": "Внутренний одноразовый доступ к созданному client config или QR",
        "internal": True,
        "inputSchema": {
            "type": "object",
            "properties": {
                "token": {"type": "string"},
                "kind": {"type": "string", "enum": ["config", "qr"]},
            },
            "required": ["token", "kind"],
        },
    }
)
