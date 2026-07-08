"""task_prod12_ssrf_01 — guard de destino con resolución y validación de IP."""

from __future__ import annotations

import socket
from typing import Any

import pytest
from agent_runtime.ssrf_guard import (
    PinnedDestination,
    SsrfViolationError,
    pinned_url,
    validate_destination,
)


def _resolver_for(*addrs: str) -> Any:
    """Fake getaddrinfo devolviendo las IPs dadas (sin red en los tests)."""

    def _resolve(_host: str, _port: Any, **_kw: Any) -> list[tuple[Any, ...]]:
        return [
            (socket.AF_INET6 if ":" in a else socket.AF_INET, None, None, "", (a, 0)) for a in addrs
        ]

    return _resolve


# --- IP literales -----------------------------------------------------------


@pytest.mark.parametrize(
    "literal",
    ["169.254.169.254", "127.0.0.1", "10.1.2.3", "8.8.8.8", "[::1]", "fd00::1"],
)
def test_literal_ips_are_rejected(literal: str) -> None:
    with pytest.raises(SsrfViolationError, match="literal IP"):
        validate_destination(literal, resolver=_resolver_for("8.8.8.8"))


# --- rangos prohibidos ------------------------------------------------------


@pytest.mark.parametrize(
    ("addr", "reason"),
    [
        ("169.254.169.254", "metadata"),
        ("127.0.0.1", "loopback"),
        ("10.0.0.5", "private"),
        ("192.168.1.10", "private"),
        ("172.16.0.9", "private"),
        ("169.254.10.10", "link-local"),
        ("::1", "loopback"),
        ("fd12::5", "private"),
        ("fe80::1", "link-local"),
    ],
)
def test_internal_resolutions_are_rejected(addr: str, reason: str) -> None:
    with pytest.raises(SsrfViolationError, match=reason):
        validate_destination("evil.example.com", resolver=_resolver_for(addr))


def test_mixed_public_and_private_is_rejected() -> None:
    # Rebinding clásico: un nombre con una IP pública Y una interna — si
    # CUALQUIERA es interna, se rechaza entero.
    with pytest.raises(SsrfViolationError, match="private"):
        validate_destination(
            "dual.example.com", resolver=_resolver_for("93.184.216.34", "10.0.0.5")
        )


def test_unresolvable_host_is_rejected() -> None:
    def _fail(_host: str, _port: Any, **_kw: Any) -> list[Any]:
        raise socket.gaierror("Name or service not known")

    with pytest.raises(SsrfViolationError, match="cannot resolve"):
        validate_destination("nope.example.com", resolver=_fail)


# --- camino feliz + pin -----------------------------------------------------


def test_public_host_pins_ipv4_first() -> None:
    pin = validate_destination(
        "example.com", resolver=_resolver_for("2606:2800:220:1::1", "93.184.216.34")
    )
    assert isinstance(pin, PinnedDestination)
    assert pin.host == "example.com"
    assert pin.ip == "93.184.216.34"
    assert set(pin.all_ips) == {"2606:2800:220:1::1", "93.184.216.34"}


def test_pinned_url_rewrites_host_and_preserves_the_rest() -> None:
    pin = validate_destination("example.com", resolver=_resolver_for("93.184.216.34"))
    out = pinned_url("https://example.com:8443/a/b?x=1#frag", pin)
    assert out == "https://93.184.216.34:8443/a/b?x=1#frag"


def test_pinned_url_brackets_ipv6() -> None:
    pin = validate_destination("v6.example.com", resolver=_resolver_for("2606:2800:220:1::1"))
    out = pinned_url("https://v6.example.com/path", pin)
    assert out == "https://[2606:2800:220:1::1]/path"
