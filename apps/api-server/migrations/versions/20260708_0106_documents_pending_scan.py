"""documents.status += 'pending_scan' — antivirus fail-closed (prod-12 av_01).

Con ClamAV inalcanzable la ingesta ya NO indexa con warning (fail-open): el
documento queda en ``pending_scan`` y el sweep de pendientes lo reintenta
cuando el backend vuelva (ADR 0105).

Revision ID: 0106_documents_pending_scan
Revises: 0105_project_allowed_domains
Create Date: 2026-07-08
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0106_documents_pending_scan"
down_revision: str | Sequence[str] | None = "0105_project_allowed_domains"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "ck_documents_status"
_OLD = "status IN ('pending', 'processing', 'indexed', 'indexed_empty', 'failed')"
_NEW = "status IN ('pending', 'pending_scan', 'processing', 'indexed', 'indexed_empty', 'failed')"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "documents", type_="check")
    op.create_check_constraint(_CONSTRAINT, "documents", _NEW)


def downgrade() -> None:
    # Normaliza al valor más cercano del set antiguo antes de re-estrechar la
    # CHECK: un documento a la espera de reescaneo vuelve a `pending` (el sweep
    # legacy lo re-encola igualmente).
    op.execute("UPDATE documents SET status = 'pending' WHERE status = 'pending_scan'")
    op.drop_constraint(_CONSTRAINT, "documents", type_="check")
    op.create_check_constraint(_CONSTRAINT, "documents", _OLD)
