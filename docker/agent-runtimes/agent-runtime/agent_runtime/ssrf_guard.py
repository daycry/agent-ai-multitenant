"""SSRF destination guard for the HTTP tools (prod-12 Fase A — gap4-1, gap4-3).

The domain allowlist alone compares hostnames as TEXT: it neither rejects hosts
that RESOLVE into internal ranges (loopback, RFC1918, link-local, the cloud
metadata endpoint) nor prevents DNS rebinding (httpx re-resolving the name
AFTER validation). This module closes both holes:

  * :func:`validate_destination` resolves the hostname ONCE (A + AAAA),
    validates EVERY resolved address against the internal-range denylist and
    returns a :class:`PinnedDestination` with the address to connect to;
  * :func:`pinned_url` rewrites the request URL onto the pinned IP — together
    with the original ``Host`` header and TLS SNI (the tools set both) the
    connection can only land on the address that was validated (no TOCTOU).

Literal IP URLs are rejected outright: allowlist entries are FQDNs (validated
server-side, task_prod12_ssrf_03), so a literal can never be a legitimate
destination and accepting one would just re-open the denylist bypass game.

Deny-by-range, never by name: the guard runs AFTER the allowlist check and is
independent of it — defence in depth even if the allowlist is misconfigured.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

#: Resolver signature (test seam) — mirrors ``socket.getaddrinfo``.
Resolver = Callable[..., Sequence[tuple[Any, Any, Any, Any, Any]]]

_METADATA_V4 = ipaddress.ip_address("169.254.169.254")


class SsrfViolationError(Exception):
    """The destination failed SSRF validation (reason in ``str(exc)``)."""


@dataclass(frozen=True)
class PinnedDestination:
    """A validated destination: the ORIGINAL host + the pinned, safe IP."""

    host: str
    ip: str
    all_ips: tuple[str, ...]


# Ordered checks: the specific labels go BEFORE `is_private` (which also covers
# RFC1918, IPv6 ULA fc00::/7 — includes fd00::/8 — and ::1) so errors read
# precisely; the metadata endpoint is matched by equality first.
_FORBIDDEN_CHECKS: tuple[tuple[str, Callable[[Any], bool]], ...] = (
    ("cloud metadata endpoint", lambda ip: ip == _METADATA_V4),
    ("loopback", lambda ip: ip.is_loopback),
    ("link-local", lambda ip: ip.is_link_local),
    ("private range", lambda ip: ip.is_private),
    ("reserved range", lambda ip: ip.is_reserved),
    ("multicast", lambda ip: ip.is_multicast),
    ("unspecified", lambda ip: ip.is_unspecified),
)


def _forbidden_reason(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """Why this address must never be reached from an agent tool, or ``None``."""
    for reason, check in _FORBIDDEN_CHECKS:
        if check(ip):
            return reason
    return None


def validate_destination(
    host: str, *, resolver: Resolver = socket.getaddrinfo
) -> PinnedDestination:
    """Resolve ``host`` once, validate ALL its addresses, return the pin.

    Raises :class:`SsrfViolationError` when the host is a literal IP, cannot be
    resolved, or ANY resolved address falls in a forbidden range (a rebinding
    name that mixes a public and an internal address is rejected outright).
    """
    bare = host.strip().strip("[]")
    try:
        ipaddress.ip_address(bare)
    except ValueError:
        pass
    else:
        raise SsrfViolationError(
            f"literal IP addresses are not allowed: {host!r} (use an allowlisted domain)"
        )

    try:
        infos = resolver(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise SsrfViolationError(f"cannot resolve host {host!r}: {exc}") from exc

    ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    seen: set[str] = set()
    for info in infos:
        sockaddr = info[4]
        addr = str(sockaddr[0])
        # scope-id de IPv6 link-local ("fe80::1%eth0") — sepáralo para parsear.
        addr = addr.split("%", 1)[0]
        if addr in seen:
            continue
        seen.add(addr)
        try:
            ips.append(ipaddress.ip_address(addr))
        except ValueError:
            continue
    if not ips:
        raise SsrfViolationError(f"host {host!r} resolved to no usable addresses")

    for ip in ips:
        reason = _forbidden_reason(ip)
        if reason is not None:
            raise SsrfViolationError(f"host {host!r} resolves to forbidden address {ip} ({reason})")

    # Pin IPv4 first (URL rewriting without brackets); otherwise the first IPv6.
    pinned = next((ip for ip in ips if ip.version == 4), ips[0])
    return PinnedDestination(host=host, ip=str(pinned), all_ips=tuple(str(ip) for ip in ips))


def pinned_url(url: str, pin: PinnedDestination) -> str:
    """Rewrite ``url``'s host to the pinned IP, preserving scheme/port/path/query.

    The caller MUST send the original ``Host`` header and TLS SNI
    (``extensions={"sni_hostname": pin.host}``) so virtual hosting and
    certificate validation keep working against the real hostname.
    """
    parts = urlsplit(url)
    host = pin.ip if ":" not in pin.ip else f"[{pin.ip}]"
    netloc = host if parts.port is None else f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


__all__ = [
    "PinnedDestination",
    "Resolver",
    "SsrfViolationError",
    "pinned_url",
    "validate_destination",
]
