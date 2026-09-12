"""Snapshot WireGuard statistics (current totals, no history)."""

from __future__ import annotations

from typing import Any

from results import text_result
from wireguard import has_link, peer_is_online, read_interface, validate_interface


def _share(peer_total: int, grand_total: int) -> float:
    return round(peer_total / grand_total * 100, 1) if grand_total else 0.0


def stats(arguments: dict[str, Any]) -> dict[str, Any]:
    interface = validate_interface(arguments)
    top_n = max(1, int(arguments.get("top", 5) or 5))

    details = read_interface(interface)
    peers = details["peers"]
    total_rx = sum(peer["receive_bytes"] for peer in peers)
    total_tx = sum(peer["transmit_bytes"] for peer in peers)
    grand_total = total_rx + total_tx
    online = 0

    for peer in peers:
        peer["online"] = peer_is_online(peer)
        peer["total_bytes"] = peer["receive_bytes"] + peer["transmit_bytes"]
        peer["share"] = _share(peer["total_bytes"], grand_total)
        online += int(peer["online"])

    top = sorted(peers, key=lambda peer: peer["total_bytes"], reverse=True)[:top_n]

    lines = [
        f"Interface: {interface}",
        f"Link: {'up' if has_link(interface) else 'down'}",
        f"Peers: {len(peers)} ({online} online / {len(peers) - online} offline)",
        f"Total: RX {total_rx} B / TX {total_tx} B",
        "",
        f"Top {len(top)} peers by traffic:",
    ]
    for peer in top:
        lines.append(
            f"  {peer['public_key'][:12]}...  "
            f"RX {peer['receive_bytes']} B / TX {peer['transmit_bytes']} B  "
            f"({peer['share']}%)  {'online' if peer['online'] else 'offline'}"
        )

    return text_result(
        "\n".join(lines),
        interface=interface,
        peer_count=len(peers),
        online_peers=online,
        total_receive_bytes=total_rx,
        total_transmit_bytes=total_tx,
        peers=top,
    )
