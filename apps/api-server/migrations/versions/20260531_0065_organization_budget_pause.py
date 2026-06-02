"""organizations.tenant_paused_by_budget (Plan 11.1 Fase B, task_11_1_06).

Adds the **tenant-level** auto-pause flag to ``organizations`` — the peer of
the project-level ``projects.paused_by_budget`` column that already exists
since the domain-minimum migration (0002). When a tenant reaches 100% of its
tenant-wide budget for the active period the consumption evaluator
(``api_server.budgets.pause``) sets this flag, and the orchestrator's
execution-start path refuses to enqueue NEW runs for that tenant (active runs
keep going — the flag never kills them). A manual override clears it; a new
budget period auto-clears it (consumption for the fresh window is < 100%).

  - ``tenant_paused_by_budget``  boolean NOT NULL DEFAULT false — mirrors the
                                 ``projects.paused_by_budget`` shape exactly.

NOT NULL with a server_default of ``false`` so existing rows need no backfill
(every tenant starts un-paused). Single head before this migration is
``0064_budget_alert_states``; this is ``0065_organization_budget_pause``.
Fully reversible: ``downgrade`` drops the column, restoring 0064 exactly. The
plan-wide reversibility proof target is ``0040_sso_email_domains``.

Revision ID: 0065_organization_budget_pause
Revises: 0064_budget_alert_states
Create Date: 2026-05-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0065_organization_budget_pause"
down_revision: str | Sequence[str] | None = "0064_budget_alert_states"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "tenant_paused_by_budget",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("organizations", "tenant_paused_by_budget")
