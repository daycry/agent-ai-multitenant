"""The install pipeline runs Alembic migrations before seeding (Plan prod-01
task_12, finding deploy-6).

Before this, ``INSTALL_STEP_ORDER`` went generate_config → pull_images →
start_stack → bootstrap_vault → seed_tenant — with NO migration step, so
``seed_tenant`` ran against an empty schema. The ``run_migrations`` step closes
that gap; it must sit AFTER the stack is up (postgres reachable) and BEFORE the
tenant seed (which needs the schema).
"""

from __future__ import annotations

import pytest
from installer_backend.install import (
    INSTALL_STEP_ORDER,
    INSTALL_STEP_TITLES_EN,
    INSTALL_STEP_TITLES_ES,
    InstallStep,
    install_step_index,
)

pytestmark = pytest.mark.unit


def test_run_migrations_runs_after_the_stack_and_before_the_seed() -> None:
    assert InstallStep.RUN_MIGRATIONS in INSTALL_STEP_ORDER
    assert install_step_index(InstallStep.RUN_MIGRATIONS) > install_step_index(
        InstallStep.START_STACK
    ), "migrations need postgres up first"
    assert install_step_index(InstallStep.RUN_MIGRATIONS) < install_step_index(
        InstallStep.SEED_TENANT
    ), "the schema must exist before seeding the tenant"


def test_every_step_has_a_title_in_both_languages() -> None:
    # Adding a step without its UI titles would surface a blank progress row.
    for step in INSTALL_STEP_ORDER:
        assert step in INSTALL_STEP_TITLES_ES, f"missing ES title for {step}"
        assert step in INSTALL_STEP_TITLES_EN, f"missing EN title for {step}"
