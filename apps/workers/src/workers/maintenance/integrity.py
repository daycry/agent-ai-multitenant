"""Integridad referencial: sweep post-restore + vigilancia de tenants muertos
(PROJ-03 + G-04/P1-08, auditoría proyecto 2026-07-17).

El restore per-tenant copia con ``session_replication_role = replica`` (los
triggers de FK quedan apagados dentro de la transacción): si el bundle y la DB
viva divergen — catálogo builtin distinto, filas que referencian otro tenant,
restores parciales — quedan filas huérfanas que ninguna FK volverá a validar.

- :func:`sweep_fk_orphans` descubre las FKs single-column de ``public`` en
  ``pg_constraint`` y borra las filas hijas cuyo padre no existe, iterando
  hasta secarse (un borrado puede destapar huérfanos de segundo nivel).
  Cubre también la relación LÓGICA ``tenant_id -> organizations`` (las tablas
  tenant-scoped no llevan FK física al tenant): los hijos de un tenant
  inexistente caen en la misma pasada. Se ejecuta EXPLÍCITAMENTE
  (post-restore / one-shot), nunca en beat: borra datos.
- :func:`check_tenant_children` solo CUENTA hijos de tenants inexistentes
  (organizations) — el reconciler la corre cada 90s y deja WARNING; borrar es
  decisión del operador (o del sweep post-restore).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from sqlalchemy.engine import CursorResult

_log = structlog.get_logger("workers.maintenance.integrity")

# FKs single-column de tablas de `public` (multi-column: no hay ninguna hoy;
# si aparece una, queda excluida y avisada por el WARNING de abajo).
_FK_QUERY = text(
    """
    SELECT
        child.relname   AS child_table,
        att_child.attname  AS child_col,
        parent.relname  AS parent_table,
        att_parent.attname AS parent_col,
        array_length(con.conkey, 1) AS n_cols
    FROM pg_constraint con
    JOIN pg_class child ON child.oid = con.conrelid
    JOIN pg_class parent ON parent.oid = con.confrelid
    JOIN pg_namespace ns ON ns.oid = child.relnamespace
    JOIN pg_namespace pns ON pns.oid = parent.relnamespace
    LEFT JOIN pg_attribute att_child
        ON att_child.attrelid = child.oid AND att_child.attnum = con.conkey[1]
    LEFT JOIN pg_attribute att_parent
        ON att_parent.attrelid = parent.oid AND att_parent.attnum = con.confkey[1]
    WHERE con.contype = 'f' AND ns.nspname = 'public' AND pns.nspname = 'public'
    ORDER BY child.relname, con.conname
    """
)

# Tope de pasadas del sweep: cada pasada solo borra hijos directos de un padre
# ausente; una cadena huérfana de N niveles necesita N pasadas. 10 cubre
# cualquier profundidad real del esquema con margen.
_MAX_PASSES = 10

# Tablas de `public` con columna tenant_id (relación lógica a organizations,
# sin FK física). `organizations` misma queda fuera, obviamente.
_TENANT_TABLES_QUERY = text(
    """
    SELECT table_name FROM information_schema.columns
    WHERE table_schema = 'public' AND column_name = 'tenant_id'
      AND table_name <> 'organizations'
    ORDER BY table_name
    """
)

# Tablas hijas directas de organizations que el reconciler vigila (los
# hallazgos vivos de la auditoría: proyectos/agentes/equipos de tenants
# borrados a mano). plans/tasks cubren la propagación típica.
_TENANT_CHILD_TABLES = ("projects", "agents", "teams", "plans", "tasks")


async def sweep_fk_orphans(session: AsyncSession) -> dict[str, int]:
    """Borra las filas hijas cuyo padre referenciado no existe.

    Devuelve ``{"child.col->parent": filas_borradas}`` solo con entradas > 0.
    Iterativo hasta que una pasada no borra nada (huérfanos transitivos). El
    caller es dueño de la transacción.
    """
    fks = (await session.execute(_FK_QUERY)).all()
    single = [fk for fk in fks if fk.n_cols == 1]
    for fk in fks:
        if fk.n_cols != 1:
            _log.warning(
                "integrity.sweep.skipped_multicolumn_fk",
                child=fk.child_table,
                parent=fk.parent_table,
            )

    tenant_tables = list((await session.execute(_TENANT_TABLES_QUERY)).scalars())

    report: dict[str, int] = {}
    for _ in range(_MAX_PASSES):
        deleted_this_pass = 0
        for fk in single:
            stmt = text(
                f'DELETE FROM "{fk.child_table}" c'
                f' WHERE c."{fk.child_col}" IS NOT NULL'
                f' AND NOT EXISTS (SELECT 1 FROM "{fk.parent_table}" p'
                f' WHERE p."{fk.parent_col}" = c."{fk.child_col}")'
            )
            result = cast("CursorResult[Any]", await session.execute(stmt))
            count = int(result.rowcount or 0)
            if count:
                key = f"{fk.child_table}.{fk.child_col}->{fk.parent_table}"
                report[key] = report.get(key, 0) + count
                deleted_this_pass += count
        # Relación lógica tenant_id -> organizations (sin FK física). tenant_id
        # NULL es legítimo (filas de plataforma) y no se toca.
        for table in tenant_tables:
            result = cast(
                "CursorResult[Any]",
                await session.execute(
                    text(
                        f'DELETE FROM "{table}" c'
                        " WHERE c.tenant_id IS NOT NULL"
                        " AND NOT EXISTS (SELECT 1 FROM organizations o"
                        " WHERE o.id = c.tenant_id)"
                    )
                ),
            )
            count = int(result.rowcount or 0)
            if count:
                key = f"{table}.tenant_id->organizations"
                report[key] = report.get(key, 0) + count
                deleted_this_pass += count
        if deleted_this_pass == 0:
            break
    if report:
        _log.warning("integrity.sweep.orphans_deleted", **report)
    return report


async def check_tenant_children(session: AsyncSession) -> dict[str, int]:
    """Cuenta filas de las tablas hijas cuyo ``tenant_id`` no existe en
    ``organizations``. Solo informa (nunca borra) — el reconciler deja WARNING.
    Devuelve solo entradas > 0."""
    report: dict[str, int] = {}
    for table in _TENANT_CHILD_TABLES:
        count = (
            await session.execute(
                text(
                    f'SELECT count(*) FROM "{table}" c'
                    " WHERE NOT EXISTS (SELECT 1 FROM organizations o"
                    " WHERE o.id = c.tenant_id)"
                )
            )
        ).scalar_one()
        if count:
            report[table] = int(count)
    return report


__all__ = ["check_tenant_children", "sweep_fk_orphans"]
