"""organizations.personal_assistant_enabled toggle (Plan 10 task_10_14).

Per-tenant gate for the conversational personal assistant. The assistant
is accessible ONLY to Tenant Admins, and only when this toggle is ON. It
DEFAULTS to ``false`` (server_default) so the feature is opt-in: a tenant
that has never touched it has the assistant disabled and every Tenant
Admin of that tenant is denied (403) until an admin flips it on.

The tenant-level assistant *identity* (name, avatar, tone, language,
system_prompt override, enabled-tools list) is NOT a column here — it is
stored as a single JSONB blob in the existing ``tenant_settings`` table
under the ``assistant`` category (no schema change, evolves freely). This
migration only adds the boolean gate, which deserves a first-class column
because it is read on the hot path of every assistant request.

Fully reversible: ``downgrade`` drops the column.

Revision ID: 0047_personal_assistant_enabled
Revises: 0046_notification_templates
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0047_personal_assistant_enabled"
down_revision: str | Sequence[str] | None = "0046_notification_templates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "personal_assistant_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("organizations", "personal_assistant_enabled")
