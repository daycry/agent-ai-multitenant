"""Provenance del marketplace en el catálogo nativo (ADR 0100, pieza 1).

``tools`` y ``skills`` ganan tres columnas nullable — espejo del idioma
``forked_from_*`` de agents/teams: ``source_listing_id`` (FK
``marketplace_listings`` ON DELETE SET NULL), ``source_installation_id``
(FK ``marketplace_installations`` ON DELETE SET NULL) y ``source_version``.
Índice parcial por ``source_installation_id`` (la des-materialización de
uninstall/revoke busca por él). Schema-only, reversible, sin cambio de
comportamiento por sí sola.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0111_marketplace_provenance"
down_revision: str | Sequence[str] | None = "0110_projects_guardrails"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("tools", "skills")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column(
                "source_listing_id",
                UUID(as_uuid=True),
                sa.ForeignKey("marketplace_listings.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
        op.add_column(
            table,
            sa.Column(
                "source_installation_id",
                UUID(as_uuid=True),
                sa.ForeignKey("marketplace_installations.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
        op.add_column(table, sa.Column("source_version", sa.Text(), nullable=True))
        op.create_index(
            f"ix_{table}_source_installation",
            table,
            ["source_installation_id"],
            postgresql_where=sa.text("source_installation_id IS NOT NULL"),
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_index(f"ix_{table}_source_installation", table_name=table)
        op.drop_column(table, "source_version")
        op.drop_column(table, "source_installation_id")
        op.drop_column(table, "source_listing_id")
