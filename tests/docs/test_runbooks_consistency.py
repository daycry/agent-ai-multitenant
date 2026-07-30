"""Consistency guards for operator runbooks (``docs/06-runbooks/``).

A runbook is run against a **real Docker-Compose deployment**, not a
developer checkout. The trigger for these guards (Plan prod-01 task_13) was
the upgrade runbook telling operators to ``cd apps/api-server`` and run
``python -m alembic upgrade head`` — which silently assumes a Python env +
an editable project install that a production single-machine host does not
have. The schema must be applied by the stack's own one-shot ``migrations``
service instead (the builder prod-01 added to the installer compose
generator), so the upgrade uses the very image it just pulled.

La guarda del checkout está **acotada al camino de producción**. Nació como un
`substring` sobre el fichero entero, y el 2026-07-28 el runbook ganó una
subsección `Dev / manuals` con el atajo por el venv del repo — legítimo en dev,
donde sí hay `.venv`. Eso dejó el test en rojo sin que nadie lo viera, porque
``tests/docs/`` no estaba en la lista de verificación local de CONTINUE_HERE.
Ahora el invariante se afirma sobre el runbook MENOS ese bloque rotulado, y un
segundo test exige que el rótulo siga ahí: la excepción no se puede ensanchar
borrando la etiqueta que la delimita.
"""

from __future__ import annotations

from pathlib import Path

import pytest

RUNBOOKS = Path(__file__).resolve().parents[2] / "docs" / "06-runbooks"
UPGRADE = RUNBOOKS / "03-system-upgrade.md"

# El runbook tiene una subsección explícita para dev/manuals, donde el atajo por
# el venv del repo SÍ es legítimo (lo documentó el despliegue del 2026-07-28: es
# la vía corta cuando ya tienes el .venv montado). El invariante de prod-01 es
# sobre el camino de PRODUCCIÓN, no sobre el fichero entero, así que la guarda
# recorta ese bloque y afirma sobre el resto.
DEV_MARKER = "**Dev / manuals"

# Formas de arrancar Alembic desde un checkout. `python.exe -m alembic` se colaba
# por el resquicio de la guarda anterior, que sólo buscaba `python -m alembic`.
CHECKOUT_ALEMBIC = ("python -m alembic", "python.exe -m alembic", "apps/api-server")


@pytest.fixture(scope="module")
def upgrade_text() -> str:
    return UPGRADE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def upgrade_production_guidance(upgrade_text: str) -> str:
    """El runbook sin el bloque de código de la subsección dev/manuals.

    Si el marcador desaparece, esto revienta a propósito: nadie puede ampliar
    la excepción borrando la etiqueta que la delimita.
    """
    start = upgrade_text.index(DEV_MARKER)
    fence_open = upgrade_text.index("```", start)
    fence_close = upgrade_text.index("```", fence_open + 3)
    return upgrade_text[:start] + upgrade_text[fence_close + 3 :]


def test_upgrade_runbook_exists() -> None:
    assert UPGRADE.is_file(), f"missing runbook: {UPGRADE}"


def test_upgrade_does_not_run_alembic_from_a_checkout(upgrade_production_guidance: str) -> None:
    # `python -m alembic` y `cd <repo>/apps/api-server` sólo funcionan donde el
    # proyecto está pip-installed — nunca en un host de despliegue de producción.
    for needle in CHECKOUT_ALEMBIC:
        assert needle not in upgrade_production_guidance, (
            f"el camino de producción del runbook usa {needle!r}: arranca Alembic "
            "desde un checkout en vez del servicio one-shot `migrations`"
        )


def test_upgrade_checkout_shortcut_stays_fenced_in_the_dev_subsection(upgrade_text: str) -> None:
    # El atajo por el venv puede existir, pero SÓLO dentro del bloque rotulado
    # dev/manuals. Sin este test, la excepción de arriba se podría ensanchar
    # hasta tragarse el camino del operador.
    assert DEV_MARKER in upgrade_text, (
        "falta la subsección dev/manuals: el atajo por checkout se queda sin "
        "etiqueta que lo acote al entorno de desarrollo"
    )
    dev_block = upgrade_text[upgrade_text.index(DEV_MARKER) :]
    fence_open = dev_block.index("```")
    dev_code = dev_block[fence_open : dev_block.index("```", fence_open + 3)]
    assert "apps/api-server" in dev_code, (
        "el atajo por checkout ya no está en el bloque dev/manuals; si se ha "
        "movido, actualiza esta guarda en vez de dejarla mirando a un sitio vacío"
    )


def test_upgrade_applies_schema_via_oneshot_migrations_service(upgrade_text: str) -> None:
    # The schema is applied by the stack's one-shot `migrations` service,
    # launched as a one-off that brings postgres up as its dependency.
    assert (
        "run --rm migrations" in upgrade_text
    ), "upgrade runbook must apply migrations via `docker compose run --rm migrations`"


def test_upgrade_steps_are_ordered_backup_pull_migrate_up(upgrade_text: str) -> None:
    # Order is load-bearing: a verified backup must exist before any schema
    # change, and migrations must complete before the app services come up.
    backup = upgrade_text.index("### 1. Backup pre-upgrade")
    pull = upgrade_text.index("### 2. Trae el código")
    migrate = upgrade_text.index("### 4. Migraciones de esquema")
    up = upgrade_text.index("### 5. Levanta el stack")
    assert backup < pull < migrate < up, "upgrade steps are out of order"
