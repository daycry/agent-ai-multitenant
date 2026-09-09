"""El mapa de pantallas del panel describe TODAS las rutas, y ninguna inventada.

Plan [`ui-reestructuracion-2026-09-09`](../../docs/roadmap/ui-reestructuracion-2026-09-09.md),
`task_ui_04`, segunda mitad: «`docs/04-reference/` gana el mapa de pantallas por
área (qué ruta, en qué área, para qué rol)».

## Por qué el mapa necesita guarda y no basta con escribirlo

Un mapa de 81 filas escrito a mano nace con errores de transcripción y envejece a
la primera pantalla nueva. Y su modo de fallo es el peor de los de documentación:
**no falla, miente**. Quien lo consulta para saber dónde vive una pantalla —o
para decidir en qué área colocar la siguiente— no tiene forma de notar que la
fila que busca nunca se escribió.

Por eso se comprueba contra el árbol, en las dos direcciones:

* toda ruta que el panel sirve tiene su fila (si el plan añade una pantalla y no
  la documenta, esto se pone rojo);
* toda ruta del mapa existe (una fila que sobrevive a la pantalla que describía
  manda a la gente a un 404).

El descubrimiento se importa de la guarda hermana
[`tests/unit/test_admin_panel_routes_preserved.py`](../unit/test_admin_panel_routes_preserved.py)
en vez de copiarse: dos parsers del mismo árbol se bifurcan, y este repo ya pagó
ese precio (ver la nota de `tests/docs/test_adr_deferrals.py` sobre por qué
importa su parseo del hermano en lugar de duplicarlo).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.unit.test_admin_panel_routes_preserved import _APP_DIR, _discover_routes

pytestmark = pytest.mark.unit

_MAP = (
    Path(__file__).resolve().parents[2] / "docs" / "04-reference" / "mapa-de-pantallas-del-panel.md"
)

#: Una ruta citada en el documento: `` `/admin/algo` `` o `` `/` ``. Se exige el
#: backtick para no confundir una ruta con un trozo de prosa que empiece por `/`.
_ROUTE_IN_DOC_RE = re.compile(r"`(/[a-z0-9\[\]/_-]*)`", re.IGNORECASE)


def _routes_in_the_map() -> set[str]:
    """Las rutas que el documento cita entre backticks."""
    return set(_ROUTE_IN_DOC_RE.findall(_MAP.read_text(encoding="utf-8")))


def test_the_discovery_finds_both_sides() -> None:
    """Sin universo a los dos lados, las dos comparaciones pasan en vacío."""
    assert _MAP.exists(), f"falta el mapa de pantallas ({_MAP.name})"
    assert len(_discover_routes(_APP_DIR)) >= 60, "el descubrimiento de rutas no ve el árbol"
    assert len(_routes_in_the_map()) >= 60, (
        f"el mapa sólo cita {len(_routes_in_the_map())} rutas entre backticks; "
        "¿cambió el formato de la tabla?"
    )


def test_every_route_the_panel_serves_has_a_row() -> None:
    """Una pantalla sin fila es una pantalla que nadie sabe dónde vive."""
    sin_documentar = sorted(_discover_routes(_APP_DIR) - _routes_in_the_map())

    assert not sin_documentar, (
        "estas rutas las sirve el panel y el mapa de pantallas no las nombra:\n"
        + "\n".join(f"  {route}" for route in sin_documentar)
        + f"\n\nAñádelas a {_MAP.name} con su área y su rol, en el mismo commit "
        "que las crea."
    )


def test_the_map_does_not_describe_routes_that_do_not_exist() -> None:
    """Y la dirección contraria: una fila huérfana manda a la gente a un 404."""
    inventadas = sorted(_routes_in_the_map() - _discover_routes(_APP_DIR))

    assert not inventadas, (
        "el mapa de pantallas describe rutas que ningún `page.tsx` sirve:\n"
        + "\n".join(f"  {route}" for route in inventadas)
    )
