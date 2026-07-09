"""Anti-SSRF para la web del córtex (ADR 0067, Principio 2 — egress controlado).

Las host tools ``web_search`` / ``web_fetch`` (a diferencia de la web NATIVA del
Claude Agent SDK del ADR 0076, donde Anthropic gestiona el fetch) salen a Internet
desde el api-server a través del ``egress-proxy``. Eso abre una superficie SSRF: un
modelo hostil — o un resultado de búsqueda envenenado — podría intentar que el
api-server lea ``http://169.254.169.254/...`` (metadata cloud), ``http://vault:8200``
o cualquier servicio de la red interna.

:func:`assert_safe_url` es la primera línea de defensa, ANTES de cualquier GET:

  * sólo ``http`` / ``https`` (nada de ``file://``, ``gopher://``, ``data:``…);
  * resuelve el host (``getaddrinfo``) y RECHAZA si CUALQUIER IP resuelta es
    privada / loopback / link-local / ULA / metadata cloud (un DNS rebinding que
    apunte a 169.254.169.254 o a 10.x se corta aquí);
  * rechaza también una IP privada/loopback escrita LITERALMENTE en el host (sin
    depender del resolver) y los nombres de metadata (``*.internal``);
  * rechaza puertos fuera del conjunto HTTP permitido (sin esto, ``host:6379``
    sería una puerta a Redis).

Es una función **pura y testeable**: el resolver DNS se inyecta (``resolver=``),
así que los tests no tocan la red real. El egress real lo añade :mod:`api_server
.cortex.web` (GET por el proxy); este módulo sólo decide si una URL es segura.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

# Un resolver DNS inyectable: ``(host, port) -> [ip_str, ...]``. El default usa
# ``socket.getaddrinfo``; los tests inyectan uno determinista.
Resolver = Callable[[str, int], "list[str]"]

# Puertos permitidos para egress web. 80/443 son lo normal; permitimos también el
# rango "HTTP alternativo" típico (8080/8443) por si un proveedor de búsqueda
# self-host (SearXNG) escucha ahí. Cualquier otro puerto se rechaza para que la
# tool no pueda hablar con servicios internos (Redis 6379, Postgres 5432,
# Vault 8200, Ollama 11434…).
ALLOWED_PORTS: frozenset[int] = frozenset({80, 443, 8080, 8443})

# Sufijos de host que NUNCA se permiten — apuntan a servicios internos / metadata
# aunque el DNS devolviese algo "público". Se comparan en minúsculas.
_BLOCKED_HOST_SUFFIXES: tuple[str, ...] = (
    ".internal",
    ".local",
    ".localdomain",
    ".cluster.local",
)
_BLOCKED_HOST_EXACT: frozenset[str] = frozenset(
    {"localhost", "metadata", "metadata.google.internal"}
)


class UnsafeUrlError(ValueError):
    """La URL es insegura para egress (esquema, host, IP o puerto rechazados).

    El mensaje NUNCA filtra un secreto — sólo describe por qué la URL no pasa el
    anti-SSRF. El caller (la tool) lo traduce a un error claro para el modelo."""


def _default_resolver(host: str, port: int) -> list[str]:
    """Resuelve ``host`` vía ``getaddrinfo`` y devuelve las IPs como strings.

    Devuelve TODAS las direcciones (v4 + v6): si CUALQUIERA es privada, la URL se
    rechaza. Una resolución fallida la traduce el caller a :class:`UnsafeUrlError`.
    """
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    out: list[str] = []
    for info in infos:
        sockaddr = info[4]
        if sockaddr:
            out.append(str(sockaddr[0]))
    return out


def _is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True sólo si la IP es enrutable en Internet (ni privada ni especial).

    Bloquea loopback, RFC1918 / ULA, link-local (incl. 169.254.169.254 metadata),
    unspecified (0.0.0.0 / ::), multicast y reservadas. Para IPv6 mapeado a IPv4
    (``::ffff:10.0.0.1``) se evalúa la IPv4 subyacente."""
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _host_is_blocked_name(host: str) -> bool:
    """True si el host es un nombre de metadata/interno que se bloquea sin resolver."""
    h = host.lower().rstrip(".")
    if h in _BLOCKED_HOST_EXACT:
        return True
    return any(h.endswith(suffix) for suffix in _BLOCKED_HOST_SUFFIXES)


def assert_safe_url(
    url: str, *, resolver: Resolver | None = None, allow_internal: bool = False
) -> None:
    """Valida que ``url`` es segura para un GET de egress; lanza si no.

    Pasos (cualquiera que falle levanta :class:`UnsafeUrlError`):

      1. esquema ∈ {http, https};
      2. host presente;
      3. host NO es un nombre de metadata/interno (``*.internal``, ``localhost``…);
      4. puerto (explícito o por defecto del esquema) ∈ :data:`ALLOWED_PORTS`;
      5. si el host es una IP literal, esa IP debe ser pública;
      6. en otro caso, se resuelve el host y TODAS las IPs deben ser públicas.

    ``allow_internal=True`` relaja SOLO los pasos 5/6 (la exigencia de IP
    pública) para un backend de CONFIANZA configurado por el operador (el
    buscador searxng/brave, `cortex.searxng_url`, sólo escribible por un System
    Admin), que por diseño vive en la red interna del docker con IP privada. El
    guard estricto (sin este flag) sigue aplicándose a las URLs de los
    RESULTADOS (web_fetch), que sí son controladas por el modelo/la web. El
    resto de validaciones (esquema, host, nombres de metadata, puerto) se
    mantienen siempre — un flag laxo no abre `file://` ni ``localhost``.

    ``resolver`` se inyecta en tests para no tocar la red; en producción se usa
    ``getaddrinfo``. No devuelve nada (es una aserción)."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise UnsafeUrlError(f"esquema no permitido: {scheme or '(vacío)'!r} (sólo http/https)")

    host = parts.hostname
    if not host:
        raise UnsafeUrlError("la URL no tiene host")

    if _host_is_blocked_name(host):
        raise UnsafeUrlError(f"host interno/metadata no permitido: {host!r}")

    # Puerto: el explícito, o el por defecto del esquema.
    try:
        port = parts.port if parts.port is not None else (443 if scheme == "https" else 80)
    except ValueError as exc:  # puerto no numérico en la URL
        raise UnsafeUrlError("puerto inválido en la URL") from exc
    if port not in ALLOWED_PORTS:
        raise UnsafeUrlError(f"puerto no permitido: {port} (permitidos: {sorted(ALLOWED_PORTS)})")

    # ¿Es el host una IP literal? Entonces se valida directamente, sin resolver.
    literal_ip = _parse_ip_literal(host)
    if literal_ip is not None:
        if not allow_internal and not _is_public_ip(literal_ip):
            raise UnsafeUrlError(f"IP no pública en el host: {host!r}")
        return

    # Nombre de host → resolver; con allow_internal basta que resuelva, si no
    # TODAS sus IPs deben ser públicas (delegado a un helper para acotar ramas).
    resolve = resolver or _default_resolver
    try:
        addresses = resolve(host, port)
    except OSError as exc:
        raise UnsafeUrlError(f"no se pudo resolver el host {host!r}") from exc
    if not addresses:
        raise UnsafeUrlError(f"el host {host!r} no resolvió a ninguna IP")
    if not allow_internal:
        _assert_resolved_ips_public(host, addresses)


def _assert_resolved_ips_public(host: str, addresses: list[str]) -> None:
    """Lanza si alguna de las IPs resueltas de ``host`` no es pública."""
    for addr in addresses:
        ip = _parse_ip_literal(addr)
        if ip is None:
            raise UnsafeUrlError(f"el resolver devolvió un valor no-IP: {addr!r}")
        if not _is_public_ip(ip):
            raise UnsafeUrlError(f"el host {host!r} resuelve a una IP no pública ({addr})")


def _parse_ip_literal(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parsea ``value`` como IP (v4/v6), tolerando el ``%zone`` de IPv6 link-local.

    Devuelve ``None`` cuando no es una IP literal (es un nombre de host)."""
    candidate = value.split("%", 1)[0]
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        return None


__all__ = [
    "ALLOWED_PORTS",
    "Resolver",
    "UnsafeUrlError",
    "assert_safe_url",
]
