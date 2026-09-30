"""Utilidad, `disputed`, archivado y compactación de memorias (task_mem_10 / task_mem_11).

ADITIVA, reversible y transaccional (sin ``CREATE INDEX CONCURRENTLY``: rompería el
cerrojo de migraciones, ver el docstring de la 0150). Tres cambios:

1. ``memory_entries`` gana cinco columnas, todas con DEFAULT constante o NULL, así que
   ninguna reescribe la tabla:

   - ``useful_count`` / ``harmful_count`` (``integer NOT NULL DEFAULT 0``): los votos de
     ``memory_feedback``, que el ranking lee (``memorizer.ranking``);
   - ``disputed_at`` (``timestamptz`` NULL): la memoria está marcada como errónea o
     desfasada (``wrong`` / ``outdated``). NO se borra: el ranking la baja y la
     compactación la propone al operador;
   - ``archived_at`` / ``archive_reason``: archivado SOFT. El recall no la ve; el visor
     sí, con el filtro ``archived=true``.

2. ``memory_feedback``: un voto por (memoria, votante). El votante es la ejecución en
   marcha o, sin ella, el agente: un run no puede inflar ni hundir una memoria repitiendo
   el mismo veredicto, y cambiar de opinión reescribe su fila. Es la traza de auditoría de
   cada voto (quién, cuándo, con qué nota). FK a ``memory_entries`` con ``ON DELETE
   CASCADE`` (la tabla no está particionada).

3. ``memory_compaction_proposals``: la revisión que el job de mantenimiento propone para
   las memorias ``disputed`` de un mismo proyecto y que un ``tenant_admin`` confirma o
   rechaza. A lo sumo UNA pendiente por proyecto (índice único parcial): el job refresca
   la pendiente en vez de apilar duplicados.

Las dos tablas nuevas llevan ``tenant_id UUID NOT NULL`` y RLS ``FOR ALL`` LITERAL (la
guarda ``tests/security/test_pentest_findings.py`` busca ese ``ALTER TABLE <tabla>
ENABLE ROW LEVEL SECURITY`` con el nombre escrito, no plantillado).

Revision ID: 0153_memory_feedback_archive
Revises: 0152_memory_entries_kind
Create Date: 2026-09-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0153_memory_feedback_archive"
down_revision: str | Sequence[str] | None = "0152_memory_entries_kind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: El predicado canónico de aislamiento por tenant, LITERAL de la migración 0052.
_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.add_column(
        "memory_entries",
        sa.Column("useful_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "memory_entries",
        sa.Column("harmful_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "memory_entries", sa.Column("disputed_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )
    op.add_column(
        "memory_entries", sa.Column("archived_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )
    op.add_column(
        "memory_entries", sa.Column("archive_reason", sa.String(length=40), nullable=True)
    )

    op.execute(
        """
        CREATE TABLE memory_feedback (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            memory_id uuid NOT NULL REFERENCES memory_entries(id) ON DELETE CASCADE,
            voter_key varchar(64) NOT NULL,
            agent_id uuid,
            execution_id uuid,
            verdict varchar(10) NOT NULL,
            note text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_memory_feedback_verdict
                CHECK (verdict IN ('useful', 'wrong', 'outdated')),
            CONSTRAINT uq_memory_feedback_voter UNIQUE (tenant_id, memory_id, voter_key)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_memory_feedback_tenant_memory ON memory_feedback (tenant_id, memory_id)"
    )
    op.execute("ALTER TABLE memory_feedback ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE memory_feedback FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY memory_feedback_tenant_isolation ON memory_feedback FOR ALL"
        f" USING ({_PREDICATE}) WITH CHECK ({_PREDICATE})"
    )

    op.execute(
        """
        CREATE TABLE memory_compaction_proposals (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            status varchar(12) NOT NULL DEFAULT 'pending',
            memory_ids jsonb NOT NULL,
            summary text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            decided_at timestamptz,
            decided_by uuid REFERENCES users(id) ON DELETE SET NULL,
            CONSTRAINT ck_memory_compaction_status
                CHECK (status IN ('pending', 'confirmed', 'rejected'))
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_memory_compaction_pending_project"
        " ON memory_compaction_proposals (tenant_id, project_id) WHERE status = 'pending'"
    )
    op.execute(
        "CREATE INDEX ix_memory_compaction_tenant_status"
        " ON memory_compaction_proposals (tenant_id, status, created_at)"
    )
    op.execute("ALTER TABLE memory_compaction_proposals ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE memory_compaction_proposals FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY memory_compaction_proposals_tenant_isolation"
        " ON memory_compaction_proposals FOR ALL"
        f" USING ({_PREDICATE}) WITH CHECK ({_PREDICATE})"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS memory_compaction_proposals")
    op.execute("DROP TABLE IF EXISTS memory_feedback")
    op.drop_column("memory_entries", "archive_reason")
    op.drop_column("memory_entries", "archived_at")
    op.drop_column("memory_entries", "disputed_at")
    op.drop_column("memory_entries", "harmful_count")
    op.drop_column("memory_entries", "useful_count")
