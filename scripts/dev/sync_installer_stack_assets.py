#!/usr/bin/env python
"""Vuelve a copiar en el instalador los auxiliares del stack, desde ``docker/``.

Los ficheros que el compose generado monta (los scripts de ``postgres/init``, la
config de Vault, los perfiles seccomp, los dos contextos de tinyproxy y la
configuración de monitorización) viajan **dentro** del paquete
``installer_backend``: en el destino no hay ningún ``docker/`` del que copiarlos,
porque el compose se escribe bajo la raíz de datos y la imagen del instalador sólo
lleva ``src/``. El porqué completo está en
``apps/installer/backend/src/installer_backend/stack_assets/__init__.py``.

La fuente de verdad sigue siendo ``docker/``. Este script existe para que
sincronizar no sea copiar veintitrés ficheros a mano —que es como se introduce la
deriva que la guarda persigue—, y para que el mensaje de esa guarda tenga un
comando que ofrecer:

    .venv/Scripts/python.exe scripts/dev/sync_installer_stack_assets.py

Sin argumentos copia lo que difiera y dice qué tocó. Con ``--check`` no escribe
nada y sale con rc=1 si algo ha derivado.

Qué NO hace, y conviene saberlo antes de fiarse de un «Al día»
--------------------------------------------------------------
Este script trabaja **sobre el manifiesto**: sincroniza el contenido de lo que ya
está declarado. No decide qué debe viajar, así que un fichero NUEVO bajo
``docker/postgres/init/`` —el caso caro, porque la instalación no lo ejecutaría
jamás— le es invisible: no está en el manifiesto, luego no lo mira.

Quien sí lo mira es la guarda, ``tests/unit/test_installer_ships_stack_assets.py``,
que recorre el árbol bajo un conjunto de raíces declaradas y exige que cada
fichero esté en el manifiesto o en una exclusión escrita. Vigila además la
dirección contraria (nada en el paquete sin entrada en el manifiesto) y que el
empaquetado siga metiendo los auxiliares en el wheel. Las dos cosas se
complementan: la guarda dice *qué falta*, este script arregla *lo que derivó*.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "apps" / "installer" / "backend" / "src"))

from installer_backend import stack_assets  # noqa: E402  (tras ajustar sys.path)

_PACKAGE_ROOT = (
    _REPO_ROOT / "apps" / "installer" / "backend" / "src" / "installer_backend" / "stack_assets"
)


def _normalised(path: Path) -> str:
    """El texto tal y como lo lee el instalador: finales de línea a ``\\n``."""

    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def main() -> int:
    # RawDescription: el docstring lleva párrafos y un encabezado subrayado, y el
    # formateador por defecto los aplasta en un solo bloque ilegible. Quien pide
    # `--help` aquí es quien acaba de tocar `docker/` y necesita entender el
    # alcance del script, no un párrafo continuo.
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="no escribe nada; sale con rc=1 si algún auxiliar ha derivado",
    )
    args = parser.parse_args()

    derivados: list[str] = []
    faltan_fuentes: list[str] = []
    for asset in stack_assets.ALL_ASSETS:
        origen = _REPO_ROOT / asset.source
        destino = _PACKAGE_ROOT / asset.path
        if not origen.is_file():
            faltan_fuentes.append(asset.source)
            continue
        esperado = _normalised(origen)
        if destino.is_file() and _normalised(destino) == esperado:
            continue
        derivados.append(asset.path)
        if not args.check:
            destino.parent.mkdir(parents=True, exist_ok=True)
            # newline="\n": el destino es un host Linux, y un `.sh` con retornos
            # de carro no arranca. No dependemos de la plataforma del que copia.
            destino.write_text(esperado, encoding="utf-8", newline="\n")

    if faltan_fuentes:
        print("Fuentes que el manifiesto declara y ya no existen:", file=sys.stderr)
        for source in faltan_fuentes:
            print(f"  {source}", file=sys.stderr)
        print(
            "Actualiza el manifiesto de installer_backend.stack_assets.",
            file=sys.stderr,
        )
        return 1

    if not derivados:
        print(f"Al día: {len(stack_assets.ALL_ASSETS)} auxiliares idénticos a docker/.")
        return 0

    if args.check:
        print(f"{len(derivados)} auxiliar(es) han derivado de docker/:", file=sys.stderr)
        for path in derivados:
            print(f"  {path}", file=sys.stderr)
        return 1

    print(f"Sincronizados {len(derivados)} auxiliar(es):")
    for path in derivados:
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
