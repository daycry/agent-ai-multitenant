"""Consistency guards for operator runbooks (``docs/06-runbooks/``).

A runbook is run against a **real Docker-Compose deployment**, not a
developer checkout. The trigger for these guards (Plan prod-01 task_13) was
the upgrade runbook telling operators to ``cd apps/api-server`` and run
``python -m alembic upgrade head`` — which silently assumes a Python env +
an editable project install that a production single-machine host does not
have. The schema must be applied by the stack's own one-shot ``migrations``
service instead (the builder prod-01 added to the installer compose
generator), so the upgrade uses the very image it just pulled.
"""

from __future__ import annotations

from pathlib import Path

import pytest

RUNBOOKS = Path(__file__).resolve().parents[2] / "docs" / "06-runbooks"
UPGRADE = RUNBOOKS / "03-system-upgrade.md"


@pytest.fixture(scope="module")
def upgrade_text() -> str:
    return UPGRADE.read_text(encoding="utf-8")


def test_upgrade_runbook_exists() -> None:
    assert UPGRADE.is_file(), f"missing runbook: {UPGRADE}"


def test_upgrade_does_not_run_alembic_from_a_checkout(upgrade_text: str) -> None:
    # `python -m alembic` and `cd <repo>/apps/api-server` only work where the
    # project is pip-installed — never on a production deploy host.
    assert "python -m alembic" not in upgrade_text, (
        "upgrade runbook still drives Alembic from a source checkout; "
        "use the one-shot `migrations` service instead"
    )
    assert (
        "apps/api-server" not in upgrade_text
    ), "upgrade runbook still points the operator at a source dir"


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
