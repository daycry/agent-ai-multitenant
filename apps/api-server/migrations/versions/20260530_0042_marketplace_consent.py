"""marketplace_installations.denied_permissions — granular consent (Plan 09 task_09_07).

Adds the ``denied_permissions`` JSONB column to ``marketplace_installations``
so the per-permission consent flow (task_09_07) can persist BOTH sides of
the project owner's decision:

  - ``granted_permissions`` (already present, added in 0041) — the subset of
    the listing's ``requested_permissions`` the project owner consented to.
  - ``denied_permissions`` (this migration) — the subset the project owner
    explicitly rejected.

The "pending" set is derived (requested minus granted minus denied), so it
needs no column. An install of a listing whose trust level requires per-permission
consent (community / experimental, plan decision (b)) stays ``disabled``
until EVERY requested permission has been granted; an explicit deny of any
required permission keeps it ``disabled`` and writes a ``consent_denied``
audit row.

Symmetric to ``granted_permissions``: same JSONB type, same ``'[]'::jsonb``
server default, NOT NULL. Fully reversible — ``downgrade`` drops the column.

Revision ID: 0042_marketplace_consent
Revises: 0041_marketplace
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0042_marketplace_consent"
down_revision: str | Sequence[str] | None = "0041_marketplace"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "marketplace_installations",
        sa.Column(
            "denied_permissions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("marketplace_installations", "denied_permissions")
