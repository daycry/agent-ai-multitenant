"""La red de seguridad del arnés de integración, comprobada sin base de datos.

`tests/integration/conftest.py` deja tres tablas PLATFORM-GLOBAL
(`platform_settings`, `model_prices`, `llm_providers`) en estado conocido al
empezar cada fichero, y purga la caché Redis de ajustes al empezar cada test.
Existe porque esas tres tablas no llevan `tenant_id`, la suite comparte UNA base
de datos de sesión y CI reparte los ficheros entre cuatro shards por round-robin:
lo que un fichero deja escrito ahí lo lee el siguiente, y el orden cambia con
sólo añadir un test en cualquier parte del árbol.

Este fichero comprueba las TRES premisas de las que depende ese arreglo, y lo
hace en `tests/unit/` a propósito: son estáticas y tienen que poder correr sin
PostgreSQL. Si alguna se cae, el arreglo sigue en su sitio pero ya no hace lo
que dice.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_RAIZ = Path(__file__).resolve().parents[2]
_CONFTEST = _RAIZ / "tests" / "integration" / "conftest.py"

#: Las tres tablas sin `tenant_id` que el arnés rebaja a estado conocido.
_TABLAS_GLOBALES = ("platform_settings", "model_prices", "llm_providers")


def _arbol() -> ast.Module:
    return ast.parse(_CONFTEST.read_text(encoding="utf-8"))


def _fixtures() -> dict[str, ast.FunctionDef]:
    """Las funciones de `conftest.py` decoradas con `@pytest.fixture`."""
    encontradas: dict[str, ast.FunctionDef] = {}
    for nodo in ast.walk(_arbol()):
        if not isinstance(nodo, ast.FunctionDef):
            continue
        for deco in nodo.decorator_list:
            objetivo = deco.func if isinstance(deco, ast.Call) else deco
            if isinstance(objetivo, ast.Attribute) and objetivo.attr == "fixture":
                encontradas[nodo.name] = nodo
    return encontradas


def _kwargs_del_decorador(fn: ast.FunctionDef) -> dict[str, ast.expr]:
    for deco in fn.decorator_list:
        if isinstance(deco, ast.Call):
            return {kw.arg: kw.value for kw in deco.keywords if kw.arg}
    return {}


# ===========================================================================
# 1. Las dos fixtures siguen siendo automáticas, y con la granularidad elegida
# ===========================================================================
def test_the_baseline_fixtures_are_still_autouse_with_their_scopes() -> None:
    """Sin `autouse`, nadie las pide y el arreglo desaparece sin dejar rojo.

    Los scopes tampoco son decorativos: las FILAS se limpian por MÓDULO (la fuga
    que no se ve es la que cruza ficheros, y truncar por test rompería a quien
    siembre un proveedor en un test y lo lea en el siguiente), y la CACHÉ por
    TEST (muerde entre tests del mismo fichero, y purgarla cuesta un DEL).
    """
    fixtures = _fixtures()
    esperado = {
        "_global_tables_baseline": "module",
        "_platform_setting_cache_baseline": "function",
    }
    for nombre, scope in esperado.items():
        assert nombre in fixtures, (
            f"`{nombre}` ya no existe en {_CONFTEST.name}. Si se renombró, actualiza"
            " este test; si se borró, la suite volvió a depender de que cada fichero"
            " se acuerde de limpiar el estado global."
        )
        kwargs = _kwargs_del_decorador(fixtures[nombre])
        autouse = kwargs.get("autouse")
        assert isinstance(autouse, ast.Constant) and autouse.value is True, (
            f"`{nombre}` ya no es `autouse=True`: nadie la pide, así que no corre."
        )
        declarado = kwargs.get("scope")
        real = declarado.value if isinstance(declarado, ast.Constant) else "function"
        assert real == scope, f"`{nombre}` pasó de scope `{scope}` a `{real}`"


# ===========================================================================
# 2. La lista de fixtures «esto habla con PostgreSQL» está completa
# ===========================================================================
def test_every_database_fixture_is_declared_in_the_db_fixture_list() -> None:
    """`_DB_FIXTURES` decide qué módulos se rebajan a estado conocido.

    Se deja fuera a los ficheros que NO tocan la BD (los de Docker puro:
    `test_egress_proxy`, `test_container_isolation`, `test_no_docker_socket`…),
    que hoy corren sin PostgreSQL levantado y tienen que seguir haciéndolo. El
    modo de fallo de esa decisión es que alguien añada una fixture de BD nueva y
    la lista se quede corta: los módulos que sólo pidan ESA fixture volverían a
    heredar el estado del fichero anterior, en silencio.
    """
    from tests.integration.conftest import _DB_FIXTURES

    # Una fixture habla con PostgreSQL si su cuerpo nombra las constantes de
    # conexión del arnés, o depende de otra que lo haga.
    marcas = ("PG_", "_admin_dsn", "test_database_url", "alembic_config", "DATABASE_URL")
    hablan_con_pg = {
        nombre
        for nombre, fn in _fixtures().items()
        if any(marca in ast.unparse(fn) for marca in marcas)
    }

    assert len(hablan_con_pg) >= 6, (
        "el descubrimiento dejó de encontrar las fixtures de BD del conftest"
        f" (vio {len(hablan_con_pg)}: {sorted(hablan_con_pg)}). Sin esta aserción,"
        " el test de abajo pasaría en vacío."
    )
    faltan = hablan_con_pg - set(_DB_FIXTURES) - {"_global_tables_baseline"}
    assert not faltan, (
        f"estas fixtures de {_CONFTEST.name} hablan con PostgreSQL y no están en"
        " `_DB_FIXTURES`: "
        f"{sorted(faltan)}. Un módulo que pida sólo una de ellas NO recibiría el"
        " estado conocido, y volvería a leer lo que dejó el fichero anterior de"
        " su shard."
    )


# ===========================================================================
# 3. «Vacías» ES el estado tras migrar — el arnés no borra la semilla de nadie
# ===========================================================================
def test_no_migration_or_startup_seed_writes_the_global_tables() -> None:
    """La premisa que hace que truncar sea seguro.

    `_reset_global_tables` deja las tres tablas VACÍAS. Eso sólo es «estado
    conocido» y no «borrar de más» mientras nadie las siembre por debajo: ni una
    migración de Alembic ni un seed de arranque. El día que alguien añada un
    catálogo de proveedores por defecto, este test cae y hay que decidir —seed
    reproducido en la fixture, o tabla fuera de la lista—, en vez de descubrirlo
    como un rojo lejano en otro fichero.
    """
    migraciones = sorted((_RAIZ / "apps/api-server/migrations/versions").glob("*.py"))
    seeds = sorted((_RAIZ / "apps/api-server/src/api_server/seeds").glob("*.py"))
    assert len(migraciones) >= 100, (
        f"esperaba el corpus de migraciones, encontré {len(migraciones)} ficheros"
    )
    assert len(seeds) >= 5, f"esperaba los seeds de arranque, encontré {len(seeds)}"

    escritores: list[str] = []
    for fichero in [*migraciones, *seeds]:
        texto = fichero.read_text(encoding="utf-8", errors="replace")
        for tabla in _TABLAS_GLOBALES:
            if re.search(rf'INSERT\s+INTO\s+"?{tabla}\b', texto, re.I):
                escritores.append(f"{fichero.name} -> {tabla}")
            if re.search(rf"bulk_insert\s*\(\s*{tabla}\b", texto, re.I):
                escritores.append(f"{fichero.name} -> {tabla} (bulk_insert)")

    assert not escritores, (
        "algo siembra las tablas platform-global por debajo del arnés, así que"
        " dejarlas vacías SÍ borra datos que otros tests dan por hechos:"
        f" {escritores}"
    )
