"""marketplace v2 (ADR 0142 D6) — publicar pasa por revisión.

Cuatro columnas en `marketplace_listings` y ningún concepto nuevo: la revisión
es un estado del listing, no una tabla aparte, porque no hay nada que guardar
por revisión más allá del veredicto vigente (el histórico de quién decidió qué
ya lo lleva la auditoría append-only, que `marketplace/review.py` escribe por
duplicado — fila de tenant + fila de plataforma).

* `review_status` — `draft | pending_review | published | rejected`, con CHECK.
  **`server_default = 'published'`, y esa asimetría es deliberada.** El único
  publicador no confiable es `POST /marketplace/private/listings`, y ese camino
  escribe `pending_review` de forma EXPLÍCITA. Lo demás que escribe esta tabla
  es curado por la plataforma: el seed del catálogo oficial y este mismo
  backfill. Con default `'draft'` el catálogo vivo se vaciaría en el despliegue
  y en cada re-seed — un apagón más ruidoso que el fallo del que protege el
  default estricto.
* `reviewed_by` / `reviewed_at` — quién y cuándo. **NULL en las filas que este
  backfill publica**: a nadie le pasaron por delante, y estampar un revisor ahí
  sería mentir en el rastro de auditoría.
* `rejection_reason` — obligatorio en un rechazo, pero NO por CHECK: la columna
  es NULL en las otras tres cuartas partes del vocabulario, así que la guarda
  vive donde se puede expresar de verdad (`review.reject_listing`, que rechaza
  también un motivo de solo espacios).

El backfill no es una sentencia aparte: `ADD COLUMN ... NOT NULL DEFAULT
'published'` rellena las filas existentes en el mismo paso, que es exactamente
«lo publicado hoy sigue publicado».

RLS: **sin cambios**. No hay tabla nueva; `marketplace_listings` ya es híbrida
con sus tres policies desde la 0043, y añadir columnas no las toca. Por eso esta
migración no aparece en `test_rls_invariant`: no tiene nada que declararle.

Revision ID: 0129_listing_review
Revises: 0128_marketplace_v2_deploy
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0129_listing_review"
down_revision: str | Sequence[str] | None = "0128_marketplace_v2_deploy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK_NAME = "ck_marketplace_listings_review_status"
_QUEUE_INDEX = "ix_marketplace_listings_review_queue"


def upgrade() -> None:
    op.add_column(
        "marketplace_listings",
        sa.Column(
            "review_status",
            sa.String(length=16),
            server_default=sa.text("'published'"),
            nullable=False,
        ),
    )
    op.add_column(
        "marketplace_listings",
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "marketplace_listings",
        sa.Column("reviewed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "marketplace_listings",
        sa.Column("rejection_reason", sa.Text(), nullable=True),
    )

    # SET NULL: el veredicto sobrevive a quien lo firmó — es rastro, no relación
    # de dominio. Borrar al revisor no puede borrar la revisión.
    op.create_foreign_key(
        "fk_marketplace_listings_reviewed_by",
        "marketplace_listings",
        "users",
        ["reviewed_by"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_check_constraint(
        _CHECK_NAME,
        "marketplace_listings",
        "review_status IN ('draft', 'pending_review', 'published', 'rejected')",
    )

    # La cola del admin: todo lo que NO está publicado, que es el lado pequeño
    # de la tabla. Parcial para no pagar el índice por el catálogo entero.
    op.create_index(
        _QUEUE_INDEX,
        "marketplace_listings",
        ["review_status"],
        unique=False,
        postgresql_where=sa.text("review_status <> 'published' AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    """Baja de verdad: índice, CHECK, FK y las cuatro columnas.

    Se pierde el estado de revisión, que es justo el dato que esta migración
    introdujo. Un listing que estaba en la cola vuelve a ser indistinguible de
    uno publicado — es la consecuencia inevitable de quitar la columna que los
    distingue, y por eso el downgrade se ejerce en el round-trip del test y no
    en producción con la cola llena.
    """
    op.drop_index(_QUEUE_INDEX, table_name="marketplace_listings")
    op.drop_constraint(_CHECK_NAME, "marketplace_listings", type_="check")
    op.drop_constraint(
        "fk_marketplace_listings_reviewed_by", "marketplace_listings", type_="foreignkey"
    )
    op.drop_column("marketplace_listings", "rejection_reason")
    op.drop_column("marketplace_listings", "reviewed_at")
    op.drop_column("marketplace_listings", "reviewed_by")
    op.drop_column("marketplace_listings", "review_status")
