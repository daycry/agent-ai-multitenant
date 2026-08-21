"""prod-13 · task_prod13_14 — el ALCANCE de la purga es una decisión escrita.

La purga física es irreversible, así que lo que NO purga importa tanto como lo
que purga. Este módulo no toca la base de datos: fija la propiedad estructural
que hace que el alcance no crezca (ni encoja) por accidente.

El modo de fallo que evita: alguien añade `deleted_at` a una tabla nueva y la
purga —o pasa a borrarla sin que nadie lo haya decidido, o la ignora para
siempre sin que nadie se entere. La guarda obliga a clasificarla, igual que
`_KNOWN_UNREGISTERED_BEAT_TASKS` obliga a mirar las entradas de beat huérfanas.
"""

from __future__ import annotations

import contextlib
import importlib
import pkgutil

import pytest

pytestmark = pytest.mark.unit


def _soft_delete_tables() -> set[str]:
    """Todas las tablas del modelo con columna ``deleted_at``.

    Importa TODOS los módulos de ``api_server.db``: el agregador ``models.py``
    solo trae un subconjunto, y medir sobre él daría un universo artificialmente
    pequeño — la guarda pasaría vacuamente para las tablas que no importa.
    """
    import api_server.db as dbpkg

    for module in pkgutil.iter_modules(dbpkg.__path__):
        # Un módulo opcional que no importe no invalida la guarda: lo que
        # importa es que el universo medido no se quede corto, y para eso el
        # test tiene su propia cota inferior.
        with contextlib.suppress(Exception):
            importlib.import_module(f"api_server.db.{module.name}")
    from api_server.db.base import Base

    return {name for name, table in Base.metadata.tables.items() if "deleted_at" in table.c}


def test_every_soft_delete_table_is_classified() -> None:
    from workers.maintenance.purge import EXCLUDED_SOFT_DELETE_TABLES, PURGABLE_ROOTS

    universe = _soft_delete_tables()
    assert len(universe) >= 30, f"la guarda dejó de ver el modelo (vio {len(universe)})"

    classified = set(PURGABLE_ROOTS) | set(EXCLUDED_SOFT_DELETE_TABLES)
    unclassified = sorted(universe - classified)
    message = (
        f"tablas con `deleted_at` que la purga no clasifica: {unclassified}. "
        "Decide si se purgan (PURGABLE_ROOTS) o no (EXCLUDED_SOFT_DELETE_TABLES, "
        "con el motivo escrito). Dejarlas fuera en silencio es acumular disco "
        "que nadie reclama."
    )
    assert not unclassified, message


def test_the_exclusions_carry_a_written_reason() -> None:
    """Una exclusión sin motivo es un olvido disfrazado de decisión."""
    from workers.maintenance.purge import EXCLUDED_SOFT_DELETE_TABLES

    thin = sorted(
        table for table, reason in EXCLUDED_SOFT_DELETE_TABLES.items() if len(reason.strip()) < 30
    )
    assert not thin, f"exclusiones sin justificación suficiente: {thin}"


def test_tenants_and_users_are_never_purged() -> None:
    """Las dos que más caro salen si se cuelan.

    Borrar físicamente una `organizations` es dar de baja un tenant entero —una
    operación de negocio con su propio procedimiento— y `users` es global (ADR
    0137): un usuario soft-borrado puede seguir siendo miembro de otro tenant.
    """
    from workers.maintenance.purge import PURGABLE_ROOTS

    assert "organizations" not in PURGABLE_ROOTS
    assert "users" not in PURGABLE_ROOTS


def test_the_beat_entry_exists_and_is_wired() -> None:
    """El cableado. Un job de mantenimiento sin entrada de beat no corre nunca."""
    from workers.beat_schedule import BEAT_SCHEDULE, PURGE_SOFT_DELETED_BEAT_ENTRY

    entry = BEAT_SCHEDULE[PURGE_SOFT_DELETED_BEAT_ENTRY]
    assert entry["task"] == "workers.purge_soft_deleted"
    options = entry["options"]
    assert isinstance(options, dict)
    # `ingestion` como el GC de conocimiento: es donde vive el cliente de MinIO.
    assert options["queue"] == "ingestion"


def test_the_task_is_exported_by_the_maintenance_package() -> None:
    """El façade `workers.maintenance` es lo que Celery importa en el boot; una
    task que no cuelgue de ahí queda sin registrar y beat la encola contra
    `NotRegistered` — la trampa de
    `gotchas/beat-entry-whose-task-nobody-imports.md`."""
    import workers.maintenance as pkg

    assert "purge_soft_deleted_task" in pkg.__all__
    assert hasattr(pkg, "purge_soft_deleted_task")
