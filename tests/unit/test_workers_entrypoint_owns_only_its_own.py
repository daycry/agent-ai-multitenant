"""El worker no se apropia de los datos de los demás servicios.

**El defecto (2026-08-28, e2e run 33187158257).** `apps/workers/docker-entrypoint.sh`
hacía `chown -R 1000:1000 "$DATA_ROOT"` — la raíz ENTERA de datos, que contiene
también los directorios de postgres, redis, minio, vault y caddy.

Llevaba así desde 2026-07-02 y nadie lo notó, porque con `cap_drop: ALL` el
chown fallaba en silencio: la línea lleva `|| true`. El día que se le devolvió
`CAP_CHOWN` al worker —para que pudiera hacer `setpriv` y arrancar— el chown
pasó a **funcionar**, y se llevó por delante la PKI de Caddy:

    provisioning CA 'local': loading root cert:
      open /data/caddy/pki/authorities/local/root.crt: permission denied

Dos lecciones que este fichero conserva:

* Un servicio que se apropia de los datos de los demás es un fallo **por sí
  mismo**. Que estuviera tapado por un `|| true` no lo hacía menos grave: lo
  hacía invisible, y lo dejaba listo para aparecer el día que algo cambiara.
* Un `|| true` convierte un error en silencio. Aquí ocultó un bug durante casi
  dos meses y lo devolvió en el peor momento — a mitad de una tanda de arreglos,
  disfrazado de regresión nueva.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ENTRYPOINT = Path(__file__).resolve().parents[2] / "apps" / "workers" / "docker-entrypoint.sh"

#: Directorios del árbol de datos que NO son del worker. Si el entrypoint
#: llegara a tocarlos, se los quita a su dueño.
_DE_OTROS = ("postgres", "redis", "minio", "vault", "caddy", "clamav", "grafana")


def _lineas_de_codigo() -> list[str]:
    """El script sin comentarios: lo que se ejecuta, no lo que se explica."""
    return [
        linea.split("#", 1)[0].rstrip()
        for linea in _ENTRYPOINT.read_text(encoding="utf-8").splitlines()
        if linea.split("#", 1)[0].strip()
    ]


def test_el_entrypoint_existe_y_se_puede_leer() -> None:
    """Sin esto, un renombrado dejaría las demás guardas pasando en vacío."""
    assert _ENTRYPOINT.is_file(), f"no existe {_ENTRYPOINT}"
    assert len(_lineas_de_codigo()) > 10, "el script ha quedado vacío o el parseo se ha roto"


def test_no_hace_chown_de_la_raiz_entera() -> None:
    """El defecto exacto: `chown -R` sobre `$DATA_ROOT`."""
    culpables = [
        linea
        for linea in _lineas_de_codigo()
        if "chown" in linea and re.search(r'"\$\{?DATA_ROOT\}?"\s*(\|\||$)', linea)
    ]
    assert not culpables, (
        f"el entrypoint hace chown de la raíz de datos: {culpables}.\n"
        "Ahí viven también postgres, redis, minio, vault y caddy. Cámbialo por "
        "los subárboles que el worker usa de verdad (WORKER_OWNED)."
    )


def test_declara_que_subarboles_son_suyos() -> None:
    """La lista tiene que existir y ser explícita, no deducirse al vuelo."""
    codigo = "\n".join(_lineas_de_codigo())
    casa = re.search(r'WORKER_OWNED="([^"]+)"', codigo)
    assert casa, "no se declara WORKER_OWNED: sin lista explícita esto vuelve a crecer"
    declarados = set(casa.group(1).split())
    assert "projects" in declarados and "worktrees" in declarados, (
        f"WORKER_OWNED={sorted(declarados)} no incluye los bare repos ni los "
        "worktrees, que son justamente lo que el worker provisiona"
    )
    intrusos = declarados & set(_DE_OTROS)
    assert not intrusos, (
        f"WORKER_OWNED reclama {sorted(intrusos)}, que son de otros servicios. "
        "Apropiarse de su directorio les quita el acceso a sus propios datos: "
        "es exactamente el fallo que tumbó a Caddy."
    )


@pytest.mark.parametrize("ajeno", _DE_OTROS)
def test_no_nombra_el_directorio_de_otro_servicio(ajeno: str) -> None:
    """Ni siquiera de pasada: el worker no tiene nada que hacer ahí."""
    codigo = "\n".join(_lineas_de_codigo())
    assert f"/{ajeno}" not in codigo and f'"{ajeno}"' not in codigo, (
        f"el entrypoint del worker menciona `{ajeno}` en código ejecutable. "
        "Ese directorio es de otro servicio."
    )
