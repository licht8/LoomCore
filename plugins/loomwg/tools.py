"""MCP tool definitions and WireGuard tool handlers."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import Any

import clients_store
import service_mode
from client import (
    client_paths,
    create_client,
    export_client,
    get_artifact,
    remove_client_artifacts,
    store_client_record,
)
from config_view import show_config
from install import setup_server
from keys import validate_public_key
from lifecycle import format_lifecycle, restart, start, stop
from results import text_result
from stats import stats as stats_tool
from validate import check_config
from wireguard import (
    CommandError,
    active_interfaces,
    config_directory,
    configured_interfaces,
    has_link,
    peer_is_online,
    read_interface,
    run_command,
    validate_interface,
)

from config import iter_peers, upsert_peer
from config import remove_peer as delete_peer


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


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
    store = clients_store.load()

    active = interface in set(active_interfaces())
    live: dict[str, dict[str, Any]] = {}
    if active:
        live = {peer["public_key"]: peer for peer in read_interface(interface)["peers"]}

    tracked_public_keys = {record.get("public_key") for record in store.values()}
    clients: list[dict[str, Any]] = []
    rows: list[str] = [f"Interface: {interface}", f"Clients: {len(store)}", ""]
    for name in sorted(store):
        record = store[name]
        public_key = str(record.get("public_key") or "")
        peer = live.get(public_key)
        client_path, qr_path = client_paths(name)
        online = peer_is_online(peer) if peer else False
        entry = {
            "name": name,
            "public_key": public_key,
            "addresses": record.get("addresses", []),
            "installed": client_path.is_file(),
            "qr_present": qr_path.is_file(),
            "present": peer is not None,
            "online": online,
            "endpoint": peer.get("endpoint") if peer else None,
            "latest_handshake": peer.get("latest_handshake") if peer else None,
            "receive_bytes": peer.get("receive_bytes", 0) if peer else 0,
            "transmit_bytes": peer.get("transmit_bytes", 0) if peer else 0,
        }
        clients.append(entry)
        state = "online" if online else ("offline" if peer else "stale")
        rows.append(
            f"{name}: {state} | {public_key[:12]}... | "
            f"conf={'yes' if entry['installed'] else 'no'} "
            f"qr={'yes' if entry['qr_present'] else 'no'}"
        )

    unmanaged = [
        {
            "public_key": key,
            "addresses": peer.get("allowed_ips", []),
            "online": peer_is_online(peer),
            "endpoint": peer.get("endpoint"),
            "receive_bytes": peer.get("receive_bytes", 0),
            "transmit_bytes": peer.get("transmit_bytes", 0),
        }
        for key, peer in live.items()
        if key not in tracked_public_keys
    ]
    if unmanaged:
        rows.append(
            f"Unmanaged peers: {len(unmanaged)} — run wg_backfill_clients to import"
        )
    if not store and not unmanaged:
        rows.append("No clients")

    return text_result(
        "\n".join(rows),
        interface=interface,
        active=active,
        clients=clients,
        unmanaged=unmanaged,
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
    name = clients_store.validate_name(arguments.get("name"))
    public_key_arg = arguments.get("public_key")
    addresses_arg = arguments.get("addresses")
    preshared_key = arguments.get("preshared_key")
    if preshared_key is not None:
        preshared_key = validate_public_key(str(preshared_key))

    existing = clients_store.get(name)

    # No public key given -> provision a new client (generate keys + files).
    if not public_key_arg:
        if existing:
            raise ValueError(
                f"Client '{name}' already exists; use wg_export_client to reissue its config"
            )
        provision_args: dict[str, Any] = {"interface": interface, "name": name}
        if addresses_arg:
            provision_args["addresses"] = addresses_arg
        created = create_client(provision_args)
        store_client_record(name, created)
        safe = {
            key: value
            for key, value in created.items()
            if key not in {"private_key", "preshared_key"}
        }
        return text_result(
            f"Client '{name}' created. One-time config and QR links are ready "
            f"(valid for {created['artifact_expires_in']}s).",
            **safe,
            changed=True,
        )

    public_key = validate_public_key(str(public_key_arg))
    allowed_ips = _allowed_ips(arguments)
    if interface not in set(active_interfaces()):
        raise CommandError(f"Интерфейс {interface} не активен")

    existing_peer = None
    for peer in read_interface(interface)["peers"]:
        if peer["public_key"] == public_key:
            existing_peer = peer
            break

    # Idempotent: same peer with the same allowed IPs is a no-op.
    if existing_peer is not None and set(existing_peer["allowed_ips"]) == set(allowed_ips):
        clients_store.upsert(
            name,
            {
                "public_key": public_key,
                "addresses": allowed_ips,
                "created_at": (existing or {}).get("created_at") or _now(),
            },
        )
        return text_result(
            f"Client '{name}' already tracks peer {public_key[:12]}... with the same allowed IPs",
            name=name,
            public_key=public_key,
            addresses=allowed_ips,
            changed=False,
        )

    command = ["wg", "set", interface, "peer", public_key, "allowed-ips", ",".join(allowed_ips)]
    if preshared_key:
        command.extend(["preshared-key", "/dev/stdin"])
    run_command(command, input_text=preshared_key)
    try:
        changed = upsert_peer(interface, public_key, allowed_ips, preshared_key)
    except Exception:
        if existing_peer is None:
            run_command(["wg", "set", interface, "peer", public_key, "remove"])
        raise

    record: dict[str, Any] = {
        "public_key": public_key,
        "addresses": allowed_ips,
        "created_at": (existing or {}).get("created_at") or _now(),
    }
    if preshared_key:
        record["preshared_key"] = preshared_key
    clients_store.upsert(name, record)
    action = "updated" if existing is not None else "added"
    return text_result(
        f"Client '{name}' {action} (peer {public_key[:12]}...) in {interface}",
        name=name,
        public_key=public_key,
        allowed_ips=allowed_ips,
        changed=changed,
    )


def remove_peer(arguments: dict[str, Any]) -> dict[str, Any]:
    interface = validate_interface(arguments)
    name_arg = arguments.get("name")
    public_key_arg = arguments.get("public_key")
    name: str | None = None

    if public_key_arg:
        public_key = validate_public_key(str(public_key_arg))
    elif name_arg:
        name = clients_store.resolve_name(name_arg)
        record = clients_store.get(name)
        if not record:
            raise ValueError(f"Client '{name}' not found")
        public_key = str(record.get("public_key") or "")
    else:
        raise ValueError("Provide 'name' or 'public_key'")

    if interface not in set(active_interfaces()):
        raise CommandError(f"Интерфейс {interface} не активен")
    run_command(["wg", "set", interface, "peer", public_key, "remove"])
    with contextlib.suppress(FileNotFoundError, ValueError):
        delete_peer(interface, public_key)
    try:
        removed_clients = remove_client_artifacts(public_key)
    except Exception:
        removed_clients = []

    removed_names = clients_store.remove_by_public_key(public_key)
    if name and name not in removed_names:
        clients_store.remove(name)
        removed_names = [*removed_names, name]
    return text_result(
        f"Peer {public_key[:12]}... removed from {interface}",
        interface=interface,
        public_key=public_key,
        removed_client_files=removed_clients,
        removed_clients=removed_names,
    )


def lifecycle_tool(operation, arguments: dict[str, Any]) -> dict[str, Any]:
    result = operation(arguments)
    return text_result(format_lifecycle(result), **result)


def export_client_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    result = export_client(arguments)
    action = "recreated with new keys" if result.get("rotated") else "exported"
    return text_result(
        f"Client '{result['name']}' {action}. One-time config and QR links are "
        f"ready (valid for {result['artifact_expires_in']}s).",
        **result,
    )


def backfill_clients(arguments: dict[str, Any]) -> dict[str, Any]:
    """Import untracked peers from the server config into the JSON store."""
    interface = validate_interface(arguments)
    store = clients_store.load()
    tracked = {record.get("public_key") for record in store.values()}

    created: list[str] = []
    skipped: list[str] = []
    for peer in iter_peers(interface):
        public_key = peer["public_key"]
        if public_key in tracked:
            skipped.append(public_key[:12])
            continue
        name = f"peer-{public_key[:12]}"
        clients_store.upsert(
            name,
            {
                "public_key": public_key,
                "addresses": peer["allowed_ips"],
                "created_at": _now(),
            },
        )
        created.append(name)
        tracked.add(public_key)

    lines = [
        f"Backfill on {interface}: imported {len(created)}, skipped {len(skipped)}",
    ]
    lines.extend(f"  + {name}" for name in created)
    return text_result(
        "\n".join(lines),
        interface=interface,
        created=created,
        skipped=skipped,
    )


def get_artifact_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    return get_artifact(arguments)


HANDLERS = {
    "wg_list_interfaces": list_interfaces,
    "wg_status": status,
    "wg_list_peers": list_peers,
    "wg_diagnostics": diagnostics,
    "wg_show_config": show_config,
    "wg_check_config": check_config,
    "wg_stats": stats_tool,
    "wg_add_peer": add_peer,
    "wg_remove_peer": remove_peer,
    "wg_start": lambda args: lifecycle_tool(start, args),
    "wg_stop": lambda args: lifecycle_tool(stop, args),
    "wg_restart": lambda args: lifecycle_tool(restart, args),
    "wg_service_enable": service_mode.enable,
    "wg_service_disable": service_mode.disable,
    "wg_export_client": export_client_tool,
    "wg_backfill_clients": backfill_clients,
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
        "description": (
            "Create/track a client. Without public_key a keypair is generated and "
            "the client is saved to the store; with public_key an existing peer is "
            "tracked. Idempotent for the same key and allowed IPs."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "interface": {"type": "string", "default": "wg0"},
                "public_key": {"type": "string"},
                "addresses": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional client addresses; auto-allocated when omitted",
                },
                "allowed_ips": {"type": "array", "items": {"type": "string"}},
                "preshared_key": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "wg_remove_peer",
        "description": "Remove a client/peer by name (JSON store) or by public key.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "interface": {"type": "string", "default": "wg0"},
                "name": {"type": "string"},
                "public_key": {"type": "string"},
            },
            "required": [],
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

TOOLS_SCHEMA.extend(
    [
        {
            "name": "wg_show_config",
            "description": (
                "Show the interface configuration (live, or the saved file if the "
                "interface is down). Keys are masked unless show_secrets is true."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "interface": {"type": "string", "default": "wg0"},
                    "show_secrets": {"type": "boolean", "default": False},
                },
                "required": [],
            },
        },
        {
            "name": "wg_check_config",
            "description": (
                "Validate a WireGuard config without starting it: sections, keys, "
                "CIDRs, duplicate peers, and subnet overlaps."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "interface": {"type": "string", "default": "wg0"},
                    "path": {
                        "type": "string",
                        "description": "Optional: validate this config file instead of the interface file",
                    },
                    "content": {
                        "type": "string",
                        "description": "Optional: validate this config text instead of a file",
                    },
                },
                "required": [],
            },
        },
        {
            "name": "wg_stats",
            "description": (
                "Snapshot statistics: total traffic, per-peer share, and the top "
                "peers by traffic (no history)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "interface": {"type": "string", "default": "wg0"},
                    "top": {"type": "integer", "default": 5, "minimum": 1},
                },
                "required": [],
            },
        },
        {
            "name": "wg_export_client",
            "description": (
                "Regenerate the config/QR artifact links for an existing client. "
                "If the saved config is missing, pass rotate=true to recreate the "
                "client with new keys. Backfilled peer-<shortkey> clients have no "
                "stored private key and therefore require rotate=true."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "interface": {"type": "string", "default": "wg0"},
                    "kind": {
                        "type": "string",
                        "enum": ["config", "qr", "both"],
                        "default": "both",
                    },
                    "rotate": {"type": "boolean", "default": False},
                },
                "required": ["name"],
            },
        },
        {
            "name": "wg_service_enable",
            "description": (
                "Enable autostart: bring the interface up now and on plugin start "
                "(app-managed; no host systemd)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"interface": {"type": "string", "default": "wg0"}},
                "required": [],
            },
        },
        {
            "name": "wg_service_disable",
            "description": (
                "Disable autostart for the interface (does not stop it; use wg_stop)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"interface": {"type": "string", "default": "wg0"}},
                "required": [],
            },
        },
        {
            "name": "wg_backfill_clients",
            "description": (
                "Import untracked peers from the server config into the JSON store "
                "(names them peer-<shortkey>). Requires admin approval. Imported "
                "peers have no private key, so exporting them needs rotate=true."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"interface": {"type": "string", "default": "wg0"}},
                "required": [],
            },
        },
    ]
)
