"""Guarda: ningún worker vuelve a abrir su propio engine (`task_audit14_06`).

El plan de remediación pide «prohibir imports directos de `create_async_engine`
fuera del módulo y tests». Esta es esa prohibición, y a día de hoy es un **muro**:
:data:`PENDING_MIGRATION` está vacía, así que cualquier `create_async_engine`
bajo `apps/workers/` que no esté en la factoría pone el test rojo.

Nació como **ratchet** porque los 13 módulos de `workers/maintenance/` los llevaba
otro carril: entraron en la lista uno a uno y el ratchet mordía por los dos lados
—un infractor nuevo fuera de la lista, rojo; y un módulo de la lista que ya NO
infringe, **también rojo**, exigiendo borrarlo—. Funcionó: ese carril los migró y
vació la lista el mismo día. El mecanismo se conserva porque la próxima excepción
tendrá que pasar por él, pero con la lista vacía
`test_pending_migration_list_has_no_stale_entries` no puede fallar: hoy no es una
guarda, es el candado de una puerta que nadie ha vuelto a abrir.

Lo que sí muerde siempre:

- :func:`test_no_worker_builds_its_own_engine` — la prohibición en sí.
- :func:`test_the_guard_actually_found_migrated_modules` — la aserción de «encontré
  algo». Un test de inventario que deja de encontrar nada pasa vacío y envejece
  sin avisar (apartado 4 de
  `docs/03-guides/verificar-antes-de-implementar.md`); si alguien renombra
  `workers.db` o rompe el descubrimiento de ficheros, esto se pone rojo.
- :func:`test_every_factory_helper_has_real_callers` — que ningún helper de la
  factoría se quede sin llamantes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_WORKERS_SRC = Path(__file__).resolve().parents[2] / "apps" / "workers" / "src" / "workers"

#: El único módulo autorizado a construir engines.
_FACTORY = "db.py"

#: Módulos que aún abren su engine a mano. **VACÍO desde prod-13 task_prod13_08**:
#: los 13 de `workers/maintenance/` —el carril que faltaba— pasaron a
#: `worker_engine(settings)`. El ratchet ya no es ratchet, es muro: cualquier
#: `create_async_engine` fuera de la factoría pone el test rojo. Si vuelve a hacer
#: falta una excepción, se añade aquí Y se justifica; una lista vacía que nadie
#: puede rellenar sin explicarse es la postura correcta.
PENDING_MIGRATION: frozenset[str] = frozenset()

#: Mínimo de módulos que deben usar la factoría. El 2026-07-30 eran 42 (29 del
#: carril de remediación + 13 de `maintenance/`); se deja
#: margen para que un refactor legítimo que fusione módulos no ponga esto rojo,
#: pero no tanto como para que la guarda pase con dos.
_MIN_MIGRATED = 20

_ENGINE_CALL = re.compile(r"\bcreate_async_engine\s*\(")
_FACTORY_USE = re.compile(r"\bfrom workers\.db import\b|\bworkers\.db\b")


def _module_files() -> list[Path]:
    return sorted(p for p in _WORKERS_SRC.rglob("*.py") if "__pycache__" not in p.parts)


def _rel(path: Path) -> str:
    return path.relative_to(_WORKERS_SRC).as_posix()


def test_discovery_finds_the_workers_package() -> None:
    """Si el descubrimiento se rompe, todo lo demás pasaría vacío."""
    files = _module_files()
    assert len(files) >= 40, f"¿ruta mal? sólo {len(files)} módulos bajo {_WORKERS_SRC}"
    assert (_WORKERS_SRC / _FACTORY).is_file(), "workers/db.py, la factoría, no está"


def test_no_worker_builds_its_own_engine() -> None:
    offenders = sorted(
        _rel(p)
        for p in _module_files()
        if _rel(p) != _FACTORY and _ENGINE_CALL.search(p.read_text(encoding="utf-8"))
    )
    unexpected = [o for o in offenders if o not in PENDING_MIGRATION]
    assert not unexpected, (
        "estos módulos abren su propio engine en vez de usar `workers.db`: "
        f"{unexpected}. La factoría es `worker_sessionmaker(settings)`."
    )


def test_pending_migration_list_has_no_stale_entries() -> None:
    """El otro lado del ratchet: migrar un módulo obliga a sacarlo de la lista."""
    still_offending = {
        _rel(p) for p in _module_files() if _ENGINE_CALL.search(p.read_text(encoding="utf-8"))
    }
    stale = sorted(PENDING_MIGRATION - still_offending)
    assert not stale, (
        "estos módulos ya NO abren engine a mano: bórralos de PENDING_MIGRATION "
        f"para que la excepción no se fosilice → {stale}"
    )


def test_the_guard_actually_found_migrated_modules() -> None:
    """Aserción de «encontré algo»: sin esto el test pasaría vacío el día que el
    descubrimiento deje de funcionar."""
    migrated = sorted(
        _rel(p)
        for p in _module_files()
        if _rel(p) != _FACTORY and _FACTORY_USE.search(p.read_text(encoding="utf-8"))
    )
    assert len(migrated) >= _MIN_MIGRATED, (
        f"la guarda dejó de encontrar los llamantes de la factoría (vio {len(migrated)}: "
        f"{migrated})"
    )


@pytest.mark.parametrize(("helper", "minimum"), [("worker_sessionmaker", 2), ("worker_session", 1)])
def test_every_factory_helper_has_real_callers(helper: str, minimum: int) -> None:
    """El patrón dominante de esta base es «mecanismo entregado, cero llamantes»
    (apartado 5 de `docs/03-guides/verificar-antes-de-implementar.md`). Si un
    helper de la factoría se queda sin usar, sobra: o se cablea o se borra."""
    pattern = re.compile(rf"\b{helper}\s*\(")
    callers = sorted(
        _rel(p)
        for p in _module_files()
        if _rel(p) != _FACTORY and pattern.search(p.read_text(encoding="utf-8"))
    )
    assert len(callers) >= minimum, (
        f"`{helper}` tiene {len(callers)} llamantes en producción ({callers}); "
        "un helper sin llamantes se borra, no se conserva"
    )


def test_factory_pins_nullpool() -> None:
    """El contenido del contrato, no sólo quién lo llama: si alguien quita el
    `NullPool` de la factoría, los 42 módulos vuelven al QueuePool de golpe."""
    source = (_WORKERS_SRC / _FACTORY).read_text(encoding="utf-8")
    assert "poolclass=NullPool" in source
