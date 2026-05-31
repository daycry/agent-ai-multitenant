"""agents.agent_type enum value-set CHECK (Plan 16 Fase A, task_16_01).

The ``agents.agent_type`` column already ships with the domain-minimum
migration (0002) as ``String(16) NOT NULL DEFAULT 'ai'`` — every existing
agent row is therefore already ``agent_type='ai'`` (no behaviour change for
AI agents). What was missing is DB-level enforcement of the AgentType value
set: the plain ``String(16)`` accepts any text. This migration adds a CHECK
constraint so the database enforces ``agent_type IN ('ai', 'human')`` — the
:class:`AgentType` StrEnum value set (Plan 16 Decisiones Clave: ``agent_type``
extends the EXISTING Agent entity rather than a separate table).

  - ``ck_agents_agent_type``  CHECK (agent_type IN ('ai', 'human')) — mirrors
                              the model's ``__table_args__`` CheckConstraint of
                              the same name, the same shape as the existing
                              ``ck_agents_scope_project_consistency`` (0003).

No backfill is needed: existing rows are 'ai' and already satisfy the
constraint. Single head before this migration is
``0065_organization_budget_pause``; this is ``0066_agent_type_check``. Fully
reversible: ``downgrade`` drops the constraint, restoring 0065 exactly. The
plan-wide reversibility proof target is ``0040_sso_email_domains``.

Revision ID: 0066_agent_type_check
Revises: 0065_organization_budget_pause
Create Date: 2026-05-31
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0066_agent_type_check"
down_revision: str | Sequence[str] | None = "0065_organization_budget_pause"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_agents_agent_type",
        "agents",
        "agent_type IN ('ai', 'human')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_agents_agent_type", "agents", type_="check")
