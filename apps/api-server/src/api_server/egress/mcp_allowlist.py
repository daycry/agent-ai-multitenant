"""La allowlist de hosts MCP remotos: validar y renderizar (`task_mk_02`, ADR 0165).

El ajuste de plataforma `egress.mcp_allowed_hosts` guarda **hostnames en claro,
nunca regexes**; este módulo es el único sitio que decide si un hostname puede
entrar y cómo se convierte en la línea del filtro de tinyproxy.

## Por qué el alfabeto es tan estrecho

`filter.txt` es una **regex ERE por línea** evaluada contra el destino del CONNECT,
con `FilterDefaultDeny Yes`. Dos formas de romperlo, las dos silenciosas:

- Un punto sin escapar es un comodín: `mcp.atlassian.com` casaría `mcpXatlassianYcom`.
- Una línea sin anclar casa por prefijo: `mcp.atlassian.com` dejaría pasar
  `evil-mcp.atlassian.com.attacker.tld`.
- Y una entrada con metacaracteres (`.*`) **abre el proxy entero**.

Por eso el validador reduce el alfabeto a `[a-z0-9.-]` y el renderizador ancla
siempre. Con ese alfabeto el único metacarácter que puede aparecer es el punto,
y se escapa sólo ese: `re.escape` emite además `\\-` para el guion, y en POSIX ERE
una barra invertida delante de un carácter ordinario es **comportamiento
indefinido** — glibc y musl lo toleran hoy, el contrato no lo garantiza.

## Lo no-ASCII se rechaza, no se normaliza

El ADR 0165 prescribía normalizar por IDNA para cerrar el homógrafo unicode, y su
addendum lo corrigió con la medida: un `paypal.com` escrito con dos letras
cirílicas se codifica en IDNA como `xn--ypal-43d9g.com`, un host **válido** que
pasa el alfabeto sin despeinarse. IDNA no rechaza el homógrafo: lo admite. Así
que aquí se rechaza toda entrada no-ASCII y se pide el punycode explícito — que
es exactamente la diferencia entre una decisión auditable y una que se toma sola.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable

from api_server.cortex.web_safety import host_is_blocked_name

__all__ = [
    "BLOCK_BEGIN",
    "BLOCK_END",
    "MAX_HOSTS",
    "InvalidMcpHostError",
    "normalise_host",
    "render_filter_line",
    "render_generated_block",
]

#: Tope duro de entradas. No es rendimiento —tinyproxy compila las regex una vez—
#: sino legibilidad: una allowlist que nadie puede leer de un vistazo ha dejado de
#: ser una allowlist.
MAX_HOSTS = 100

#: Centinelas del bloque que reescribe el renderizador. Se conservan aunque el
#: bloque quede vacío: sin ellos, el script no sabría dónde volver a escribir y
#: acabaría añadiendo un segundo bloque en cada pasada.
BLOCK_BEGIN = "# >>> BEGIN generated: egress.mcp_allowed_hosts — NO EDITAR A MANO"
BLOCK_END = "# <<< END generated: egress.mcp_allowed_hosts"

_MAX_HOST_LEN = 253
_MAX_LABEL_LEN = 63
#: Etiquetas de 1 a 63, sin guion inicial ni final, y al menos un punto.
_HOST_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$"
)


class InvalidMcpHostError(ValueError):
    """Un host no puede entrar en la allowlist, y se dice por qué.

    El ``reason`` viaja al 422 del ajuste: un rechazo que no dice el motivo
    obliga al operador a adivinar, y adivinar sobre una lista de egress acaba en
    una entrada más permisiva «por probar».
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _looks_like_ip(host: str) -> bool:
    """¿La entrada es una IP literal (v4, v6, o v6 entre corchetes)?

    Una IP no se puede auditar por reputación ni revocar por DNS, así que no
    entra aunque sea pública.
    """
    candidato = host.strip("[]")
    try:
        ipaddress.ip_address(candidato)
    except ValueError:
        return False
    return True


def normalise_host(raw: str | None) -> str:
    """Devuelve el hostname canónico, o :class:`InvalidMcpHostError` con su motivo.

    El orden importa: se minusculiza ANTES de cualquier otra cosa porque la
    codificación IDNA no baja a minúsculas y un host legítimo escrito en
    mayúsculas rebotaría contra el alfabeto.
    """
    if raw is None or not str(raw).strip():
        raise InvalidMcpHostError("la entrada está vacía")

    host = str(raw).strip().rstrip(".").lower()

    for falla, motivo in (
        (
            not host.isascii(),
            "sólo se admiten hosts ASCII: escribe la forma punycode (por ejemplo "
            "`xn--ypal-43d9g.com`) para que la entrada sea auditable a simple vista",
        ),
        (
            _looks_like_ip(host),
            f"`{host}` es una IP literal: no se puede auditar por reputación ni revocar por DNS",
        ),
        (
            "." not in host,
            f"`{host}` no tiene punto, así que es un nombre de servicio del compose: "
            "un MCP interno no necesita allowlist, se exime por NO_PROXY",
        ),
        (len(host) > _MAX_HOST_LEN, f"el host pasa de {_MAX_HOST_LEN} caracteres"),
        (
            any(len(label) > _MAX_LABEL_LEN for label in host.split(".")),
            f"alguna etiqueta del host pasa de {_MAX_LABEL_LEN} caracteres",
        ),
        (
            not _HOST_RE.match(host),
            f"`{host}` no es un nombre de dominio bien formado: se admite sólo el alfabeto "
            "`[a-z0-9.-]`, con etiquetas que no empiezan ni acaban en guion "
            "(nada de esquema, puerto, ruta ni comodines)",
        ),
        (
            host_is_blocked_name(host),
            f"`{host}` es un nombre interno o de metadata bloqueado: abrirlo daría al sandbox "
            "una vía hacia el interior del stack",
        ),
    ):
        if falla:
            raise InvalidMcpHostError(motivo)
    return host


def render_filter_line(host: str) -> str:
    """La línea ERE de ``host``: anclada, y con el punto —único metacarácter que el
    alfabeto permite— como lo único escapado."""
    canonico = normalise_host(host)
    return "^" + canonico.replace(".", r"\.") + "$"


def render_generated_block(hosts: Iterable[str]) -> str:
    """El bloque completo entre centinelas, listo para sustituir en `filter.txt`.

    Ordenado y sin duplicados a propósito: el fichero se lee en revisiones y en
    incidencias, y un orden estable hace que su `diff` signifique algo.
    """
    canonicos = sorted({normalise_host(h) for h in hosts})
    if len(canonicos) > MAX_HOSTS:
        raise InvalidMcpHostError(
            f"la allowlist tiene {len(canonicos)} hosts y el tope son {MAX_HOSTS}: "
            "una lista que nadie puede leer de un vistazo ha dejado de ser una allowlist"
        )
    lineas = [
        BLOCK_BEGIN,
        "# Lo reescribe `scripts/egress/render-mcp-allowlist.py` desde el ajuste de",
        "# plataforma `egress.mcp_allowed_hosts` (ADR 0165). Editarlo a mano hace que",
        "# el fichero y el ajuste digan cosas distintas, que es justo lo que el ADR evita.",
    ]
    lineas.extend("^" + h.replace(".", r"\.") + "$" for h in canonicos)
    lineas.append(BLOCK_END)
    return "\n".join(lineas)
