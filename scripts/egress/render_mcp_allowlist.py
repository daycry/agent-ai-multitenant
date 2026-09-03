#!/usr/bin/env python3
"""Vuelca la allowlist de hosts MCP remotos al filtro del egress-proxy (ADR 0165).

El ajuste de plataforma `egress.mcp_allowed_hosts` es la fuente de verdad de la
INTENCIÓN; `filter.txt` es la autoridad de lo que el proxy aplica de verdad. Este
script es el puente entre las dos, y **no** es un detalle de implementación: es el
paso que convierte «lo pedí» en «está en vigor», y por eso lo ejecuta una persona
y queda en el historial.

## Por qué es stdlib puro

El ADR decía «reescribe las dos copias del filtro». Su addendum (A4) lo corrigió
con la medida: las dos copias sólo existen **en el repo**
(`docker/egress-proxy/` y el espejo de `installer_backend.stack_assets`). En un
host producido por el instalador hay **una sola**, bajo
`{compose_dir}/stack/egress-proxy/filter.txt`, y ahí no existe ni el árbol
`docker/` ni el paquete `api_server`. Un script que importase el api-server
serviría en el repo y no serviría donde de verdad hace falta, así que aquí sólo
entra la biblioteca estándar y las rutas llegan por argumento.

## Después de esto hay que aplicar

`filter.txt` está horneado en la imagen (`COPY filter.txt /etc/tinyproxy/filter`)
y ningún compose lo monta como bind, así que reescribirlo **no cambia nada** hasta
que el proxy se reconstruye y se recrea:

    docker compose build egress-proxy && docker compose up -d --force-recreate egress-proxy

El script lo dice al terminar. Callarlo sería justo la deriva que el ADR evita:
un fichero cambiado, un operador convencido de que ya está, y los runs siguiendo
muertos con `403 Filtered`.

## Uso

    python3 scripts/egress/render_mcp_allowlist.py --hosts-json hosts.json
    python3 scripts/egress/render_mcp_allowlist.py --host mcp.atlassian.com --host api.github.com
    python3 scripts/egress/render_mcp_allowlist.py --hosts-json - < hosts.json  # por stdin

Sin `--filter` escribe las dos copias del repo (lo que quiere un checkout); con
`--filter <ruta>` (repetible) escribe exactamente las que se le digan, que es lo
que quiere un host instalado.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

BLOCK_BEGIN = "# >>> BEGIN generated: egress.mcp_allowed_hosts — NO EDITAR A MANO"
BLOCK_END = "# <<< END generated: egress.mcp_allowed_hosts"

MAX_HOSTS = 100
_MAX_HOST_LEN = 253
_HOST_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$"
)

_RAIZ = Path(__file__).resolve().parents[2]
FILTROS_DEL_REPO = (
    _RAIZ / "docker" / "egress-proxy" / "filter.txt",
    _RAIZ
    / "apps"
    / "installer"
    / "backend"
    / "src"
    / "installer_backend"
    / "stack_assets"
    / "egress-proxy"
    / "filter.txt",
)


class HostInvalidoError(ValueError):
    """Un host no puede entrar en el filtro. El motivo va en el mensaje."""


def normalise_host(raw: str) -> str:
    """Minúsculas, sin punto final, y sólo si es un FQDN ASCII bien formado.

    Es una copia DELIBERADA y mínima de la validación de
    `api_server.egress.mcp_allowlist`: este script no puede importar aquel módulo
    (ver el docstring), y prefiere rechazar de más a escribir en el filtro algo
    que el api-server habría rechazado. La validación buena es la del api-server,
    que es la que ve el operador al guardar; ésta es la última red.
    """
    host = str(raw).strip().rstrip(".").lower()
    if not host:
        raise HostInvalidoError("entrada vacía")
    if not host.isascii():
        raise HostInvalidoError(f"{raw!r}: sólo ASCII (usa la forma punycode)")
    if len(host) > _MAX_HOST_LEN or not _HOST_RE.match(host):
        raise HostInvalidoError(f"{raw!r}: no es un nombre de dominio bien formado")
    return host


def render_generated_block(hosts: list[str]) -> str:
    """El bloque entre centinelas: una línea ERE anclada por host, ordenadas."""
    canonicos = sorted({normalise_host(h) for h in hosts})
    if len(canonicos) > MAX_HOSTS:
        raise HostInvalidoError(f"{len(canonicos)} hosts y el tope son {MAX_HOSTS}")
    lineas = [
        BLOCK_BEGIN,
        "# Lo reescribe `scripts/egress/render_mcp_allowlist.py` desde el ajuste de",
        "# plataforma `egress.mcp_allowed_hosts` (ADR 0165). Editarlo a mano hace que",
        "# el fichero y el ajuste digan cosas distintas, que es justo lo que el ADR evita.",
    ]
    lineas.extend("^" + h.replace(".", r"\.") + "$" for h in canonicos)
    lineas.append(BLOCK_END)
    return "\n".join(lineas)


def replace_generated_block(
    texto: str, bloque: str, begin: str = BLOCK_BEGIN, end: str = BLOCK_END
) -> str:
    """Sustituye EN SITIO el bloque delimitado; si no está, lo añade al final.

    Lo de fuera de los centinelas no se toca nunca: ahí viven las entradas
    escritas a mano —los proveedores LLM, los hosts del córtex, el comodín de
    APIM— que sostienen que el stack funcione y cuya gobernanza es el PR, no un
    ajuste.
    """
    i = texto.find(begin)
    j = texto.find(end)
    if i == -1 or j == -1 or j < i:
        sufijo = "" if texto.endswith("\n") else "\n"
        return f"{texto}{sufijo}\n{bloque}\n"
    return texto[:i] + bloque + texto[j + len(end) :]


def _leer_hosts(args: argparse.Namespace) -> list[str]:
    if args.host:
        return list(args.host)
    if args.hosts_json:
        crudo = (
            sys.stdin.read()
            if args.hosts_json == "-"
            else Path(args.hosts_json).read_text(encoding="utf-8")
        )
        datos = json.loads(crudo)
        if isinstance(datos, dict):  # acepta el cuerpo del ajuste tal cual
            datos = datos.get("value", [])
        if not isinstance(datos, list):
            raise HostInvalidoError("el JSON tiene que ser una lista de hosts")
        return [str(x) for x in datos]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--host", action="append", help="host a permitir (repetible)")
    parser.add_argument("--hosts-json", help="fichero JSON con la lista (o `-` para stdin)")
    parser.add_argument(
        "--filter",
        action="append",
        type=Path,
        help="ruta del filter.txt a reescribir (repetible). Por defecto, las dos del repo",
    )
    parser.add_argument("--check", action="store_true", help="no escribe: dice si algo cambiaría")
    args = parser.parse_args(argv)

    try:
        hosts = _leer_hosts(args)
        bloque = render_generated_block(hosts)
    except (HostInvalidoError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    destinos = list(args.filter) if args.filter else list(FILTROS_DEL_REPO)
    cambiados = []
    for ruta in destinos:
        if not ruta.is_file():
            print(f"error: no existe {ruta}", file=sys.stderr)
            return 2
        antes = ruta.read_text(encoding="utf-8")
        despues = replace_generated_block(antes, bloque)
        if antes == despues:
            continue
        cambiados.append(ruta)
        if not args.check:
            ruta.write_text(despues, encoding="utf-8", newline="\n")

    if args.check:
        print("cambiaría: " + (", ".join(str(r) for r in cambiados) or "nada"))
        return 1 if cambiados else 0

    if not cambiados:
        print(f"{len(hosts)} host(s): el filtro ya decía eso, no se ha tocado nada.")
        return 0

    print(f"{len(hosts)} host(s) escritos en: " + ", ".join(str(r) for r in cambiados))
    print(
        "\nFALTA APLICARLO. El filtro está horneado en la imagen del proxy, así que hasta que\n"
        "no se reconstruya y se recree, el egress sigue como estaba:\n"
        "  docker compose build egress-proxy && docker compose up -d --force-recreate egress-proxy"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
