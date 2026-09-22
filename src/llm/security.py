"""Best-effort SSRF guards for LLM provider URLs.

The core lets an admin point the LLM client at any OpenAI-compatible gateway.
To keep that from becoming a server-side request forgery primitive, provider
URLs are checked against literal and well-known internal targets and, when
asked, against every address the host resolves to.

This is deliberately best-effort: it blocks literal/known-internal hosts and,
when asked, resolved non-public ranges, but it cannot defeat DNS rebinding
between the check and the actual connection. The resolving form is used when a
provider is saved or tested; the per-request guard is literal-only (no DNS).

Reserved blocks that ``ipaddress.is_global`` mislabels as global (for example
``128.0.0.0/16`` and ``192.0.0.0/24``) are rejected via an explicit denylist.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

# ``metadata.google.internal`` needs no entry: the ``.internal`` suffix check
# below already rejects it.
_METADATA_HOSTS = frozenset({"metadata.goog"})
_INTERNAL_SUFFIXES = (".local", ".internal", ".localhost")

_BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "128.0.0.0/16",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "255.255.255.255/32",
        "::/128",
        "::1/128",
        "::/96",
        "::ffff:0:0/96",
        "64:ff9b::/96",
        "fc00::/7",
        "fe80::/10",
        "ff00::/8",
        "2001:db8::/32",
    )
)


def resolve_host(host: str) -> list[str]:
    """Resolve ``host`` to its IP address strings (a test seam)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise ValueError(f"base_url host '{host}' could not be resolved") from exc
    return [info[4][0] for info in infos]


def _normalize_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Unwrap IPv4-embedding IPv6 forms so checks see the real IPv4 target.

    IPv4-mapped (``::ffff:a.b.c.d``) and 6to4 (``2002::/16``) addresses are
    reduced to the embedded IPv4; NAT64 (``64:ff9b::/96``) has no such property
    and is rejected by ``_BLOCKED_NETWORKS`` instead.
    """
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        return mapped
    sixtofour = getattr(address, "sixtofour", None)
    if sixtofour is not None:
        return sixtofour
    return address


def _address_is_global(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    """True only for a parsed address that is safe to reach from the server."""
    address = _normalize_address(address)
    if not address.is_global:
        return False
    return not any(address in network for network in _BLOCKED_NETWORKS)


def _is_global_address(host: str) -> bool:
    """True only for an IP-literal ``host`` that is safe to reach."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return _address_is_global(address)


def _parse_host_address(
    host: str,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse ``host`` as an IP literal, including resolver-only numeric forms.

    ``ipaddress`` accepts only canonical literals, while ``getaddrinfo`` also
    accepts ``2130706433``, ``0x7f000001``, ``0177.0.0.1`` and ``127.1``. Those
    must be recognized as literals too, or the literal-only guard would treat
    them as ordinary host names and let them through.
    """
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    try:
        return ipaddress.ip_address(socket.inet_aton(host))
    except OSError:
        return None


def validate_provider_url(url: str, *, resolve: bool) -> None:
    """Raise ``ValueError`` when ``url`` is not a safe provider base URL.

    ``resolve=False`` skips DNS (used on the request path and at config-write
    time); ``resolve=True`` additionally rejects any host that resolves to a
    non-global address.
    """
    if not isinstance(url, str):
        raise ValueError("base_url must be a string")

    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError("base_url must start with http:// or https://")

    host = parts.hostname
    if not host:
        raise ValueError("base_url must include a host")

    # A trailing dot (``localhost.`` / ``foo.internal.``) denotes the same host;
    # strip it so neither the numeric parse nor the name checks can be bypassed.
    normalized = host.rstrip(".")
    if not normalized:
        raise ValueError("base_url must include a host")
    lowered = normalized.lower()
    address = _parse_host_address(normalized)

    if address is not None:
        if not _address_is_global(address):
            raise ValueError(f"base_url host '{host}' is not a public address")
    elif (
        lowered == "localhost"
        or lowered.endswith(_INTERNAL_SUFFIXES)
        or lowered in _METADATA_HOSTS
    ):
        raise ValueError(f"base_url host '{host}' is not permitted")

    if resolve:
        for address in resolve_host(host):
            if not _is_global_address(address):
                raise ValueError(
                    f"base_url host '{host}' resolves to a non-public address"
                )
