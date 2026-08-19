"""agent_prompt_versions — el historial del prompt del agente (`task_gov_02`).

Hoy `PUT /agents/{id}` sobrescribe `system_prompt` y `model_config.system_prompts`
con un `apply_partial_update` + `flush`: **sin versión, sin autor y sin diff**. Si
la calidad de un agente cae, no hay forma de saber qué cambió ni de volver.

Cuatro rasgos de esta tabla que no son de adorno:

1. **Append-only, y se nota en el esquema.** No hay `updated_at` — a diferencia de
   casi todas las tablas de este repo, que traen `TimestampMixin`. Una fila de
   historial que se puede editar no es historial. El invariante se sostiene en el
   repositorio (`db/agent_prompt_version_repo.py`, sólo `append` + `list`), y la
   ausencia de la columna es la señal de que ese repositorio no miente.
2. **`UNIQUE (agent_id, version)`**, que hace dos cosas a la vez: impide dos
   versiones con el mismo número —una carrera entre dos `PUT` simultáneos aterriza
   como 409 y no como historial duplicado, vía `flush_or_conflict`— y sirve de
   índice para la única consulta caliente, «la última versión de este agente»
   (`ORDER BY version DESC LIMIT 1`, que PostgreSQL resuelve como recorrido hacia
   atrás del mismo índice, sin `Sort`).
3. **RLS con `FORCE` y política por tenant**, patrón exacto de la migración 0127
   (`user_invitations`). `tests/integration/test_rls_invariant.py` descubre en el
   catálogo toda tabla con columna `*tenant_id` y exige `ENABLE` + `FORCE` + una
   policy que referencie `app.tenant_id`: crearla sin política habría roto la
   suite en el sitio correcto.
4. **`parent_version_id` es la cadena, y su FK es `SET NULL`.** No `CASCADE`:
   borrar un eslabón no debe llevarse el resto del historial por delante. El
   `agent_id`, en cambio, sí es `CASCADE` — el historial del prompt de un agente
   que ya no existe no le sirve a nadie, y `agents` es la fila de dominio.

## Por qué `prompt_hash` vive aquí y no se recalcula al leer

Es el sello del texto EFECTIVO que viajó al modelo —lo que
`resolve_agent_persona` resuelve: `model_config.system_prompts.es` → `.en` →
`system_prompt` plano, capado a `PERSONA_MAX_CHARS`—, y `task_gov_03` lo mezcla en
`executions.prompt_version`. Recalcularlo al leer lo ataría a la versión de HOY
del resolutor: el día que cambie la precedencia bilingüe o el cap, los sellos de
los runs viejos se moverían y dejarían de identificar lo que de verdad corrió. Es
el mismo fallo que ya se pagó con `EvalRun.subject_prompt_version`.

`system_prompt` y `persona` guardan en cambio los valores **crudos**, que es lo
que un humano necesita para leer el diff: si sólo se guardase el efectivo, un
cambio en el idioma no preferido no aparecería en el historial.

Revision ID: 0143_agent_prompt_versions
Revises: 0142_cortex_forget_sweep_index
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0143_agent_prompt_versions"
down_revision: str | Sequence[str] | None = "0142_cortex_forget_sweep_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RLS_UP: tuple[str, ...] = (
    "ALTER TABLE agent_prompt_versions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE agent_prompt_versions FORCE ROW LEVEL SECURITY",
    "CREATE POLICY tenant_isolation ON agent_prompt_versions FOR ALL"
    " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
)

_RLS_DOWN: tuple[str, ...] = (
    "DROP POLICY IF EXISTS tenant_isolation ON agent_prompt_versions",
    "ALTER TABLE agent_prompt_versions DISABLE ROW LEVEL SECURITY",
)


def upgrade() -> None:
    op.create_table(
        "agent_prompt_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        # El prompt plano CRUDO, tal cual estaba en `agents.system_prompt`.
        sa.Column("system_prompt", sa.Text(), nullable=False),
        # `model_config.system_prompts` crudo ({} cuando el agente no es bilingüe).
        sa.Column(
            "persona",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        # sha256 hex del texto EFECTIVO (64 chars) — ver el docstring del módulo.
        sa.Column("prompt_hash", sa.String(length=64), nullable=False),
        sa.Column("changed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("parent_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "version", name="uq_agent_prompt_versions_agent_version"),
        sa.CheckConstraint("version >= 1", name="ck_agent_prompt_versions_version_positive"),
        sa.ForeignKeyConstraint(["tenant_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        # SET NULL: el historial sobrevive al usuario que lo escribió — es un
        # rastro de auditoría, no una relación de dominio (patrón de la 0127).
        sa.ForeignKeyConstraint(["changed_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["parent_version_id"], ["agent_prompt_versions.id"], ondelete="SET NULL"
        ),
    )
    # `TenantScopedMixin` declara `tenant_id` con `index=True`, así que el modelo
    # lo espera: sin este índice, un `alembic revision --autogenerate` lo
    # propondría a partir de hoy y la deriva modelo↔BD crecería en silencio
    # (`tests/integration/test_alembic_autogenerate_clean.py`).
    op.create_index(
        "ix_agent_prompt_versions_tenant_id",
        "agent_prompt_versions",
        ["tenant_id"],
        unique=False,
    )
    for stmt in _RLS_UP:
        op.execute(stmt)


def downgrade() -> None:
    # Reversible de verdad: política, RLS, índice y tabla. El historial se pierde
    # con la tabla, que es el único dato que esta migración introdujo. El índice
    # único lo crea `UniqueConstraint`, así que se va con el `DROP TABLE`.
    for stmt in _RLS_DOWN:
        op.execute(stmt)
    op.drop_index("ix_agent_prompt_versions_tenant_id", table_name="agent_prompt_versions")
    op.drop_table("agent_prompt_versions")
