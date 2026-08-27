"""Dos ADR con el mismo número hacen ambigua cualquier referencia a ese número.

**El defecto, encontrado el 2026-08-27.** El corpus tiene 161 ADR y dos números
usados dos veces:

* `0053` → `capacidad-de-equipo` (2026-06-04) y `modelo-asistente-personal` (2026-06-08)
* `0054` → `acoplamiento-contexto-proyecto-task` (2026-06-04) y
  `memoria-usuario-asistente` (2026-06-08)

Las dos colisiones son del mismo par de días, así que fue una tanda que se
numeró sin mirar. Y el daño no es estético: **«ADR 0053» aparece en 64 ficheros
y «ADR 0054» en 60**, y hoy ninguna de esas 124 referencias dice a cuál de los
dos apunta. Quien la siga tiene que adivinar por contexto.

**Por qué esto es un trinquete y no una guarda a secas.** Renumerar exigiría
leer las 124 referencias una a una para decidir a cuál se refiere cada una —no
es un `sed`— y elegir cuál de los dos conserva el número es una decisión que no
toca tomar de paso. Así que el inventario de abajo **congela lo que ya está
roto** y prohíbe que crezca: un tercer número repetido pone esto en rojo.

Es el mismo patrón que `test_the_schema_drift_can_only_shrink`, y por el mismo
motivo: una lista que sólo puede menguar mide; una que puede crecer es prosa.
Cuando alguien renumere el par, tiene que **borrar su entrada de aquí**, y el
propio test se lo exige.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ADR_DIR = Path(__file__).resolve().parents[2] / "docs" / "05-architecture-decisions"

#: Colisiones que YA existían cuando se escribió esta guarda. Sólo puede menguar.
#: Al renumerar un par, bórralo de aquí — `test_the_inventory_has_not_grown` y
#: `test_a_frozen_collision_that_got_fixed_is_removed` lo comprueban en los dos
#: sentidos.
_COLISIONES_CONGELADAS: dict[str, tuple[str, ...]] = {
    "0053": (
        "0053-capacidad-de-equipo.md",
        "0053-modelo-asistente-personal.md",
    ),
    "0054": (
        "0054-acoplamiento-contexto-proyecto-task.md",
        "0054-memoria-usuario-asistente.md",
    ),
}

#: Suelo del descubrimiento: por debajo, la guarda pasaría en vacío.
_MINIMO_ADR = 100


def _por_numero() -> dict[str, list[str]]:
    """`{"0053": ["0053-a.md", "0053-b.md"], …}` para todo el corpus."""
    encontrados: dict[str, list[str]] = defaultdict(list)
    for fichero in sorted(_ADR_DIR.glob("*.md")):
        match = re.match(r"^(\d{4})-", fichero.name)
        if match:
            encontrados[match.group(1)].append(fichero.name)
    return dict(encontrados)


_POR_NUMERO = _por_numero()
_COLISIONES = {n: sorted(f) for n, f in _POR_NUMERO.items() if len(f) > 1}


def test_the_guard_still_sees_the_corpus() -> None:
    assert len(_POR_NUMERO) >= _MINIMO_ADR, (
        f"sólo se han descubierto {len(_POR_NUMERO)} números de ADR. O el patrón "
        "de nombre cambió, o el descubrimiento está roto: en cualquiera de los "
        "dos casos el resto del fichero pasaría en vacío."
    )


def test_no_new_adr_number_is_reused() -> None:
    """Un número nuevo repetido rompe aquí, antes de que nazcan sus referencias.

    Las dos colisiones vivas costaron 124 referencias ambiguas porque nadie las
    vio el día que entraron. Esta guarda existe para que la tercera se vea el
    mismo día.
    """
    nuevas = {n: f for n, f in _COLISIONES.items() if n not in _COLISIONES_CONGELADAS}
    assert not nuevas, (
        "hay números de ADR repetidos que no estaban en el inventario: "
        f"{nuevas}. Dale un número libre al nuevo antes de que alguien empiece "
        "a citarlo: hoy «ADR 0053» aparece en 64 ficheros y ninguno dice a cuál "
        "de los dos se refiere."
    )


@pytest.mark.parametrize("numero", sorted(_COLISIONES_CONGELADAS))
def test_a_frozen_collision_that_got_fixed_is_removed(numero: str) -> None:
    """Si el par ya se renumeró, su entrada sobra y hay que borrarla.

    Sin esta mitad el inventario deja de medir: diría que hay dos colisiones
    cuando ya no queda ninguna, y la próxima persona lo leería como deuda viva.
    """
    assert numero in _COLISIONES, (
        f"el número {numero} ya no está repetido, así que su entrada en "
        "`_COLISIONES_CONGELADAS` sobra. Bórrala: un inventario que nombra "
        "deuda saldada miente igual que uno que esconde deuda viva."
    )


@pytest.mark.parametrize("numero", sorted(_COLISIONES_CONGELADAS))
def test_a_frozen_collision_still_has_the_same_files(numero: str) -> None:
    """El par congelado es EXACTAMENTE ese, no «dos ficheros cualesquiera».

    Si uno de los dos se renombra o se suma un tercero al mismo número, el
    inventario ya no describe lo que hay y hay que revisarlo a mano.
    """
    assert tuple(_COLISIONES[numero]) == _COLISIONES_CONGELADAS[numero], (
        f"la colisión {numero} ya no son los mismos ficheros: el inventario dice "
        f"{_COLISIONES_CONGELADAS[numero]} y en disco hay {tuple(_COLISIONES[numero])}."
    )


def test_the_inventory_has_not_grown() -> None:
    """El suelo del trinquete: dos colisiones, y sólo hacia abajo."""
    assert len(_COLISIONES_CONGELADAS) <= 2, (
        "el inventario de colisiones ha crecido. Sólo puede menguar: cada "
        "entrada nueva es un número más que vuelve ambiguas todas sus "
        "referencias."
    )
