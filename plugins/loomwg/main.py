#!/usr/bin/env python3
"""LoomWG Plugin — MCP Server over stdio.

Инструменты:
  - wg_status: показать статус WireGuard
  - wg_list_peers: список пиров
  - add_peer: добавить пир

Работает в Mock Mode (тестовые данные), Python stdlib only.
"""

import json
import sys

# ─── Тестовые данные (Mock Mode) ────────────────────────────────────────────

MOCK_PEERS = [
    {
        "name": "hoge",
        "public_key": "dG9rZW4tcHVibGljLWtleS0x",
        "allowed_ips": ["10.0.0.2/32"],
        "endpoint": "192.168.1.100:51820",
        "latest_handshake": "2026-02-14T10:30:00Z",
        "rx_bytes": 1024000,
        "tx_bytes": 512000,
    },
    {
        "name": "office",
        "public_key": "dG9rZW4tcHVibGljLWtleS0y",
        "allowed_ips": ["10.0.0.3/32"],
        "endpoint": "192.168.1.101:51820",
        "latest_handshake": None,
        "rx_bytes": 0,
        "tx_bytes": 0,
    },
]

PEERS_DB = {p["name"]: dict(p) for p in MOCK_PEERS}


# ─── Инструменты (Tools) ────────────────────────────────────────────────────

def handle_wg_status(arguments: dict) -> dict:
    """Показать статус WireGuard."""
    peer_count = len(PEERS_DB)
    active = sum(1 for p in PEERS_DB.values() if p["latest_handshake"])
    return {
        "content": [
            {
                "type": "text",
                "text": (
                    f"WG0: UP\n"
                    f"Пиров: {peer_count}\n"
                    f"Активных: {active}\n"
                    f"Неактивных: {peer_count - active}"
                ),
            }
        ],
    }


def handle_wg_list_peers(arguments: dict) -> dict:
    """Список пиров с IP, статусом, последним обменом."""
    lines = ["Name | Public Key | Allowed IPs | Endpoint | Handshake | RX | TX"]
    lines.append("-" * 90)
    for name, peer in PEERS_DB.items():
        hs = peer["latest_handshake"] or "never"
        lines.append(
            f"{name:8} | {peer['public_key'][:20]:20} | "
            f"{peer['allowed_ips'][0]:14} | "
            f"{peer['endpoint']:18} | {hs:20} | "
            f"{peer['rx_bytes']:>8} | {peer['tx_bytes']:>8}"
        )
    return {
        "content": [
            {"type": "text", "text": "\n".join(lines)},
        ],
    }


def handle_add_peer(arguments: dict) -> dict:
    """Добавить пир (name, public_key, allowed_ips)."""
    name = arguments.get("name", "")
    public_key = arguments.get("public_key", "")
    allowed_ips = arguments.get("allowed_ips", [])

    if not name or not public_key:
        return {
            "content": [{"type": "text", "text": "Ошибка: name и public_key обязательны"}],
        }

    PEERS_DB[name] = {
        "name": name,
        "public_key": public_key,
        "allowed_ips": allowed_ips if allowed_ips else ["10.0.0.99/32"],
        "endpoint": None,
        "latest_handshake": None,
        "rx_bytes": 0,
        "tx_bytes": 0,
    }

    return {
        "content": [
            {
                "type": "text",
                "text": f"Пир '{name}' успешно добавлен. Allowed IPs: {', '.join(allowed_ips)}",
            }
        ],
    }


HANDLERS = {
    "wg_status": handle_wg_status,
    "wg_list_peers": handle_wg_list_peers,
    "add_peer": handle_add_peer,
}

TOOLS_SCHEMA = [
    {
        "name": "wg_status",
        "description": "Показать общий статус WireGuard (количество пиров, активные/неактивные)",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "wg_list_peers",
        "description": "Список всех пиров с IP, публичными ключами, статусом и обменом данными",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "add_peer",
        "description": "Добавить новый WireGuard пир (операция записи)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Имя пира",
                },
                "public_key": {
                    "type": "string",
                    "description": "Публичный ключ пира (base64)",
                },
                "allowed_ips": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Список разрешённых IP (CIDR)",
                },
            },
            "required": ["name", "public_key", "allowed_ips"],
        },
    },
]


# ─── MCP over stdio (JSON-RPC 2.0) ─────────────────────────────────────────

def _send(obj: dict):
    """Отправить JSON-объект в stdout (MCP-контракт)."""
    line = json.dumps(obj, ensure_ascii=False)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _read_request():
    """Прочитать один JSON-запрос с stdin."""
    line = sys.stdin.readline()
    if not line:
        return None
    return json.loads(line.strip())


def handle_initialize(request_id: int) -> dict:
    """Ответ на инициализацию."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {"listChanged": False},
            },
            "serverInfo": {
                "name": "loomwg",
                "version": "0.0.1",
            },
        },
    }


def handle_tools_list(request_id: int) -> dict:
    """Возвращает список инструментов."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "tools": TOOLS_SCHEMA,
        },
    }


def handle_tools_call(request_id: int, params: dict) -> dict:
    """Вызов инструмента плагина."""
    tool_name = params.get("name", "")
    tool_args = params.get("arguments", {})

    handler = HANDLERS.get(tool_name)
    if not handler:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32601,
                "message": f"Unknown tool: {tool_name}",
            },
        }

    try:
        result = handler(tool_args)
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": result,
        }
    except Exception as exc:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32000,
                "message": str(exc),
            },
        }


def main():
    """Основной цикл MCP-сервера."""
    _send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    while True:
        request = _read_request()
        if request is None:
            break

        method = request.get("method", "")
        request_id = request.get("id", 0)
        params = request.get("params", {})

        if method == "initialize":
            response = handle_initialize(request_id)
        elif method == "tools/list":
            response = handle_tools_list(request_id)
        elif method == "tools/call":
            response = handle_tools_call(request_id, params)
        else:
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": f"Unknown method: {method}",
                },
            }

        _send(response)


if __name__ == "__main__":
    main()
