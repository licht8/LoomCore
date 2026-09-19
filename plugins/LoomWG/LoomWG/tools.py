"""MCP tool definitions and WireGuard tool handlers."""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime
from functools import partial
from ipaddress import ip_network
from typing import Any

from .clients import store as clients_store
from .clients.client import (
    client_paths,
    create_client,
    export_existing_clients,
    get_artifact,
    remove_client_artifacts,
    store_client_record,
)
from .manifest import runtime_report
from .ops import service as service_mode
from .ops.dependencies import dependency_report, install_wireguard
from .ops.lifecycle import format_lifecycle, restart, start, stop
from .ops.provision import setup_server
from .ops.stats import stats as stats_tool
from .peers.config import iter_peers, upsert_peer
from .peers.config import remove_peer as delete_peer
from .peers.view import show_config
from .results import text_result
from .schemas import TOOLS_SCHEMA  # noqa: F401  (re-exported for the server and tests)
from .wg.device import (
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
from .wg.keys import validate_public_key
from .wg.validate import check_config

logger = logging.getLogger(__name__)


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
        "\n".join(rows) if names else "No WireGuard interfaces found",
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
        # Observation only: bringing the interface up is the caller's decision
        # (wg_interface_start / wg_setup_server), not a side effect of status.
        return text_result(
            f"{interface}: DOWN\nPeers: 0\nOnline: 0\nOffline: 0",
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
        f"Port: {details['listen_port']}\n"
        f"Peers: {peer_count}\n"
        f"Online: {online}\n"
        f"Offline: {peer_count - online}",
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
            f"Unmanaged peers: {len(unmanaged)} — run wg_export_existing_clients to import"
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

    # Observation only: diagnostics never brings the interface up itself; the
    # caller decides via wg_interface_start / wg_setup_server.
    config_exists = (config_directory() / f"{interface}.conf").is_file()

    checks["link_present"] = has_link(interface)
    checks["config_present"] = config_exists
    checks["setup_required"] = not config_exists

    prerequisites = dependency_report()
    checks["prerequisites"] = prerequisites["ready"]
    checks["missing_tools"] = prerequisites["missing"]
    prerequisites_line = (
        "Prerequisites: OK"
        if prerequisites["ready"]
        else "Prerequisites: missing "
        + ", ".join(prerequisites["missing"])
        + " — use wg_install_dependencies"
    )

    checks["healthy"] = bool(
        checks["wg_installed"]
        and checks["active"]
        and checks["link_present"]
        and prerequisites["ready"]
    )

    manifest_report = runtime_report()
    manifest = manifest_report["manifest"] or {}
    checks["manifest"] = manifest
    checks["manifest_issues"] = manifest_report["issues"]
    checks["permission_warnings"] = manifest_report["permission_warnings"]
    checks["permission_notes"] = manifest_report["permission_notes"]

    lines = [
        f"WireGuard installed: {'yes' if checks['wg_installed'] else 'no'}",
        prerequisites_line,
        f"{interface} active: {'yes' if checks['active'] else 'no'}",
        f"Network link present: {'yes' if checks['link_present'] else 'no'}",
        f"Configuration found: {'yes' if checks['config_present'] else 'no'}",
    ]
    if checks["setup_required"]:
        lines.append(
            "Server setup required: use wg_setup_server with defaults or provide your own"
        )
    if manifest:
        lines.append(
            f"Manifest: {manifest.get('name', '?')} {manifest.get('version', '')}".rstrip()
        )
    else:
        lines.append("Manifest: not found")
    lines.extend(f"Manifest issue: {issue}" for issue in manifest_report["issues"])
    lines.extend(
        f"Permission warning: {warning}"
        for warning in manifest_report["permission_warnings"]
    )
    lines.extend(
        f"Optional permission note: {note}"
        for note in manifest_report["permission_notes"]
    )
    lines.append(f"Result: {'OK' if checks['healthy'] else 'PROBLEM'}")
    return text_result("\n".join(lines), **checks)


def _allowed_ips(arguments: dict[str, Any]) -> list[str]:
    values = arguments.get("allowed_ips")
    if not isinstance(values, list) or not values:
        raise ValueError("allowed_ips must be a non-empty array of CIDRs")
    normalized = []
    for value in values:
        try:
            network = ip_network(str(value), strict=False)
        except ValueError as exc:
            raise ValueError(f"Invalid AllowedIP: {value}") from exc
        normalized.append(str(network))
    if len(normalized) != len(set(normalized)):
        raise ValueError("allowed_ips contains duplicates")
    return normalized


def _restore_live_peer(
    interface: str,
    public_key: str,
    existing_peer: dict[str, Any] | None,
    *,
    rotate_preshared_key: bool = False,
    previous_preshared_key: str | None = None,
) -> None:
    """Undo a live ``wg set`` when persisting the peer to the config failed.

    A peer that did not exist before is removed; a peer that did keeps its
    previous allowed IPs and, when this call rotated it, its preshared key, so
    the interface cannot silently diverge from the persisted config.
    """
    if existing_peer is None:
        run_command(["wg", "set", interface, "peer", public_key, "remove"])
        return
    command = [
        "wg",
        "set",
        interface,
        "peer",
        public_key,
        "allowed-ips",
        ",".join(existing_peer.get("allowed_ips") or []),
    ]
    input_text = None
    if rotate_preshared_key:
        # The persisted key is the source of truth; when it was absent an empty
        # value tells wg to drop the newly applied one.
        command.extend(["preshared-key", "/dev/stdin"])
        input_text = previous_preshared_key or ""
    run_command(command, input_text=input_text)


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
                f"Client '{name}' already exists; use wg_export_existing_clients to reissue its config"
            )
        provision_args: dict[str, Any] = {"interface": interface, "name": name}
        if addresses_arg:
            provision_args["addresses"] = addresses_arg
        if preshared_key is not None:
            provision_args["preshared_key"] = preshared_key
        created = create_client(provision_args)
        store_client_record(name, created)
        safe = {
            key: value
            for key, value in created.items()
            if key not in {"private_key", "preshared_key"}
        }
        return text_result(
            f"Client '{name}' created. Config and QR are ready to download.",
            **safe,
            changed=True,
        )

    public_key = validate_public_key(str(public_key_arg))
    allowed_ips = _allowed_ips(arguments)
    if interface not in set(active_interfaces()):
        raise CommandError(f"Interface {interface} is not active")

    existing_peer = None
    for peer in read_interface(interface)["peers"]:
        if peer["public_key"] == public_key:
            existing_peer = peer
            break

    # Idempotent: same peer with the same allowed IPs is a no-op, unless the
    # caller asked to rotate the preshared key.
    if (
        existing_peer is not None
        and preshared_key is None
        and set(existing_peer["allowed_ips"]) == set(allowed_ips)
    ):
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

    previous_preshared_key = None
    restore_preshared_key = False
    if preshared_key and existing_peer is not None:
        try:
            previous_preshared_key = next(
                (
                    peer["preshared_key"]
                    for peer in iter_peers(interface)
                    if peer.get("public_key") == public_key
                ),
                None,
            )
            restore_preshared_key = True
        except (OSError, ValueError):
            # The persisted key could not be read; leaving the newly applied
            # key in place is safer than clearing a key that may still be
            # present in the config.
            restore_preshared_key = False

    command = ["wg", "set", interface, "peer", public_key, "allowed-ips", ",".join(allowed_ips)]
    if preshared_key:
        command.extend(["preshared-key", "/dev/stdin"])
    run_command(command, input_text=preshared_key)
    try:
        changed = upsert_peer(interface, public_key, allowed_ips, preshared_key)
    except Exception:
        with contextlib.suppress(CommandError):
            _restore_live_peer(
                interface,
                public_key,
                existing_peer,
                rotate_preshared_key=restore_preshared_key,
                previous_preshared_key=previous_preshared_key,
            )
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
        raise CommandError(f"Interface {interface} is not active")
    run_command(["wg", "set", interface, "peer", public_key, "remove"])
    errors: list[str] = []
    try:
        delete_peer(interface, public_key)
    except FileNotFoundError:
        # The on-disk config is gone; there is nothing left to un-persist.
        pass
    except ValueError:
        # The peer is already absent from the config file; it is in sync.
        pass
    except OSError as exc:
        errors.append(f"could not update config: {exc}")
        logger.warning(
            "peer %s removed from %s live state but its config entry could not be "
            "removed: %s",
            public_key[:12],
            interface,
            exc,
        )
    try:
        removed_clients = remove_client_artifacts(public_key)
    except OSError as exc:
        removed_clients = []
        errors.append(f"could not remove client files: {exc}")
        logger.warning(
            "removed peer %s but could not remove its client files: %s",
            public_key[:12],
            exc,
        )

    removed_names = clients_store.remove_by_public_key(public_key)
    if name and name not in removed_names:
        clients_store.remove(name)
        removed_names = [*removed_names, name]
    message = f"Peer {public_key[:12]}... removed from {interface}"
    if errors:
        message += "\nWarning: " + "; ".join(errors)
    return text_result(
        message,
        interface=interface,
        public_key=public_key,
        removed_client_files=removed_clients,
        removed_clients=removed_names,
        errors=errors,
    )


def lifecycle_tool(operation, arguments: dict[str, Any]) -> dict[str, Any]:
    result = operation(arguments)
    return text_result(format_lifecycle(result), **result)


def export_existing_clients_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    result = export_existing_clients(arguments)
    clients = result["clients"]
    lines = [
        f"Exported {len(clients)} client(s) on {result['interface']}: "
        f"{len(result['rotated'])} rekeyed, {len(result['reissued'])} reissued, "
        f"{len(result['imported'])} imported, {len(result['failed'])} failed."
    ]
    lines.extend(f"  + imported {name}" for name in result["imported"])
    lines.extend(
        f"  ! {item['name']}: {item['error']}" for item in result["failed"]
    )
    return text_result("\n".join(lines), **result)


def get_artifact_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    artifact = get_artifact(arguments)
    return text_result(artifact["filename"], **artifact)


HANDLERS = {
    "wg_list_interfaces": list_interfaces,
    "wg_interface_status": status,
    "wg_list_peers": list_peers,
    "wg_diagnostics": diagnostics,
    "wg_install_dependencies": install_wireguard,
    "wg_show_interface_config": show_config,
    "wg_check_interface_config": check_config,
    "wg_stats": stats_tool,
    "wg_create_peer": add_peer,
    "wg_remove_peer": remove_peer,
    "wg_interface_start": partial(lifecycle_tool, start),
    "wg_interface_stop": partial(lifecycle_tool, stop),
    "wg_interface_restart": partial(lifecycle_tool, restart),
    "wg_service_enable": service_mode.enable,
    "wg_service_disable": service_mode.disable,
    "wg_export_existing_clients": export_existing_clients_tool,
    "wg_setup_server": setup_server,
    "wg_get_client_artifact": get_artifact_tool,
}
