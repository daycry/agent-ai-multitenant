"""Excluir una tabla del autogenerate exige motivo escrito y acceso revocado.

`TABLES_WITHOUT_A_MODEL` es una venda sobre `alembic check`: lo que entra ahí
deja de compararse contra el modelo, para siempre y en silencio. Su docstring ya
exige que cada entrada explique **por qué la tabla no puede tener modelo** —
«todavía no lo tiene» no vale, eso es deriva—, pero esa exigencia no la
comprobaba nada. Una regla que nadie verifica dura lo que dura la memoria de
quien la escribió.

Las dos entradas de hoy son respaldos de migración (`*_backfill_*`): tablas que
guardan fila a fila lo que un backfill insertó, para que el `downgrade` retire
exactamente eso en vez de recalcularlo. Traen una **pareja** que no es
decorativa: la aplicación tiene el acceso REVOCADO, porque un respaldo legible
desde la app es un camino de lectura hacia datos que el diseño quiere cerrados
— y el arnés de integración reproduce ese revoke para no probar contra unos
permisos más laxos que los de producción.

Este fichero fija las dos mitades: que el motivo esté escrito de verdad, y que
la pareja no se caiga por un lado.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from api_server.db.autogenerate_policy import TABLES_WITHOUT_A_MODEL

pytestmark = pytest.mark.unit

_CONFTEST = Path(__file__).resolve().parents[1] / "integration" / "conftest.py"

# Un motivo de una línea («respaldo interno») no permite auditar la exclusión
# dentro de seis meses, que es justo para lo que está.
_MIN_MOTIVO = 120

# Marcas que delatan una exclusión provisional. El docstring del módulo las
# rechaza por su nombre: lo provisional es deriva y va al inventario del test de
# integración, que sí obliga a retirarlo cuando se arregla.
#
# Cada una se busca como PALABRA, no como subcadena, y ahí hay una lección que
# se cobró en el primer intento: buscar «TODO» sin distinguir mayúsculas lo
# encontraba dentro de «**todo** acceso» y declaraba provisionales los dos
# motivos que sí estaban bien escritos. El test estaba mal, no el motivo.
_PROVISIONAL_INSENSIBLE = ("todavía", "todavia", "aún", "aun no", "por ahora", "de momento")
_PROVISIONAL_SENSIBLE = ("TODO", "FIXME", "XXX")


def _marcas_provisionales(motivo: str) -> list[str]:
    encontradas = [
        marca
        for marca in _PROVISIONAL_INSENSIBLE
        if re.search(rf"\b{re.escape(marca)}\b", motivo, re.IGNORECASE)
    ]
    encontradas += [marca for marca in _PROVISIONAL_SENSIBLE if re.search(rf"\b{marca}\b", motivo)]
    return encontradas


def _app_revoked_tables() -> set[str]:
    """Lee `_APP_REVOKED_TABLES` del conftest de integración sin importarlo.

    Importarlo arrastraría el arnés entero (asyncpg, contenedores, fixtures de
    sesión) a la suite unitaria. El AST basta para leer una tupla de literales.
    """
    arbol = ast.parse(_CONFTEST.read_text(encoding="utf-8"))
    for nodo in ast.walk(arbol):
        objetivo = None
        if isinstance(nodo, ast.AnnAssign) and isinstance(nodo.target, ast.Name):
            objetivo = nodo.target.id
        elif (
            isinstance(nodo, ast.Assign)
            and len(nodo.targets) == 1
            and isinstance(nodo.targets[0], ast.Name)
        ):
            objetivo = nodo.targets[0].id
        if objetivo != "_APP_REVOKED_TABLES" or nodo.value is None:
            continue
        return {
            e.value
            for e in ast.walk(nodo.value)
            if isinstance(e, ast.Constant) and isinstance(e.value, str)
        }
    raise AssertionError("no se encontró `_APP_REVOKED_TABLES` en el conftest de integración")


def test_la_lectura_del_conftest_encuentra_algo() -> None:
    """Si el AST dejara de casar, los tests de abajo pasarían sobre un vacío."""
    assert len(_app_revoked_tables()) >= 2, (
        "la lectura de `_APP_REVOKED_TABLES` devolvió casi nada: probablemente "
        "cambió la forma de la declaración y esta comprobación dejó de mirar"
    )


@pytest.mark.parametrize("tabla", sorted(TABLES_WITHOUT_A_MODEL))
def test_cada_exclusion_lleva_un_motivo_auditable(tabla: str) -> None:
    motivo = TABLES_WITHOUT_A_MODEL[tabla]

    assert len(motivo) >= _MIN_MOTIVO, (
        f"la exclusión de {tabla!r} trae {len(motivo)} caracteres de motivo. "
        "Excluirla la saca de `alembic check` para siempre; el motivo tiene que "
        "poder auditarse sin arqueología"
    )

    flojas = _marcas_provisionales(motivo)
    assert not flojas, (
        f"el motivo de {tabla!r} suena provisional ({flojas}). Una tabla que "
        "«todavía» no tiene modelo es DERIVA: va al inventario de "
        "`tests/integration/test_alembic_autogenerate_clean.py`, que obliga a "
        "retirarla cuando se arregle. Aquí sólo caben las que NO PUEDEN tenerlo"
    )


@pytest.mark.parametrize("tabla", sorted(t for t in TABLES_WITHOUT_A_MODEL if "backfill" in t))
def test_un_respaldo_excluido_tiene_el_acceso_revocado(tabla: str) -> None:
    """La pareja, por el lado que importa para la seguridad.

    Excluir un respaldo del autogenerate y dejárselo legible a la aplicación es
    la peor de las dos mitades sueltas: la tabla desaparece de la comparación de
    esquema Y sigue siendo un camino de lectura abierto hacia lo que el backfill
    guardó.
    """
    assert tabla in _app_revoked_tables(), (
        f"{tabla!r} está excluida del autogenerate pero NO aparece en "
        "`_APP_REVOKED_TABLES`: o le falta el revoke, o el arnés de integración "
        "está probando con permisos más laxos que los de producción"
    )


def test_el_arnes_no_revoca_tablas_que_nadie_excluye() -> None:
    """Y por el otro lado: un revoke sin su exclusión delata una lista rancia.

    Si el arnés revoca una tabla que ya no está en `TABLES_WITHOUT_A_MODEL`, o
    bien la tabla se retiró y quedó la línea muerta, o bien alguien le dio
    modelo y el revoke ahora cierra un camino que la aplicación necesita.
    """
    huerfanas = sorted(_app_revoked_tables() - set(TABLES_WITHOUT_A_MODEL))
    assert not huerfanas, (
        f"el arnés revoca {huerfanas}, que ya no figuran entre las tablas sin "
        "modelo: o la línea quedó muerta, o la tabla tiene modelo y el revoke "
        "le está cerrando un camino legítimo"
    )
