"""Retirar a la aplicación todo acceso a `approval_policy_backfill_0133`.

## El agujero

La 0133 (ADR 0153) crea `approval_policy_backfill_0133` para guardar, fila a
fila, la política de aprobación previa de CADA proyecto de la plataforma — es lo
que hace reversible aquella migración. La tabla no tiene `tenant_id` ni RLS,
porque nadie la consulta desde la aplicación: la escribe el `upgrade` y la lee el
`downgrade`, ambos como `migrations_user`.

Lo que se pasó por alto es que **no hacía falta consultarla para que fuese
legible**. `docker/postgres/init/02-roles.sh:52` deja puesto un

    ALTER DEFAULT PRIVILEGES ... GRANT SELECT, INSERT, UPDATE, DELETE
                                 ON TABLES TO app_user

así que toda tabla que Alembic cree después nace con permisos para el usuario de
la aplicación. Sin RLS que la filtre, cualquier sesión de tenant podía leer la
configuración de aprobación de los proyectos de **todos los demás tenants**: qué
categorías gatea cada uno, o sea el mapa de por dónde no mira nadie. No son
credenciales, pero es inteligencia sobre otros clientes y viola el Principio
Rector nº1.

Lo destapó `test_every_global_table_is_documented` al correr la suite de
particionado, y el carril que lo encontró hizo lo correcto: NO lo metió en la
allowlist para que su suite pasara.

## Por qué se revoca y no se le pone RLS

Darle `tenant_id` + RLS la dejaría legible por su propio tenant. Pero la
aplicación no tiene NINGÚN motivo para leerla, así que la afirmación honesta es
más fuerte: no hay acceso. Quitar el permiso es defensa en profundidad; una
policy es una filtración que se confía en filtrar bien.

`tests/integration/test_rls_invariant.py::test_the_backfill_table_is_unreachable_from_the_app`
comprueba que esto se cumple de verdad, para que la entrada de la allowlist no
sea una promesa sobre el papel.

## Por qué una migración aparte y no un `REVOKE` dentro de la 0133

Una base que ya hubiera aplicado la 0133 no volvería a ejecutarla. Esta, en
cambio, la arregla esté donde esté la cadena. Es idempotente y su `downgrade` no
devuelve el permiso: restaurar un agujero no es reversibilidad, es reincidencia.

Revision ID: 0138_revoke_backfill_grants
Revises: 0137_partition_executions
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0138_revoke_backfill_grants"
down_revision: str | Sequence[str] | None = "0137_partition_executions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: La tabla de respaldo de la 0133. Nombre literal: es un artefacto histórico de
#: aquella migración, no una constante viva que puedan renombrar.
_BACKUP_TABLE = "approval_policy_backfill_0133"

#: Roles de APLICACIÓN. `migrations_user` NO está: es quien la escribe y quien la
#: lee al bajar, así que quitarle el acceso rompería el `downgrade` de la 0133.
_APPLICATION_ROLES = ("app_user", "service_user")


def upgrade() -> None:
    for role in _APPLICATION_ROLES:
        # `IF EXISTS` sobre el rol: una instalación puede no tener `service_user`
        # (se añadió en prod-14), y una migración no puede reventar por eso.
        op.execute(f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
                   AND to_regclass('public.{_BACKUP_TABLE}') IS NOT NULL THEN
                    EXECUTE 'REVOKE ALL ON TABLE public.{_BACKUP_TABLE} FROM {role}';
                END IF;
            END $$;
            """)


def downgrade() -> None:
    """No devuelve el permiso, a propósito.

    El `downgrade` de una migración deshace un CAMBIO DE ESQUEMA para poder
    volver atrás sin perder datos. Aquí lo que se deshizo fue una exposición, y
    reponerla no restaura ninguna capacidad que alguien echase de menos: la
    aplicación nunca leyó esta tabla. Un downgrade que reabre un agujero de
    aislamiento es peor que uno que no hace nada.
    """
    return None
