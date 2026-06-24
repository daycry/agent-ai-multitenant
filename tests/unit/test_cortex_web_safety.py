"""Unit: anti-SSRF para la web del córtex (ADR 0067).

``assert_safe_url`` es la primera línea de defensa contra SSRF para las host tools
``web_search`` / ``web_fetch`` (que SÍ salen a Internet, a diferencia de la web
nativa del SDK del ADR 0076). Se prueba como función PURA: el resolver DNS
(``getaddrinfo``) se inyecta para no tocar la red real.

Lo que debe RECHAZAR:
  * esquemas que no sean http/https (file://, ftp://, gopher://, data:);
  * un host que resuelva a loopback (127.0.0.1, ::1);
  * IPs privadas RFC1918 (10.x, 192.168.x, 172.16.x);
  * link-local + metadata cloud (169.254.169.254);
  * ULA IPv6 (fc00::/7) y link-local IPv6 (fe80::/10);
  * ``*.internal`` y otros nombres de metadata sin resolver;
  * puertos "raros" (no 80/443 ni el rango HTTP habitual permitido).

Lo que debe ACEPTAR: un https público cuyo host resuelva a una IP pública.
"""

from __future__ import annotations

import pytest
from api_server.cortex.web_safety import UnsafeUrlError, assert_safe_url

pytestmark = pytest.mark.unit


def _resolver(ip: str):
    """Un resolver inyectable que mapea CUALQUIER host a una IP fija."""

    def _resolve(host: str, port: int) -> list[str]:
        return [ip]

    return _resolve


# Resolver que devuelve una IP pública (8.8.8.8) para cualquier host.
_PUBLIC = _resolver("8.8.8.8")


# ---------------------------------------------------------------------------
# Acepta https público
# ---------------------------------------------------------------------------
def test_accepts_public_https() -> None:
    # No debe lanzar.
    assert_safe_url("https://example.com/path?q=1", resolver=_PUBLIC)


def test_accepts_public_http_default_port() -> None:
    assert_safe_url("http://example.com/", resolver=_PUBLIC)


def test_accepts_explicit_https_port_443() -> None:
    assert_safe_url("https://example.com:443/x", resolver=_PUBLIC)


# ---------------------------------------------------------------------------
# Rechaza esquemas no http/https
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "gopher://example.com/",
        "data:text/plain,hello",
        "javascript:alert(1)",
        "//example.com/x",  # sin esquema
        "example.com/x",  # sin esquema
    ],
)
def test_rejects_non_http_schemes(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        assert_safe_url(url, resolver=_PUBLIC)


# ---------------------------------------------------------------------------
# Rechaza loopback / privadas / link-local / metadata (por IP resuelta)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",  # loopback
        "10.0.0.5",  # RFC1918
        "192.168.1.1",  # RFC1918
        "172.16.0.1",  # RFC1918
        "169.254.169.254",  # cloud metadata / link-local
        "::1",  # loopback IPv6
        "fe80::1",  # link-local IPv6
        "fd00::1",  # ULA IPv6
        "0.0.0.0",  # unspecified
    ],
)
def test_rejects_private_resolved_ip(ip: str) -> None:
    with pytest.raises(UnsafeUrlError):
        assert_safe_url("https://evil.example/", resolver=_resolver(ip))


def test_rejects_metadata_hostname_without_resolving() -> None:
    # *.internal debe rechazarse aunque el resolver devolviese algo público.
    with pytest.raises(UnsafeUrlError):
        assert_safe_url("https://foo.internal/x", resolver=_PUBLIC)


def test_rejects_metadata_ip_literal_in_host() -> None:
    # Una IP privada literal en el host se rechaza aunque el resolver fuese permisivo.
    with pytest.raises(UnsafeUrlError):
        assert_safe_url("http://169.254.169.254/latest/meta-data/", resolver=_PUBLIC)


def test_rejects_loopback_ip_literal_in_host() -> None:
    with pytest.raises(UnsafeUrlError):
        assert_safe_url("http://127.0.0.1:8200/v1/secret", resolver=_PUBLIC)


# ---------------------------------------------------------------------------
# Rechaza puertos raros
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("port", [22, 25, 3306, 5432, 6379, 8200, 11434])
def test_rejects_weird_ports(port: int) -> None:
    with pytest.raises(UnsafeUrlError):
        assert_safe_url(f"http://example.com:{port}/x", resolver=_PUBLIC)


# ---------------------------------------------------------------------------
# Host vacío / URL malformada
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("url", ["", "https://", "http:///path"])
def test_rejects_empty_host(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        assert_safe_url(url, resolver=_PUBLIC)


# ---------------------------------------------------------------------------
# El resolver real es el default (no se cuela red): un resolver que falla
# (DNS no resuelve) también es un rechazo limpio, no un crash inesperado.
# ---------------------------------------------------------------------------
def test_dns_failure_is_unsafe() -> None:
    def _boom(host: str, port: int) -> list[str]:
        raise OSError("dns down")

    with pytest.raises(UnsafeUrlError):
        assert_safe_url("https://example.com/", resolver=_boom)
