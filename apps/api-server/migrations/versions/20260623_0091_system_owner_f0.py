"""córtex F0: users.is_system_owner (rol System Owner, singleton)

Cimiento (F0) del Córtex (ADR 0074). Añade ``users.is_system_owner`` (booleano global,
distinto de ``is_system_admin``) con un índice UNIQUE PARCIAL ``WHERE is_system_owner``
que impone el invariante singleton (a lo sumo un dueño del despliegue). Aditivo y
reversible: las filas existentes quedan en ``false``. Las tablas/bucles del córtex
(F1+) NO entran aquí — están gated.

Revision ID: 0091_system_owner_f0
Revises: 0090_execution_cancel
Create Date: 2026-06-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0091_system_owner_f0"
down_revision: str | Sequence[str] | None = "0090_execution_cancel"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_system_owner",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # Singleton: at most one owner. Partial unique index over the TRUE rows only,
    # so the many `false` rows don't collide.
    op.create_index(
        "uq_users_system_owner",
        "users",
        ["is_system_owner"],
        unique=True,
        postgresql_where=sa.text("is_system_owner"),
    )


def downgrade() -> None:
    op.drop_index("uq_users_system_owner", table_name="users")
    op.drop_column("users", "is_system_owner")
