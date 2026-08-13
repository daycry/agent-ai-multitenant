"""Índices de rendimiento + unicidad por tenant en teams/skills/agents (prod-13).

Cierra la parte de DDL de las tareas ``task_prod13_11`` y ``task_prod13_13`` del
plan `prod-13-rendimiento-y-datos`.

## 1. `ix_executions_tenant_created_at` — el índice que el sweep de presupuestos pide

`executions` solo tenía `ix_executions_task_id` y `ix_executions_tenant_status`.
Toda consulta de "gasto del tenant X en la ventana [a, b)" —
`budgets/consumption.py::_spend_usd_in_window`, el listado de runs de
`tenant_stats.py`, el sweep de presupuestos que cablea prod-06 (db-1)— filtra por
`tenant_id` **y** ordena/acota por `created_at`, y ninguna de las dos podía
servirse por índice a la vez. Con `(tenant_id, created_at)` el rango es un
index-scan sobre el prefijo del tenant.

El orden de las columnas importa y no es intercambiable: `tenant_id` va primero
porque es la igualdad (y porque RLS inyecta ese filtro en TODAS las queries),
`created_at` segundo porque es el rango.

## 2. Unicidad por tenant en `teams` / `skills` / `agents` (réplica de 0077)

La migración 0077 cerró este hueco para `tools` con un índice único **parcial**
`(tenant_id, name) WHERE deleted_at IS NULL` (un UNIQUE de PostgreSQL no admite
`WHERE`, de ahí el índice y no la constraint). `teams` tenía el mismo índice pero
**sin** `unique=True` — servía para buscar, no para impedir el duplicado — y
`skills` / `agents` no tenían ninguno sobre el nombre.

### Por qué `agents` NO lleva `(tenant_id, name)` y lleva DOS índices

El plan pedía "replicar el patrón 0077" en las tres tablas. Medido contra la BD
de desarrollo antes de escribir esto: hay **10 grupos** `(tenant_id, name)` con
más de una fila viva en `agents`, y los diez son el MISMO caso legítimo — un
agente `project_local` forkeado (`forked_from_agent_id`) de su plantilla
`global_tenant_template`, que por diseño conserva el nombre:

    CodeIgniter 4 — Backend Dev  | global_tenant_template + project_local

Un único índice `(tenant_id, name)` habría (a) exigido soft-borrar la mitad de
esas filas en el dedup previo y (b) roto el fork de agentes a partir de ese
momento. El eje correcto es el que ya declara
`ck_agents_scope_project_consistency`: el `project_id` es el discriminante.

  * `uq_agents_tenant_project_name_live` — `(tenant_id, project_id, name)` sobre
    los `project_local` (`project_id IS NOT NULL`). No se apoya en la semántica
    "NULLs distintos" de PostgreSQL: la excluye con un predicado explícito, para
    que el índice diga lo que hace.
  * `uq_agents_tenant_name_global_live` — `(tenant_id, name)` sobre los globales
    (`project_id IS NULL`), donde sí hay un único espacio de nombres por tenant.

Los dos predicados son disjuntos y cubren toda la tabla. Con este par, los
duplicados reales bajan de 10 grupos a **0** (medido), así que el dedup previo no
toca ninguna fila en la práctica y queda como red de seguridad para datos sucios.

### Dedup previo: "latest wins", igual que 0077 y 0076

Antes de construir cada índice se consolida cualquier grupo con más de una fila
viva: sobrevive la actualizada más recientemente y las perdedoras se
soft-borran. Es la única forma de que la migración no falle sobre datos sucios, y
es una operación de un solo sentido: el `downgrade` recupera el índice anterior,
no los `deleted_at` que puso. Riesgo aceptado y documentado en el plan (riesgo 4).

## 3. `documents.source_size_bytes` a BIGINT

Se declaró `Integer` en 0022, con lo que un documento de más de 2 GiB desborda al
persistir el tamaño. El límite de subida es muy inferior, pero el `kb_sync` de
docs y la promoción de ingesta escriben tamaños que no pasan por ese límite. Es
un `ALTER TYPE` que PostgreSQL resuelve reescribiendo la tabla; el downgrade
vuelve a `INTEGER` y **fallaría** si a esas alturas hubiera una fila > 2^31-1
(comportamiento correcto: perder el tamaño real en silencio sería peor).

## Lo que esta migración NO hace

`ix_chunks_content_fts` ya se reconstruyó con `public.es_unaccent` en la
migración **0107**, y `rag/search.py` unifica todas sus rutas sobre
`_TS_CONFIG = "public.es_unaccent"`. La tarea `task_prod13_10` estaba hecha
antes de empezar este plan; no se vuelve a tocar.

Revision ID: 0126_perf_indexes_uniqueness
Revises: 0125_cortex_conv_rls
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0126_perf_indexes_uniqueness"
down_revision: str | Sequence[str] | None = "0125_cortex_conv_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_LIVE = "deleted_at IS NULL"

# (tabla, columnas del grupo, predicado del índice único). El predicado incluye
# SIEMPRE `deleted_at IS NULL` para que un nombre soft-borrado quede libre.
_UNIQUE_SPECS: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    ("uq_teams_tenant_name_live", "teams", ("tenant_id", "name"), _LIVE),
    ("uq_skills_tenant_name_live", "skills", ("tenant_id", "name"), _LIVE),
    (
        "uq_agents_tenant_project_name_live",
        "agents",
        ("tenant_id", "project_id", "name"),
        f"{_LIVE} AND project_id IS NOT NULL",
    ),
    (
        "uq_agents_tenant_name_global_live",
        "agents",
        ("tenant_id", "name"),
        f"{_LIVE} AND project_id IS NULL",
    ),
)


def _dedup_latest_wins(table: str, group_by: Sequence[str], where: str) -> None:
    """Soft-borra las filas perdedoras de cada grupo duplicado ("latest wins").

    Mismo SQL que 0077: `row_number()` sobre la partición del grupo ordenada por
    `updated_at DESC, id DESC`, y `deleted_at = now()` a todo lo que no sea la
    fila 1. Idempotente: una segunda pasada no encuentra grupos.
    """
    partition = ", ".join(group_by)
    op.get_bind().execute(
        sa.text(f"""
            WITH ranked AS (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY {partition}
                           ORDER BY updated_at DESC, id DESC
                       ) AS rn
                  FROM {table}
                 WHERE {where}
            )
            UPDATE {table} t
               SET deleted_at = now()
              FROM ranked r
             WHERE t.id = r.id AND r.rn > 1
            """)
    )


def upgrade() -> None:
    # --- 1. executions: (tenant_id, created_at) para las ventanas de gasto ----
    op.create_index(
        "ix_executions_tenant_created_at",
        "executions",
        ["tenant_id", "created_at"],
    )

    # --- 2. unicidad por tenant en teams / skills / agents -------------------
    for index_name, table, columns, where in _UNIQUE_SPECS:
        _dedup_latest_wins(table, columns, where)
        op.create_index(
            index_name,
            table,
            list(columns),
            unique=True,
            postgresql_where=sa.text(where),
        )

    # `teams` ya tenía el MISMO par de columnas y predicado como índice NO
    # único: ahora es redundante (el único lo sirve igual de bien para buscar),
    # así que se retira en vez de mantener dos índices sobre lo mismo.
    op.drop_index("ix_teams_tenant_name", table_name="teams")

    # --- 3. documents.source_size_bytes -> BIGINT ----------------------------
    op.alter_column(
        "documents",
        "source_size_bytes",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
        existing_server_default=sa.text("0"),
    )


def downgrade() -> None:
    op.alter_column(
        "documents",
        "source_size_bytes",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
        existing_server_default=sa.text("0"),
    )

    # Restaura el índice NO único de `teams` tal como lo dejó 0002.
    op.create_index(
        "ix_teams_tenant_name",
        "teams",
        ["tenant_id", "name"],
        postgresql_where=sa.text(_LIVE),
    )
    for index_name, table, _columns, _where in reversed(_UNIQUE_SPECS):
        op.drop_index(index_name, table_name=table)

    op.drop_index("ix_executions_tenant_created_at", table_name="executions")
