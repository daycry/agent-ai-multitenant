"""model_prices.provider_id — associate a price with a platform provider (task_11_2_06).

Adds a nullable ``provider_id`` FK on ``model_prices`` pointing at the
platform-global ``llm_providers`` table (created in migration 0070). This
materialises the lightweight "associate each model to its provider" goal
of ADR 0028 / Plan 11.2 **without** rebuilding the price catalog or the
LiteLLM sync — it is a purely additive association column.

Design (justified in the task): a **nullable FK to ``llm_providers.id``**
(not a free-form ``provider_kind`` text) because:

  * The catalog now has a real platform table of configured providers
    (``llm_providers``, migration 0070) — a FK points a price at an
    actual configured provider row, which is exactly the association the
    task asks for, rather than duplicating the four kind strings.
  * **Nullable**: the catalog's main feed is the LiteLLM sync, which only
    knows the free-form ``provider`` family string ("anthropic",
    "openai", ...), not which ``llm_providers`` row serves a model — so a
    price may be unassociated. A System Admin associates it explicitly.
  * **ON DELETE SET NULL**: deleting a provider config must NOT delete the
    price nor break its effective-dated history (the per-call price
    snapshot, task_11_13, refers to rows that must survive) — the price
    outlives the provider row. Mirrors the existing ``updated_by``
    nullable-FK / SET NULL pattern (migration 0049).

No ``tenant_id`` is involved: both ``model_prices`` and ``llm_providers``
are platform-global (ADR 0028). The global-read RLS policy of
``model_prices`` is untouched — this migration only adds a column + its
FK index and so does not alter the read/write split.

A partial index on the non-NULL FK supports "list/filter prices by
provider" (the read endpoint + admin UI) and keeps the FK from being
unindexed.

Single head before this migration is ``0070_llm_providers``; this is
``0071_model_prices_provider_id``. Fully reversible: ``downgrade`` drops
the index then the column, restoring 0070 exactly. Proven by
``tests/integration/test_model_provider_association.py`` (up / down / up).

Revision ID: 0071_model_prices_provider_id
Revises: 0070_llm_providers
Create Date: 2026-06-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0071_model_prices_provider_id"
down_revision: str | Sequence[str] | None = "0070_llm_providers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Additive, nullable association column — existing rows get NULL (no
    # provider associated yet). FK to the platform-global llm_providers row;
    # ON DELETE SET NULL so deleting a provider config keeps the price +
    # its history (the price outlives the provider row).
    op.add_column(
        "model_prices",
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_model_prices_provider_id",
        "model_prices",
        "llm_providers",
        ["provider_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # Partial index on the associated rows — supports "filter prices by
    # provider" (the read endpoint + admin UI) and avoids an unindexed FK.
    op.create_index(
        "ix_model_prices_provider_id",
        "model_prices",
        ["provider_id"],
        postgresql_where=sa.text("provider_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_model_prices_provider_id", table_name="model_prices")
    op.drop_constraint("fk_model_prices_provider_id", "model_prices", type_="foreignkey")
    op.drop_column("model_prices", "provider_id")
