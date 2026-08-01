"""`guardrail_configs`: las TRES capas de guardrails, persistidas (prod-03 task_prod03_07).

`shared_guardrails.layers.resolve_config` sabe fusionar plataforma → tenant →
proyecto desde el Plan 11, con candados incluidos. Lo que no había era dónde
guardar las tres: la config vivía en dos sitios sueltos —
``platform_settings.guardrails_config`` (global, sin RLS) y
``projects.guardrails_config`` (migración 0110, ADR 0102 D3)— y la capa
**tenant** no existía en ninguna parte. Un tenant no podía endurecer sus
guardrails por encima del baseline de plataforma sin tocar proyecto por
proyecto, que es justo la capa intermedia que el Principio Rector nº10 promete.

Una tabla, tres scopes
----------------------
Cada fila es una capa. El scope dice cuál, y un par de CHECK cierran las
combinaciones que no significan nada:

* ``platform`` — ``tenant_id IS NULL`` y ``project_id IS NULL``. Una capa de
  plataforma que perteneciera a un tenant sería una contradicción.
* ``tenant``   — ``tenant_id NOT NULL``, ``project_id IS NULL``.
* ``project``  — los dos NOT NULL. El ``tenant_id`` es redundante con el del
  proyecto y está a propósito: es lo que hace que la RLS pueda filtrar sin un
  JOIN, y un trigger no hace falta porque esta tabla la escribe el api-server
  con el tenant ya en la mano.

Y tres índices únicos parciales: una fila de plataforma, una por tenant, una por
proyecto. Dos filas «efectivas» para el mismo ámbito serían una config ambigua,
y una config de seguridad ambigua se resuelve mal.

La asimetría de la RLS, que es deliberada
-----------------------------------------
La policy abre en lectura y cierra en escritura::

    USING      (tenant_id IS NULL OR tenant_id = app.tenant_id)
    WITH CHECK (tenant_id = app.tenant_id)

La rama ``IS NULL`` del ``USING`` deja que **cualquier** tenant LEA el baseline
de plataforma. Es intencionado y es estrictamente más seguro que hoy: ese
baseline es la capa que todos heredan (hoy vive en ``platform_settings``, una
tabla directamente SIN RLS), no contiene dato de ningún tenant, y sin poder
leerlo un proyecto se quedaría sin los guardrails obligatorios — que es el modo
de fallo que hay que evitar por encima de todo.

El ``WITH CHECK`` NO tiene esa rama: desde una sesión de tenant no se puede
crear ni modificar la fila de plataforma. Quien la escribe es el System Admin
sin contexto de tenant, que `open_tenant_session` atiende con
``migrations_user`` (BYPASSRLS), o el seed. La comprobación no depende de la
buena voluntad de la capa de aplicación: la hace PostgreSQL.

``version`` es para invalidar caché, no para historial: esta tabla guarda la
config VIGENTE de cada capa y se incrementa al escribir, de modo que el dispatch
(task_prod03_11) pueda cachear la config efectiva y saber cuándo caducarla sin
volver a leer el JSONB entero.

Reversible de verdad: el ``downgrade`` retira policy, RLS, índices y tabla, y
`tests/integration/test_guardrail_configs_table.py` lo EJECUTA (downgrade →
comprobar que no existe → upgrade → comprobar que vuelve con RLS). Se pierde lo
que hubiera en la tabla, que es el único dato que esta migración introduce; las
dos capas viejas (`platform_settings` / `projects`) no se tocan, así que bajar
deja la plataforma exactamente como estaba antes de subir.

Revision ID: 0132_guardrail_configs
Revises: 0131_partition_guardrail_events
Create Date: 2026-08-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0132_guardrail_configs"
down_revision: str | Sequence[str] | None = "0131_partition_guardrail_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_GUC = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"

_RLS_UP: tuple[str, ...] = (
    "ALTER TABLE guardrail_configs ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE guardrail_configs FORCE ROW LEVEL SECURITY",
    "CREATE POLICY tenant_isolation ON guardrail_configs FOR ALL"
    f" USING (tenant_id IS NULL OR tenant_id = {_TENANT_GUC})"
    f" WITH CHECK (tenant_id = {_TENANT_GUC})",
)

_RLS_DOWN: tuple[str, ...] = (
    "DROP POLICY IF EXISTS tenant_isolation ON guardrail_configs",
    "ALTER TABLE guardrail_configs DISABLE ROW LEVEL SECURITY",
)


def upgrade() -> None:
    op.create_table(
        "guardrail_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "config",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "scope IN ('platform', 'tenant', 'project')",
            name="ck_guardrail_configs_scope",
        ),
        # Qué columnas exige cada capa. Sin esto, una fila `platform` con
        # `tenant_id` sería aceptada por la BD y quedaría invisible para todos
        # (la RLS la filtraría) — un baseline que existe y no se aplica.
        sa.CheckConstraint(
            "(scope = 'platform' AND tenant_id IS NULL AND project_id IS NULL)"
            " OR (scope = 'tenant' AND tenant_id IS NOT NULL AND project_id IS NULL)"
            " OR (scope = 'project' AND tenant_id IS NOT NULL AND project_id IS NOT NULL)",
            name="ck_guardrail_configs_scope_columns",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        # El autor sobrevive a su cuenta: saber QUIÉN relajó un guardrail es
        # auditoría, no una relación de dominio.
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
    )
    # Una fila efectiva por capa. Parciales porque el scope decide qué columnas
    # participan en la clave (un UNIQUE plano sobre (scope, tenant_id,
    # project_id) no serviría: en PostgreSQL, NULL != NULL, así que admitiría
    # infinitas filas de plataforma).
    op.create_index(
        "uq_guardrail_configs_platform",
        "guardrail_configs",
        ["scope"],
        unique=True,
        postgresql_where=sa.text("scope = 'platform'"),
    )
    op.create_index(
        "uq_guardrail_configs_tenant",
        "guardrail_configs",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("scope = 'tenant'"),
    )
    op.create_index(
        "uq_guardrail_configs_project",
        "guardrail_configs",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("scope = 'project'"),
    )
    # El camino caliente es «dame las capas de este tenant», que el resolvedor
    # de config efectiva hace en cada dispatch.
    op.create_index(
        "ix_guardrail_configs_tenant_scope",
        "guardrail_configs",
        ["tenant_id", "scope"],
        unique=False,
    )
    for stmt in _RLS_UP:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in _RLS_DOWN:
        op.execute(stmt)
    op.drop_index("ix_guardrail_configs_tenant_scope", table_name="guardrail_configs")
    op.drop_index("uq_guardrail_configs_project", table_name="guardrail_configs")
    op.drop_index("uq_guardrail_configs_tenant", table_name="guardrail_configs")
    op.drop_index("uq_guardrail_configs_platform", table_name="guardrail_configs")
    op.drop_table("guardrail_configs")
