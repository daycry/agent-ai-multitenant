"""Devuelve `stack_exec` a las COPIAS de tenant que se quedaron con la puerta mala.

## Qué se rompió

Ningún agente built-in de plataforma repartía `stack_exec`. Un run real de un
proyecto CodeIgniter 4 pidió a la primera lo correcto —
``stack_exec("composer create-project codeigniter4/appstarter .")``— recibió
``tool stack_exec not allowed in this mode``, y se pasó 24 llamadas a
`shell_exec` buscando PHP dentro de su propio sandbox hasta agotar reintentos:
2,22 USD y 62,2k tokens sin instalar nada.

El seed ya está arreglado, pero **un seed no toca los datos de tenant**. Los
equipos ya adoptados son copias (`forked_from_agent_id` → un `global_builtin`),
viven en la tabla `agents` de cada tenant y no los reescribe ningún re-seed. Sin
esta migración, cada equipo clonado seguiría atascado en la misma trampa
mientras el catálogo de fábrica ya está sano — que es exactamente la situación
al revés de la que había en agosto, cuando las copias viejas estaban MEJOR que su
original porque un humano las había parcheado a mano dos veces, en bloque, sin
dejar nada escrito.

## La condición que hace esto seguro: sólo a quien YA tiene `shell_exec`

La regla del enunciado es dura y correcta: si un tenant quitó una tool a
propósito, una migración no puede devolvérsela. Y **no hay registro** de qué
quitó nadie: `agent_tools` no tiene `deleted_at` ni auditoría de cambios, así que
«nunca la tuvo» y «la tuvo y la quitó» son indistinguibles desde los datos.

Por eso la migración no reparte por rol a secas, sino sólo a los agentes que ya
tienen concedido `shell_exec`, y el argumento es de AUTORIDAD, no estadístico:

  * `allowed_commands` del proyecto es UNA lista para las DOS puertas (ADR 0162).
  * Quien ya tiene `shell_exec` ya está autorizado a ejecutar exactamente esos
    comandos.
  * `stack_exec` (ADR 0093) no amplía ese conjunto ni un comando: lo único que
    cambia es DÓNDE corre — el runtime-template del proyecto, donde el binario
    existe, en vez del sandbox fino, donde no.

O sea que sobre esa población la migración **no puede conceder autoridad nueva**;
sólo deja de mandar el comando autorizado por la puerta que no lo puede servir.
Un agente al que su tenant dejó SIN ninguna puerta de ejecución no se toca: ahí
conceder `stack_exec` sí sería autoridad nueva, y una migración no es el sitio
para decidirlo. Esos casos se ven en el diff de fork (`GET /agents/{id}/diff`,
que ya expone las capacidades de las dos partes) y los resuelve un humano.

Dos filtros más, por las mismas razones que en el seed:

  * Sólo COPIAS de un built-in (`forked_from_agent_id` → `scope =
    'global_builtin'`). Un agente que un tenant creó desde cero es suyo entero.
  * Sólo roles que ejecutan el toolchain. El `reviewer` queda FUERA a propósito:
    el ADR 0095 le monta el worktree del implementador en READ-ONLY, pero
    `stack_exec` no corre en el sandbox del agente — el worker lo lanza sobre ese
    mismo worktree montado en escritura. Dárselo reabriría por la puerta de atrás
    el aislamiento que aquel ADR firmó. La lista va copiada aquí, no importada:
    ninguna migración de este repo importa código de la app.

## Reversible de verdad

Cada fila insertada se anota en `agent_tools_backfill_0145` y el `downgrade`
borra EXACTAMENTE esas. Reconstruir el conjunto por inferencia no valdría: tras
el `upgrade`, un grant que puso esta migración y uno que puso un administrador
del tenant al día siguiente son idénticos, y un `downgrade` que se llevara los
dos destruiría trabajo ajeno. Es el mismo patrón de la 0133.

La tabla de respaldo nace SIN acceso para la aplicación (`REVOKE`), aprendido de
la 0138: los default privileges de `docker/postgres/init/02-roles.sh` alcanzan a
toda tabla que Alembic cree, así que una tabla de bookkeeping sin `tenant_id` ni
RLS nacería legible cross-tenant si no se le quita el permiso aquí mismo.

Idempotente: re-ejecutar no inserta nada la segunda vez (el `NOT EXISTS` ya no
encuentra a nadie) y el respaldo tolera el conflicto.

Revision ID: 0145_stack_exec_builtin_forks
Revises: 0144_timestamps_not_null
Create Date: 2026-08-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0145_stack_exec_builtin_forks"
down_revision: str | Sequence[str] | None = "0144_timestamps_not_null"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Tabla de respaldo. La escribe el `upgrade`, la lee y la borra el `downgrade`.
#: Es el ÚNICO sitio donde consta qué filas puso esta migración.
BACKFILL_TABLE = "agent_tools_backfill_0145"

_BACKFILL_COMMENT = (
    "Respaldo de la 0145: los grants de stack_exec que esta migracion anadio a "
    "copias de agentes built-in. El downgrade borra exactamente estos. Sin ella la "
    "migracion es irreversible."
)

#: Roles cuyo trabajo INCLUYE ejecutar el toolchain del proyecto. Espejo literal
#: de `api_server.seeds.builtin_role_capabilities.ROLES_THAT_EXECUTE_TOOLCHAIN`;
#: va copiado porque una migración no importa código de la app (misma decisión
#: que la 0133). `reviewer`, `project_manager`, `technical_writer` y `researcher`
#: quedan fuera — ver la cabecera.
_EXECUTING_ROLES = (
    "architect",
    "backend_dev",
    "frontend_dev",
    "qa",
    "devops",
    "security",
    "specialist",
)

#: Roles de APLICACIÓN a los que se retira el acceso al respaldo. `migrations_user`
#: NO está: es quien la escribe y quien la lee al bajar.
_APPLICATION_ROLES = ("app_user", "service_user")

_ROLE_LIST_SQL = ", ".join(f"'{role}'" for role in _EXECUTING_ROLES)


def _revoke_backfill_from_app() -> None:
    for role in _APPLICATION_ROLES:
        # `IF EXISTS` sobre el rol: una instalación puede no tener `service_user`
        # (llegó en prod-14) y una migración no puede reventar por eso.
        op.execute(f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
                   AND to_regclass('public.{BACKFILL_TABLE}') IS NOT NULL THEN
                    EXECUTE 'REVOKE ALL ON TABLE public.{BACKFILL_TABLE} FROM {role}';
                END IF;
            END $$;
            """)


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {BACKFILL_TABLE} (
            agent_id   uuid        NOT NULL PRIMARY KEY,
            tool_id    uuid        NOT NULL,
            granted_at timestamptz NOT NULL DEFAULT now()
        )
        """)
    op.execute(f"COMMENT ON TABLE {BACKFILL_TABLE} IS '{_BACKFILL_COMMENT}'")
    _revoke_backfill_from_app()

    # `tenant_id` va explícito aunque el trigger `agent_tools_set_tenant_id`
    # (migración 0124) lo derive igualmente del agente: el trigger ABORTA si el
    # valor contradice al del padre, así que pasarlo convierte un error de
    # razonamiento sobre multi-tenancy en un fallo ruidoso en vez de una fila
    # colgada del tenant equivocado.
    op.execute(f"""
        WITH stack_tool AS (
            SELECT id FROM tools
             WHERE name = 'stack_exec' AND is_builtin = true AND deleted_at IS NULL
             LIMIT 1
        ),
        targets AS (
            SELECT a.id AS agent_id, a.tenant_id AS tenant_id, s.id AS tool_id
              FROM agents a
              JOIN agents src ON src.id = a.forked_from_agent_id
             CROSS JOIN stack_tool s
             WHERE a.deleted_at IS NULL
               AND src.scope = 'global_builtin'
               AND a.role IN ({_ROLE_LIST_SQL})
               AND EXISTS (
                     SELECT 1
                       FROM agent_tools g
                       JOIN tools shell ON shell.id = g.tool_id
                      WHERE g.agent_id = a.id
                        AND shell.name = 'shell_exec'
                        AND shell.is_builtin = true
                        AND shell.deleted_at IS NULL
                   )
               AND NOT EXISTS (
                     SELECT 1 FROM agent_tools g2
                      WHERE g2.agent_id = a.id AND g2.tool_id = s.id
                   )
        ),
        inserted AS (
            INSERT INTO agent_tools (agent_id, tool_id, tenant_id)
            SELECT agent_id, tool_id, tenant_id FROM targets
            ON CONFLICT (agent_id, tool_id) DO NOTHING
            RETURNING agent_id, tool_id
        )
        INSERT INTO {BACKFILL_TABLE} (agent_id, tool_id)
        SELECT agent_id, tool_id FROM inserted
        ON CONFLICT (agent_id) DO NOTHING
        """)


def downgrade() -> None:
    """Quita SÓLO lo que puso el `upgrade`, fila a fila desde el respaldo.

    No se recalcula el conjunto: después del `upgrade`, un grant que puso esta
    migración es indistinguible de uno que puso un administrador del tenant, y
    borrar los dos sería destruir trabajo ajeno en nombre de la reversibilidad.
    """
    op.execute(f"""
        DELETE FROM agent_tools g
         USING {BACKFILL_TABLE} b
         WHERE g.agent_id = b.agent_id
           AND g.tool_id = b.tool_id
        """)
    op.execute(f"DROP TABLE IF EXISTS {BACKFILL_TABLE}")
