"""Resolve the real client IP address behind a trusted reverse proxy.

When the app is fronted by nginx on loopback (the default deployment) every
request arrives with ``request.client.host == 127.0.0.1``. Keying rate limits
on that address collapses all clients into one bucket, so one attacker can
exhaust the login limit for everyone.

``client_ip`` returns the immediate peer unless that peer is a trusted proxy
(``TRUSTED_PROXIES``, loopback by default). Behind a trusted proxy it walks
``X-Forwarded-For`` from the *right*, skipping trusted hops, so a client-supplied
left-hand entry cannot be used for spoofing. ``X-Real-IP`` is the fallback.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from ipaddress import ip_address, ip_network
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


def _is_trusted(host: str) -> bool:
    try:
        address = ip_address(host)
    except ValueError:
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
            if not _is_trusted(hop):
                return hop
        real = headers.get("x-real-ip", "").strip()
        if real:
            return real
    return peer or "unknown"
