"""Resolve the real client IP address behind a trusted reverse proxy.

When the app is fronted by nginx on loopback (the default deployment) every
request arrives with ``request.client.host == 127.0.0.1``. Keying rate limits
on that address collapses all clients into one bucket, so one attacker can
exhaust the login limit for everyone.

``client_ip`` returns the immediate peer unless that peer is a trusted proxy
(``TRUSTED_PROXIES``, loopback by default). Behind a trusted proxy it walks
``X-Forwarded-For`` from the *right*, skipping trusted hops, so a client-supplied
left-hand entry cannot be used for spoofing. ``X-Real-IP`` is the fallback.

Header entries that are not valid IPs are ignored, and IPv4-mapped IPv6
addresses (``::ffff:127.0.0.1``) are normalized before the trust check.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network
from typing import Any

from src.config import settings

logger = logging.getLogger("loomcore.security")


@lru_cache(maxsize=1)
def _trusted_networks() -> tuple:
    """Parse ``TRUSTED_PROXIES`` once into a tuple of networks."""
    networks = []
    for item in (settings.trusted_proxies or "").replace(",", " ").split():
        try:
            networks.append(ip_network(item, strict=False))
        except ValueError:
            logger.warning("Ignoring invalid TRUSTED_PROXIES entry %r", item)
    return tuple(networks)


def _parse_address(host: str) -> IPv4Address | IPv6Address | None:
    """Parse ``host`` into an address, collapsing IPv4-mapped IPv6 forms.

    A dual-stack proxy can surface ``127.0.0.1`` as ``::ffff:127.0.0.1``;
    without this normalization an IPv4-mapped address is never ``in`` an IPv4
    network and a trusted proxy would be mistaken for the client.
    """
    try:
        address = ip_address(host)
    except ValueError:
        return None
    mapped = getattr(address, "ipv4_mapped", None)
    return mapped if mapped is not None else address


def _is_trusted(host: str) -> bool:
    address = _parse_address(host)
    if address is None:
        return False
    return any(address in network for network in _trusted_networks())


def client_ip(request: Any) -> str:
    """Return the client IP for ``request`` (an HTTP Request or WebSocket)."""
    peer_obj = getattr(request, "client", None)
    peer = peer_obj.host if peer_obj else ""
    headers = getattr(request, "headers", None)
    if peer and headers and _is_trusted(peer):
        forwarded = headers.get("x-forwarded-for", "")
        for hop in reversed([item.strip() for item in forwarded.split(",") if item.strip()]):
            if _parse_address(hop) is None:
                logger.warning("Ignoring non-IP X-Forwarded-For entry %r", hop)
                continue
            if not _is_trusted(hop):
                return hop
        real = headers.get("x-real-ip", "").strip()
        if real and not _is_trusted(real):
            if _parse_address(real) is None:
                logger.warning("Ignoring non-IP X-Real-IP value %r", real)
            else:
                return real
    return peer or "unknown"
