"""Índices de la memoria y compactación por ámbito (revisión adversarial del plan de memoria).

ADITIVA, reversible y transaccional (sin ``CREATE INDEX CONCURRENTLY``: rompería el cerrojo de
migraciones, ver el docstring de la 0150). Cuatro cambios:

1. ``ix_memory_entries_tenant_source_exec``: ``(tenant_id, source_execution_id)`` parcial
   (``source_execution_id IS NOT NULL``). Lo usan el resumen/línea de tiempo por ejecución y
   el listado por ``execution_id``; sin él cada consulta recorre las memorias del tenant.
2. ``ix_memory_entries_archive_candidates``: índice parcial por ``created_at`` con el
   predicado del archivado (``memorizer.lifecycle``): vivas, ``episodic``, sin ``kind =
   summary``, ``recall_count = 0`` y de un ámbito que escriben los agentes. El job diario
   deja de escanear toda la tabla.
3. ``memory_feedback.reviewed_at``: marca un voto negativo como REVISADO (al rechazar una
   compactación o al votar ``useful`` un humano): sólo los votos posteriores vuelven a
   disputar la memoria. Nullable, sin reescribir la tabla.
4. ``memory_compaction_proposals`` agrupa también ``team_shared`` y ``global``: ``scope``
   (default ``project_shared``), ``team_id`` (FK a ``teams``) y ``project_id`` pasa a
   nullable, con un CHECK que exige el puntero que toca a cada ámbito y un índice único
   parcial de UNA pendiente por (tenant, ámbito, dueño). ``private`` nunca se propone.

La bajada borra las propuestas que no son de ``project_shared`` (no caben en el esquema
anterior) antes de restaurar el ``NOT NULL``.

Revision ID: 0154_memory_indexes
Revises: 0153_memory_feedback_archive
Create Date: 2026-09-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0154_memory_indexes"
down_revision: str | Sequence[str] | None = "0153_memory_feedback_archive"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ZERO_UUID = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_memory_entries_tenant_source_exec"
        " ON memory_entries (tenant_id, source_execution_id)"
        " WHERE source_execution_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_memory_entries_archive_candidates ON memory_entries (created_at)"
        " WHERE deleted_at IS NULL AND archived_at IS NULL AND type = 'episodic'"
        " AND kind IS DISTINCT FROM 'summary' AND recall_count = 0"
        " AND scope IN ('team_shared', 'project_shared', 'global')"
    )
    op.add_column(
        "memory_feedback", sa.Column("reviewed_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )
    op.add_column(
        "memory_compaction_proposals",
        sa.Column(
            "scope",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'project_shared'"),
        ),
    )
    op.execute(
        "ALTER TABLE memory_compaction_proposals"
        " ADD COLUMN team_id uuid REFERENCES teams(id) ON DELETE CASCADE"
    )
    op.execute("ALTER TABLE memory_compaction_proposals ALTER COLUMN project_id DROP NOT NULL")
    op.execute(
        "ALTER TABLE memory_compaction_proposals ADD CONSTRAINT ck_memory_compaction_owner CHECK ("
        " (scope = 'project_shared' AND project_id IS NOT NULL)"
        " OR (scope = 'team_shared' AND team_id IS NOT NULL)"
        " OR scope = 'global')"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_memory_compaction_pending_owner"
        " ON memory_compaction_proposals"
        f" (tenant_id, scope, COALESCE(project_id, team_id, '{_ZERO_UUID}'::uuid))"
        " WHERE status = 'pending'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_memory_compaction_pending_owner")
    op.execute("DELETE FROM memory_compaction_proposals WHERE scope <> 'project_shared'")
    op.execute("ALTER TABLE memory_compaction_proposals DROP CONSTRAINT ck_memory_compaction_owner")
    op.execute("ALTER TABLE memory_compaction_proposals ALTER COLUMN project_id SET NOT NULL")
    op.execute("ALTER TABLE memory_compaction_proposals DROP COLUMN team_id")
    op.execute("ALTER TABLE memory_compaction_proposals DROP COLUMN scope")
    op.drop_column("memory_feedback", "reviewed_at")
    op.execute("DROP INDEX IF EXISTS ix_memory_entries_archive_candidates")
    op.execute("DROP INDEX IF EXISTS ix_memory_entries_tenant_source_exec")
