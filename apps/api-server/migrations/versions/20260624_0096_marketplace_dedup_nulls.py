"""Marketplace: dedupe tenant-wide installs (project_id NULL) — L4 (auditoría 2026-06).

The partial unique index ``uq_marketplace_installations_live`` was over
``(tenant_id, listing_id, project_id)``. PostgreSQL treats NULLs as distinct, so
it did NOT prevent two concurrent TENANT-WIDE installs (``project_id IS NULL``) of
the same listing — the dedup depended solely on the router's SELECT-then-insert,
which is racy under concurrency (TOCTOU). Replacing ``project_id`` with
``COALESCE(project_id, '0000…'::uuid)`` maps the NULLs onto a single sentinel so
tenant-wide rows collide, making the DB (IntegrityError → 409) the real barrier
for both project-scoped and tenant-wide installs.

Reversible: downgrade restores the original plain partial index.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0096_marketplace_dedup_nulls"
down_revision: str | Sequence[str] | None = "0095_cortex_curiosity_pursuits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAME = "uq_marketplace_installations_live"
_TABLE = "marketplace_installations"
_WHERE = "deleted_at IS NULL AND status != 'revoked'"
_ZERO = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    op.drop_index(_NAME, table_name=_TABLE)
    op.create_index(
        _NAME,
        _TABLE,
        ["tenant_id", "listing_id", sa.text(f"COALESCE(project_id, '{_ZERO}'::uuid)")],
        unique=True,
        postgresql_where=sa.text(_WHERE),
    )


def downgrade() -> None:
    op.drop_index(_NAME, table_name=_TABLE)
    op.create_index(
        _NAME,
        _TABLE,
        ["tenant_id", "listing_id", "project_id"],
        unique=True,
        postgresql_where=sa.text(_WHERE),
    )
