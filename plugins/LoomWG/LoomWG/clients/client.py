"""Secure client configuration generation for WireGuard."""

from __future__ import annotations

import base64
import contextlib
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime
from ipaddress import ip_address, ip_interface, ip_network
from pathlib import Path

from ..peers.config import add_peer, config_path, iter_peers, remove_peer, upsert_peer
from ..util.files import atomic_write_text
from ..wg.config_parser import parse_sections
from ..wg.device import CommandError, run_command
from ..wg.keys import generate_keypair, validate_public_key
from ..wg.validate import validate_config_text
from . import store as clients_store
from .paths import (
    CONF_DIR_NAME,
    QR_DIR_NAME,
    client_paths,
    clients_directory,
    migrate_client_layout,
)
from .qr import generate_qr as _generate_qr
from .render import build_client_config
from .render import format_endpoint as _format_endpoint  # noqa: F401


def rebuild_client(name: str, record: dict, interface: str) -> dict:
    """Rebuild <name>.conf and <name>.png from secrets stored in the JSON store."""
    private_key = str(record.get("private_key") or "")
    if not private_key:
        raise ValueError(
            f"Private key for '{name}' is not stored; cannot rebuild the config"
        )
    addresses = [str(item) for item in (record.get("addresses") or [])]
    if not addresses:
        raise ValueError(f"No addresses stored for '{name}'")

    server_public_key, listen_port, _, _ = _server_settings(interface)
    endpoint = str(record.get("endpoint") or os.environ.get("LOOMWG_ENDPOINT", "")).strip()
    if not endpoint:
        endpoint = _detect_endpoint()

    client_config = build_client_config(
        server_public_key,
        listen_port,
        addresses,
        private_key,
        str(record.get("preshared_key") or ""),
        endpoint,
    )
    client_path, qr_path = client_paths(name)
    _write_secret(client_path, client_config)
    _generate_qr(client_config, qr_path)
    return {
        "name": name,
        "interface": interface,
        "public_key": str(record.get("public_key") or ""),
        "client_config": str(client_path),
        "qr_code": str(qr_path),
    }


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
        "Could not detect the server's public address. "
        "Set endpoint or configure LOOMWG_ENDPOINT."
    )


def _server_settings(interface: str) -> tuple[str, int, list[str], list[str]]:
    path = config_path(interface)
    if not path.is_file():
        raise FileNotFoundError(f"Configuration {path} not found")
    private_key = ""
    listen_port = 51820
    server_networks: list[str] = []
    server_addresses: list[str] = []
    for section in parse_sections(path.read_text(encoding="utf-8")):
        for key, value in section.entries:
            if value is None:
                continue
            if key == "PrivateKey":
                private_key = value
            elif key == "ListenPort":
                listen_port = int(value)
            elif key == "Address":
                for raw_address in value.split(","):
                    address = ip_interface(raw_address.strip())
                    server_networks.append(str(address.network))
                    server_addresses.append(str(address.ip))
    if not private_key:
        raise ValueError("Server configuration is missing PrivateKey")
    public_key = run_command(["wg", "pubkey"], input_text=private_key)
    return public_key, listen_port, server_networks, server_addresses


def _used_addresses(interface: str) -> set:
    path = config_path(interface)
    used = set()
    for section in parse_sections(path.read_text(encoding="utf-8")):
        for key, value in section.entries:
            if value is None or key != "AllowedIPs":
                continue
            for raw_network in value.split(","):
                network = ip_network(raw_network.strip(), strict=False)
                if network.prefixlen in (32, 128):
                    used.add(network.network_address)
    try:
        output = run_command(["wg", "show", interface, "dump"], sensitive=True)
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


def _ipv6_at_offset(ipv6_networks, host_offset: int, used: set) -> str | None:
    """Return the free IPv6 address at ``host_offset`` in the first fitting network."""
    for ipv6_network in ipv6_networks:
        if (
            int(ipv6_network.network_address) + host_offset
            > int(ipv6_network.broadcast_address)
        ):
            # The IPv4-derived offset would overflow this network. Deriving the
            # address from the network base keeps it an IPv6Address, whereas
            # ip_address(int) coerces small values (e.g. ::/64) to IPv4.
            continue
        ipv6 = ipv6_network.network_address + host_offset
        if ipv6 not in used:
            return str(ipv6)
    return None


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
    # Largest offset any IPv6 network can hold: once a free IPv4 host sits past
    # it, every later host is past it too, so the scan can stop.
    ipv6_capacity = max(
        (
            int(network.broadcast_address) - int(network.network_address)
            for network in ipv6_networks
        ),
        default=0,
    )

    for ipv4_network in ipv4_networks:
        for ipv4 in ipv4_network.hosts():
            if ipv4 in used:
                continue
            if not ipv6_networks:
                return [f"{ipv4}/32"]
            host_offset = int(ipv4) - int(ipv4_network.network_address)
            if host_offset > ipv6_capacity:
                break
            ipv6 = _ipv6_at_offset(ipv6_networks, host_offset, used)
            if ipv6 is not None:
                return [f"{ipv4}/32", f"{ipv6}/128"]
    raise ValueError("No free IPv4/IPv6 pair available in the WireGuard networks")


def _client_addresses(arguments: dict, interface: str, server_networks: list[str], server_addresses: list[str]) -> list[str]:
    raw = arguments.get("addresses")
    if raw is None:
        raw = arguments.get("allowed_ips")
    if raw is None:
        return _allocate_addresses(interface, server_networks, server_addresses)
    if not isinstance(raw, list) or not raw:
        raise ValueError("addresses must be a non-empty array")
    result = []
    for value in raw:
        try:
            address = ip_interface(str(value))
        except ValueError as exc:
            raise ValueError(f"Invalid client address: {value}") from exc
        if address.network.prefixlen == 0:
            raise ValueError("Client address cannot be the default route")
        if address.network.prefixlen not in (32, 128):
            raise ValueError("Client address must be /32 or /128")
        result.append(str(address))
    if len(result) != len(set(result)):
        raise ValueError("addresses contains duplicates")
    return result


def _write_secret(path: Path, content: str) -> None:
    text = content.rstrip() + "\n"
    validate_config_text(text)
    atomic_write_text(path, text, mode=0o600)


def remove_client_artifacts(public_key: str) -> list[str]:
    """Remove generated client files whose key matches a server peer."""
    removed: list[str] = []
    conf_dir = clients_directory() / CONF_DIR_NAME
    qr_dir = clients_directory() / QR_DIR_NAME
    if not conf_dir.is_dir():
        return removed

    for client_path in conf_dir.glob("*.conf"):
        private_key = ""
        for line in client_path.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() == "PrivateKey":
                private_key = value.strip()
                break
        if not private_key:
            continue
        try:
            client_public_key = run_command(["wg", "pubkey"], timeout=5, input_text=private_key)
        except CommandError:
            # A stale or malformed PrivateKey must not abort the sweep and leave
            # the remaining artifacts orphaned.
            continue
        if client_public_key != public_key:
            continue

        name = client_path.stem
        client_path.unlink()
        qr_path = qr_dir / f"{name}.png"
        if qr_path.exists():
            qr_path.unlink()
        removed.append(name)
    return removed


def create_client(arguments: dict) -> dict:
    interface = str(arguments.get("interface", "wg0"))
    name = clients_store.validate_name(arguments.get("name"))
    endpoint = str(arguments.get("endpoint") or os.environ.get("LOOMWG_ENDPOINT", "")).strip()
    if not endpoint:
        endpoint = _detect_endpoint()
    if any(char.isspace() for char in endpoint):
        raise ValueError("endpoint must not contain whitespace")
    server_public_key, listen_port, server_networks, server_addresses = _server_settings(interface)
    addresses = _client_addresses(arguments, interface, server_networks, server_addresses)
    used_addresses = _used_addresses(interface)
    for address in addresses:
        if not any(ip_interface(address).ip in ip_network(network) for network in server_networks):
            raise ValueError(f"Client address {address} is outside the WireGuard network")
        if ip_interface(address).ip in used_addresses or ip_interface(address).ip in {
            ip_address(value) for value in server_addresses
        }:
            raise ValueError(f"Client address {address} is already in use")
    keypair = generate_keypair()
    preshared_key = arguments.get("preshared_key")
    if preshared_key is not None:
        preshared_key = validate_public_key(str(preshared_key))
    else:
        preshared_key = run_command(["wg", "genpsk"], sensitive=True)
    client_path, qr_path = client_paths(name)
    if client_path.exists() or qr_path.exists():
        raise ValueError(f"Client '{name}' already exists")

    client_config = build_client_config(
        server_public_key,
        listen_port,
        addresses,
        keypair["private_key"],
        preshared_key,
        endpoint,
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
        _generate_qr(client_config, qr_path)
    except BaseException:
        with contextlib.suppress(CommandError):
            run_command(["wg", "set", interface, "peer", keypair["public_key"], "remove"])
        for path in (client_path, qr_path):
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
        with contextlib.suppress(FileNotFoundError, ValueError):
            remove_peer(interface, keypair["public_key"])
        raise

    return {
        "name": name,
        "interface": interface,
        "public_key": keypair["public_key"],
        "private_key": keypair["private_key"],
        "preshared_key": preshared_key,
        "addresses": addresses,
        "endpoint": endpoint,
        "client_config": str(client_path),
        "qr_code": str(qr_path),
        "secret_persisted": True,
    }


def get_artifact(arguments: dict) -> dict:
    """Return a stored client's config or QR as base64.

    Resolves the client by name and rebuilds the artifact from the stored
    private key when the file is missing, so links survive plugin restarts.
    """
    interface = str(arguments.get("interface", "wg0"))
    name = clients_store.resolve_name(arguments.get("name"))
    kind = str(arguments.get("kind", ""))
    if kind not in {"config", "qr"}:
        raise ValueError("kind must be one of: config, qr")

    record = clients_store.get(name)
    if not record:
        raise ValueError(f"Client '{name}' not found")

    client_path, qr_path = client_paths(name)
    if not client_path.is_file():
        if not record.get("private_key"):
            raise ValueError(
                f"Config for '{name}' is not available; run "
                "wg_export_existing_clients with rotate=true to recreate it"
            )
        rebuild_client(name, record, interface)
    elif kind == "qr" and not qr_path.is_file():
        _generate_qr(client_path.read_text(encoding="utf-8"), qr_path)

    path = client_path if kind == "config" else qr_path
    if not path.is_file():
        raise FileNotFoundError(f"{kind} artifact for '{name}' not found")
    raw = path.read_bytes()
    return {
        "filename": path.name,
        "media_type": "text/plain" if kind == "config" else "image/png",
        "content_base64": base64.b64encode(raw).decode("ascii"),
    }


def store_client_record(name: str, created: dict, created_at: str | None = None) -> None:
    """Persist a provisioned client's metadata + secrets in the JSON store."""
    clients_store.upsert(
        name,
        {
            "public_key": created["public_key"],
            "private_key": created["private_key"],
            "preshared_key": created.get("preshared_key", ""),
            "addresses": created["addresses"],
            "endpoint": created["endpoint"],
            "created_at": created_at or datetime.now(tz=UTC).isoformat(),
        },
    )


def _remove_created_client(interface: str, name: str, created: dict) -> None:
    """Best-effort removal of a client created by a rotation that then failed."""
    public_key = str(created.get("public_key") or "")
    if public_key:
        with contextlib.suppress(CommandError):
            run_command(["wg", "set", interface, "peer", public_key, "remove"])
        with contextlib.suppress(OSError, ValueError):
            remove_peer(interface, public_key)
    with contextlib.suppress(OSError):
        _drop_client_files(name)


def _restore_rotated_client(
    interface: str, name: str, record: dict, original_peer: dict | None = None
) -> None:
    """Best-effort rollback of a failed rotation.

    Rotation removes the old peer and store record before ``create_client``
    runs, so a failure there would otherwise lose the client with no key
    material. Put the config peer block, the live peer and the record back.
    The record of an imported (backfilled) peer has no private key and may lack
    the PresharedKey, so the peer block parsed from the config is preferred: it
    is the only place the original PSK survives the removal.
    """
    public_key = str(record.get("public_key") or "")
    addresses = [
        str(item)
        for item in (
            (original_peer or {}).get("allowed_ips")
            or record.get("addresses")
            or []
        )
    ]
    preshared_key = str(
        (original_peer or {}).get("preshared_key")
        or record.get("preshared_key")
        or ""
    )
    if public_key and addresses:
        with contextlib.suppress(CommandError, FileNotFoundError, ValueError, OSError):
            upsert_peer(interface, public_key, addresses, preshared_key or None)
        with contextlib.suppress(CommandError):
            command = [
                "wg",
                "set",
                interface,
                "peer",
                public_key,
                "allowed-ips",
                ",".join(addresses),
            ]
            if preshared_key:
                command += ["preshared-key", "/dev/stdin"]
                run_command(command, input_text=preshared_key)
            else:
                run_command(command)
    with contextlib.suppress(OSError, ValueError):
        clients_store.upsert(name, record)


def export_client(arguments: dict) -> dict:
    """Ensure the client config/QR exist and report their paths.

    Uses the private key from the JSON store to rebuild a missing config; with
    ``rotate=true`` a peer without a stored private key is recreated with new keys.
    """
    interface = str(arguments.get("interface", "wg0"))
    name = clients_store.resolve_name(arguments.get("name"))
    kind = str(arguments.get("kind", "both")).lower()
    if kind not in {"config", "qr", "both"}:
        raise ValueError("kind must be one of: config, qr, both")
    rotate = bool(arguments.get("rotate", False))
    endpoint = str(
        arguments.get("endpoint") or os.environ.get("LOOMWG_ENDPOINT", "")
    ).strip()

    record = clients_store.get(name)
    if not record:
        raise ValueError(f"Client '{name}' not found in the store")

    client_path, qr_path = client_paths(name)
    rotated = False

    if not client_path.is_file():
        if record.get("private_key"):
            result = rebuild_client(name, record, interface)
        elif rotate:
            old_key = str(record.get("public_key") or "")
            # Drop stale artifacts first: create_client refuses a name while a
            # leftover <name>.png exists, which would abort after the peer and
            # record below are already gone.
            _drop_client_files(name)
            original_peer: dict | None = None
            if old_key:
                with contextlib.suppress(OSError, ValueError):
                    original_peer = next(
                        (
                            peer
                            for peer in iter_peers(interface)
                            if peer.get("public_key") == old_key
                        ),
                        None,
                    )
                with contextlib.suppress(CommandError):
                    run_command(["wg", "set", interface, "peer", old_key, "remove"])
                with contextlib.suppress(FileNotFoundError, ValueError):
                    remove_peer(interface, old_key)
                clients_store.remove(name)
            recreate: dict = {
                "interface": interface,
                "name": name,
                **(
                    {"addresses": record["addresses"]}
                    if record.get("addresses")
                    else {}
                ),
            }
            if endpoint:
                recreate["endpoint"] = endpoint
            created: dict | None = None
            try:
                created = create_client(recreate)
                store_client_record(name, created, record.get("created_at"))
            except BaseException:
                if created is not None:
                    _remove_created_client(interface, name, created)
                _restore_rotated_client(interface, name, record, original_peer)
                raise
            result = {
                key: value
                for key, value in created.items()
                if key not in {"private_key", "preshared_key"}
            }
            rotated = True
        else:
            raise ValueError(
                f"Config for '{name}' is missing and no private key is stored "
                "(backfilled/external peer); retry with rotate=true to recreate "
                "it with new keys"
            )
    else:
        client_config = client_path.read_text(encoding="utf-8")
        if kind == "qr" or not qr_path.is_file():
            _generate_qr(client_config, qr_path)
        result = {
            "name": name,
            "interface": interface,
            "public_key": str(record.get("public_key") or ""),
            "client_config": str(client_path),
            "qr_code": str(qr_path),
        }

    result["rotated"] = rotated
    return result


def _drop_client_files(name: str) -> None:
    """Delete generated config/QR files for a client name."""
    for path in client_paths(name):
        with contextlib.suppress(FileNotFoundError):
            path.unlink()


def export_existing_clients(arguments: dict) -> dict:
    """Import untracked peers and (re)issue config/QR artifacts for every peer.

    Backfilled peers have no private key, so ``rotate`` defaults to true and
    recreates them with a fresh keypair. Clients with a stored private key are
    only reissued. Returns a per-client list reporting artifact availability.
    """
    interface = str(arguments.get("interface", "wg0"))
    kind = str(arguments.get("kind", "both")).lower()
    if kind not in {"config", "qr", "both"}:
        raise ValueError("kind must be one of: config, qr, both")
    rotate = bool(arguments.get("rotate", True))
    endpoint = str(
        arguments.get("endpoint") or os.environ.get("LOOMWG_ENDPOINT", "")
    ).strip()
    # Detect the endpoint once. Failing per client would re-run detection (with
    # its own timeouts) for every client that needs a rebuild.
    endpoint_error: str | None = None
    if rotate and not endpoint:
        try:
            endpoint = _detect_endpoint()
        except ValueError as exc:
            endpoint_error = str(exc)

    migrate_client_layout()
    for old_name in clients_store.migrate_legacy_names():
        _drop_client_files(old_name)

    imported: list[str] = []
    targets: list[dict] = []
    for peer in iter_peers(interface):
        public_key = str(peer.get("public_key") or "")
        if not public_key:
            continue
        name = clients_store.find_by_public_key(public_key)
        if not name:
            name = clients_store.canonical_name(public_key)
            clients_store.upsert(
                name,
                {
                    "public_key": public_key,
                    "addresses": peer.get("allowed_ips") or [],
                    "created_at": datetime.now(tz=UTC).isoformat(),
                },
            )
            imported.append(name)
        targets.append({"name": name, "public_key": public_key})

    selected: set[str] | None = None
    if arguments.get("names") is not None:
        raw_names = arguments.get("names")
        if not isinstance(raw_names, list) or not raw_names:
            raise ValueError("names must be a non-empty array")
        selected = {clients_store.resolve_name(item) for item in raw_names}

    rotated: list[str] = []
    reissued: list[str] = []
    failed: list[dict] = []
    clients: list[dict] = []

    for entry in targets:
        name = entry["name"]
        if selected is not None and name not in selected:
            continue
        record = clients_store.get(name) or {}
        needs_key = (
            not client_paths(name)[0].is_file() and not record.get("private_key")
        )
        if needs_key and not rotate:
            failed.append(
                {**entry, "error": "no stored private key; retry with rotate=true"}
            )
            continue
        if endpoint_error and needs_key:
            # Only a client with no stored key needs a fresh create_client(),
            # which is what re-runs endpoint detection; a config-less client
            # that still has a private_key is rebuilt from the stored record
            # (endpoint included) and does not require detection.
            failed.append({**entry, "error": endpoint_error})
            continue
        try:
            exported = export_client(
                {
                    "interface": interface,
                    "name": name,
                    "kind": kind,
                    "rotate": rotate,
                    "endpoint": endpoint,
                }
            )
        except (ValueError, FileNotFoundError, CommandError, OSError) as exc:
            failed.append({**entry, "error": str(exc)})
            continue

        if exported.get("rotated"):
            rotated.append(name)
        else:
            reissued.append(name)
        client_path, qr_path = client_paths(name)
        clients.append(
            {
                **entry,
                "rotated": bool(exported.get("rotated")),
                "config_ready": client_path.is_file(),
                "qr_ready": qr_path.is_file(),
            }
        )

    return {
        "interface": interface,
        "clients": clients,
        "imported": imported,
        "rotated": rotated,
        "reissued": reissued,
        "failed": failed,
    }
