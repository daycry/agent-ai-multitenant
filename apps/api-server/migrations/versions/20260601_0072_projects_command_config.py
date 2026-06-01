"""projects.allowed_commands + default_runtime_template (Plan 06.16 task_06_16_01).

The polyglot tool catalog needs two per-project config fields so an
operator can authorise STACK commands (``php``, ``composer``,
``vendor/bin/phpunit``, ``pest``, ``npm``, ``dotnet``…) and pin the
runtime template the ``run_*`` tools execute in:

  * ``allowed_commands`` — the **deny-by-default** allowlist of program
    *basenames* the ``shell_exec`` builtin may run (task_06_16_02 wires
    it to the runtime). ``TEXT[]`` with a ``'{}'`` server default so
    every existing project starts with an EMPTY allowlist (deny-all —
    ``shell_exec`` runs nothing until the operator authorises a binary).
    Array (not JSONB) because the semantics are membership-only, mirroring
    ``projects.default_kb_grants`` (migration 0027).
  * ``default_runtime_template`` — the stack's runtime template id
    (``php-phpunit``, ``node-jest``, …) the ``run_*`` tools resolve
    against (task_06_16_03). ``TEXT`` nullable; NULL = keep each tool's
    current default runtime (backward-compatible — no behaviour change
    for existing Python projects).

Both columns inherit the existing ``projects`` tenant RLS (no policy
change). Additive + backward-compatible: existing rows get ``'{}'`` /
NULL.

Single head before this migration is ``0071_model_prices_provider_id``;
this is ``0072_projects_command_config`` (kept ≤ 32 chars to fit
``alembic_version.version_num``). Fully reversible: ``downgrade`` drops
both columns, restoring 0071 exactly. Proven by
``tests/integration/test_project_command_config.py`` (up / down / up).

Revision ID: 0072_projects_command_config
Revises: 0071_model_prices_provider_id
Create Date: 2026-06-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0072_projects_command_config"
down_revision: str | Sequence[str] | None = "0071_model_prices_provider_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Deny-by-default allowlist of program basenames for shell_exec.
    # NOT NULL DEFAULT '{}' so existing projects start with an empty
    # allowlist (shell_exec runs nothing until a binary is authorised).
    op.add_column(
        "projects",
        sa.Column(
            "allowed_commands",
            sa.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
    )
    # Runtime template id the run_* tools resolve against; NULL = keep
    # each tool's current default (backward-compatible).
    op.add_column(
        "projects",
        sa.Column("default_runtime_template", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "default_runtime_template")
    op.drop_column("projects", "allowed_commands")
