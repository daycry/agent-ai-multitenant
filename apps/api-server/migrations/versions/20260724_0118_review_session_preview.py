"""review_sessions: plan_id nullable + kind discriminator (preview on-demand, ADR 0130).

Hasta ahora una ``review_sessions`` SIEMPRE pertenecía a un plan en validación
humana (``plan_id`` NOT NULL, FK CASCADE). ADR 0130 añade un **app-preview
on-demand**: el operador levanta la app del proyecto (rama por defecto) o de un
plan concreto durante 24 h, SIN veredicto. Esto reutiliza toda la maquinaria de
review-runtime (proxy firmado + reapers + expiry + WS de logs), que se apoya en
la fila ``review_sessions`` + la firma HMAC, no en el estado del plan.

Dos cambios de esquema, reversibles:

  1. ``plan_id`` pasa a NULLABLE — un preview de proyecto no cuelga de un plan
     (un preview de plan sí conserva su ``plan_id``).
  2. Se añade ``kind`` (``'plan'`` | ``'preview'``, default ``'plan'``) para que
     las consultas de validación humana (autostart idempotente, lookup del panel,
     barrido de hermanas al emitir veredicto) EXCLUYAN los previews y no se
     confundan con la sesión de validación real.

``downgrade`` es reversible: borra los previews (filas ``kind='preview'``; son
efímeras — 24 h — y sin valor histórico) y las filas con ``plan_id NULL``
remanentes antes de reinstalar el NOT NULL, luego elimina la columna ``kind``.

Revision ID: 0118_review_session_preview
Revises: 0117_skills_atlassian_category
Create Date: 2026-07-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0118_review_session_preview"
down_revision: str | Sequence[str] | None = "0117_skills_atlassian_category"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "review_sessions", "plan_id", existing_type=sa.dialects.postgresql.UUID(), nullable=True
    )
    op.add_column(
        "review_sessions",
        sa.Column("kind", sa.String(length=16), nullable=False, server_default=sa.text("'plan'")),
    )
    op.create_check_constraint(
        "ck_review_sessions_kind",
        "review_sessions",
        "kind IN ('plan', 'preview')",
    )
    # Un preview de PROYECTO no tiene plan; una sesión de PLAN (validación o
    # preview de plan) sí. La invariante: plan_id NULL ⇒ kind='preview'.
    op.create_check_constraint(
        "ck_review_sessions_plan_or_preview",
        "review_sessions",
        "plan_id IS NOT NULL OR kind = 'preview'",
    )


def downgrade() -> None:
    bind = op.get_bind()
    # Los previews son efímeros y sin historia: se borran antes de reinstalar el
    # NOT NULL (que rechazaría los de proyecto, con plan_id NULL).
    bind.execute(sa.text("DELETE FROM review_sessions WHERE kind = 'preview' OR plan_id IS NULL"))
    op.drop_constraint("ck_review_sessions_plan_or_preview", "review_sessions", type_="check")
    op.drop_constraint("ck_review_sessions_kind", "review_sessions", type_="check")
    op.drop_column("review_sessions", "kind")
    op.alter_column(
        "review_sessions",
        "plan_id",
        existing_type=sa.dialects.postgresql.UUID(),
        nullable=False,
    )
