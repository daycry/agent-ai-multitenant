"""La lista de roles COPIADA en la migración 0146 no puede separarse del seed.

Mismo contrato que `test_stack_exec_backfill_contract.py` y por la misma razón:
ninguna migración de este repo importa código de la app (decisión de la 0133 —
el `upgrade` tiene que seguir corriendo dentro de seis meses aunque el módulo se
haya movido de sitio). El precio de esa decisión es una copia, y una copia sin
guarda es una divergencia con fecha de caducidad.

Aquí hay DOS copias que vigilar, no una, y la segunda es la que de verdad
sostiene el argumento de seguridad de la migración:

* **Los roles que escriben ficheros.** El día que alguien añada un rol a
  `ROLE_DEFAULT_TOOLS` con las tools de escritura, sus agentes nuevos saldrán del
  seed con `move-file` y las copias de tenant de ese mismo rol se quedarán atrás
  — la asimetría que esta migración viene a cerrar.
* **La pareja que define la población.** La 0146 sólo toca a quien YA tiene
  concedidas TODAS las demás puertas de escritura de ficheros, porque sobre esa
  población mover no concede autoridad nueva. Si mañana aparece una cuarta puerta
  en `FILE_WRITING_TOOLS`, la pareja copiada deja de ser «todas las demás» y el
  argumento se rompe en silencio. Esta guarda lo convierte en rojo.

Se anclan en unit porque no hace falta base de datos: el dato nace en dos
constantes de Python.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from uuid import UUID, uuid5

import pytest
from api_server.seeds import PLATFORM_TENANT_ID, TOOL_SEED_NAMESPACE
from api_server.seeds.builtin_role_capabilities import (
    FILE_WRITING_TOOLS,
    ROLE_DEFAULT_TOOLS,
    ROLES_WITH_READ_ONLY_WORKSPACE,
)
from api_server.seeds.builtin_tools import BUILTIN_TOOLS

pytestmark = pytest.mark.unit

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "apps"
    / "api-server"
    / "migrations"
    / "versions"
    / "20260831_0146_move_file_for_builtin_forks.py"
)

#: El slug que la migración reparte. El catálogo usa guiones para el slug y
#: guiones bajos para `tools.name`; la migración consulta por `name`.
_MOVE_SLUG = "move-file"


def _load_migration() -> ModuleType:
    """Importa el fichero de migración por ruta (no es un paquete importable)."""
    spec = importlib.util.spec_from_file_location("_m0146", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _roles_that_the_seed_gives_move_file() -> set[str]:
    """Los roles a los que el mapa por rol reparte `move-file` hoy."""
    return {role for role, tools in ROLE_DEFAULT_TOOLS.items() if _MOVE_SLUG in tools}


def test_the_migration_file_is_where_the_test_expects_it() -> None:
    """Si alguien la renombra, esta guarda tiene que caerse, no pasar vacía."""
    assert _MIGRATION.is_file(), f"no existe {_MIGRATION}"


def test_the_seed_actually_hands_move_file_to_somebody() -> None:
    """No-vacuidad: sin esto, las dos guardas de abajo compararían conjuntos vacíos."""
    assert len(_roles_that_the_seed_gives_move_file()) >= 2, (
        "el mapa por rol no reparte `move-file` a casi nadie: o el catálogo "
        "cambió de slug, o esta comprobación dejó de mirar lo que cree"
    )


def test_the_copied_role_list_mirrors_the_seed() -> None:
    module = _load_migration()
    assert set(module._WRITING_ROLES) == _roles_that_the_seed_gives_move_file(), (
        "la lista de roles de la migración 0146 y los roles que `ROLE_DEFAULT_TOOLS` "
        "surte con `move-file` han divergido: las copias de tenant dejarían de "
        "recibir lo que el seed reparte"
    )


def test_the_reviewer_is_out_and_the_reason_still_holds() -> None:
    """El reviewer queda fuera por el ADR 0095, no por casualidad.

    Se afirma sobre las DOS mitades: que la migración no lo lista, y que el
    motivo por el que no lo lista —su workspace es de sólo lectura— sigue siendo
    verdad en el módulo que lo decide. Si mañana alguien le devolviera escritura
    al reviewer, esta guarda obliga a volver aquí en vez de dejar una exclusión
    huérfana cuyo porqué ya no existe.
    """
    module = _load_migration()
    assert "reviewer" not in module._WRITING_ROLES
    assert "reviewer" in ROLES_WITH_READ_ONLY_WORKSPACE


def test_the_population_is_every_other_file_writing_door() -> None:
    """La condición de seguridad, escrita como dato y no como prosa.

    Mover no concede autoridad nueva SÓLO sobre quien ya tiene concedidas todas
    las demás puertas de escritura de ficheros: `write_file` pone bytes en una
    ruta nueva, `delete_file` retira un árbol de su sitio, y mover es exactamente
    esas dos cosas en un paso. A quien le falte una de las dos, mover le regala
    la mitad que no tiene.

    Si aparece una cuarta puerta en `FILE_WRITING_TOOLS`, la pareja copiada deja
    de ser «todas las demás» y el argumento se cae. Mejor rojo que en silencio.
    """
    module = _load_migration()
    esperada = {tool.replace("-", "_") for tool in FILE_WRITING_TOOLS if tool != _MOVE_SLUG}
    assert set(module._REQUIRED_TOOLS) == esperada, (
        "la población de la migración 0146 ya no es «quien tiene todas las demás "
        f"puertas de escritura»: exige {sorted(module._REQUIRED_TOOLS)} y hoy las "
        f"demás son {sorted(esperada)}"
    )


def test_the_required_pair_reaches_the_sql_as_a_conjunction() -> None:
    """Exigir la pareja es un AND, y la migración lo construye por interpolación.

    Un `OR` accidental (o un `IN (...)`) convertiría «tiene las dos» en «tiene
    alguna», que es justo la población a la que mover SÍ concede autoridad nueva.
    Se comprueba contando: una sub-consulta `EXISTS` por cada tool exigida.
    """
    module = _load_migration()
    for tool in module._REQUIRED_TOOLS:
        assert module._REQUIRED_TOOLS_SQL.count(f"'{tool}'") == 1, (
            f"{tool} no llega al SQL exactamente una vez"
        )
    assert module._REQUIRED_TOOLS_SQL.count("EXISTS") == len(module._REQUIRED_TOOLS), (
        "cada tool exigida necesita su propio EXISTS: con uno solo, la condición "
        "pasa de «tiene las dos» a «tiene alguna»"
    )


def test_the_role_list_reaches_the_sql_quoted() -> None:
    """El `IN (...)` se construye por interpolación; que no quede vacío ni suelto."""
    module = _load_migration()
    for role in _roles_that_the_seed_gives_move_file():
        assert f"'{role}'" in module._ROLE_LIST_SQL, f"{role} no llega al SQL"


def test_the_hardcoded_tool_id_is_the_one_the_seed_will_upsert_onto() -> None:
    """La copia que de verdad puede partir el arreglo en dos.

    El `upgrade` crea la fila de `move_file` si el seed aún no ha corrido —tiene
    que hacerlo: el one-shot `migrations` va ANTES del arranque del api-server, y
    esta tool nació el mismo día que la migración—, y para eso lleva el `id`
    escrito literal. `seed_builtin_tools` hace `ON CONFLICT (id) DO UPDATE`, así
    que si los dos ids dejaran de coincidir el seed insertaría una fila `move_file`
    GEMELA: los grants que reparte esta migración colgarían de la fila que el
    catálogo no reconoce, y encima la segunda inserción chocaría con el índice
    único parcial `uq_tools_tenant_name`.

    Se recalcula desde las constantes del seed (`uuid5(TOOL_SEED_NAMESPACE,
    "tool:move-file")`) en vez de comparar contra otro literal, para que cambiar
    el namespace o el slug también dispare esta guarda.
    """
    module = _load_migration()
    esperado = uuid5(TOOL_SEED_NAMESPACE, f"tool:{_MOVE_SLUG}")
    assert UUID(module._MOVE_TOOL_ID) == esperado, (
        f"la migración 0146 crea la tool con el id {module._MOVE_TOOL_ID} y el "
        f"seed la sembrará con {esperado}: acabarían siendo dos filas distintas"
    )
    assert UUID(module._PLATFORM_TENANT_ID) == PLATFORM_TENANT_ID


def test_the_tool_name_matches_the_catalog() -> None:
    """`tools.name` es por lo que busca el `upgrade`; el slug sólo deriva el id.

    Los valores de la fila mínima (categoría, nivel de seguridad, tipo) se
    comprueban contra el catálogo LEYENDO LA FILA CREADA, en
    `test_the_row_it_creates_is_the_one_the_seed_will_recognise`: aquí sólo vive
    el nombre, que es la clave de búsqueda de todo el SQL.
    """
    module = _load_migration()
    catalogo = {tool.slug: tool for tool in BUILTIN_TOOLS}
    assert _MOVE_SLUG in catalogo, "el catálogo ya no trae `move-file`"
    assert catalogo[_MOVE_SLUG].name == module._MOVE_TOOL_NAME


def test_the_revision_chain_is_declared() -> None:
    module = _load_migration()
    assert module.revision == "0146_move_file_builtin_forks"
    assert module.down_revision == "0145_stack_exec_builtin_forks"
