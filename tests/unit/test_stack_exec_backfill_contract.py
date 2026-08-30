"""La lista de roles COPIADA en la migración 0145 no puede separarse del seed.

Ninguna migración de este repo importa código de la app (misma decisión que la
0133: el `upgrade` tiene que seguir corriendo dentro de seis meses aunque el
módulo se haya movido de sitio). El precio de esa decisión es una copia, y una
copia sin guarda es una divergencia con fecha de caducidad: el día que alguien
añada un rol a `ROLES_THAT_EXECUTE_TOOLCHAIN`, los agentes nuevos saldrán del
seed con `stack_exec` y las copias de tenant de ese mismo rol se quedarán atrás
— exactamente la asimetría que esta migración viene a cerrar.

Se anclan aquí, en unit, porque no hace falta base de datos para comprobarlo: el
dato nace en dos constantes de Python.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from api_server.seeds.builtin_role_capabilities import ROLES_THAT_EXECUTE_TOOLCHAIN

pytestmark = pytest.mark.unit

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "apps"
    / "api-server"
    / "migrations"
    / "versions"
    / "20260830_0145_stack_exec_for_builtin_forks.py"
)


def _load_migration() -> ModuleType:
    """Importa el fichero de migración por ruta (no es un paquete importable)."""
    spec = importlib.util.spec_from_file_location("_m0145", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_file_is_where_the_test_expects_it() -> None:
    """Si alguien la renombra, esta guarda tiene que caerse, no pasar vacía."""
    assert _MIGRATION.is_file(), f"no existe {_MIGRATION}"


def test_the_copied_role_list_mirrors_the_seed() -> None:
    module = _load_migration()
    assert set(module._EXECUTING_ROLES) == set(ROLES_THAT_EXECUTE_TOOLCHAIN), (
        "la lista de roles de la migración 0145 y ROLES_THAT_EXECUTE_TOOLCHAIN han "
        "divergido: las copias de tenant dejarían de recibir lo que el seed reparte"
    )


def test_the_role_list_reaches_the_sql_quoted() -> None:
    """El `IN (...)` se construye por interpolación; que no quede vacío ni suelto."""
    module = _load_migration()
    for role in ROLES_THAT_EXECUTE_TOOLCHAIN:
        assert f"'{role}'" in module._ROLE_LIST_SQL, f"{role} no llega al SQL"


def test_the_revision_chain_is_declared() -> None:
    module = _load_migration()
    assert module.revision == "0145_stack_exec_builtin_forks"
    assert module.down_revision == "0144_timestamps_not_null"
