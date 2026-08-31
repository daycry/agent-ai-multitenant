"""Lleva `move_file` a las COPIAS de tenant que se quedaron sin la puerta buena.

## Qué se rompió, medido

Proyecto «Hello World CI4 v3», tenant mediapro, 2026-08-31. `composer
create-project .` exige un directorio COMPLETAMENTE vacío. En el paso 31 del
segundo run el agente llegó SOLO a la solución correcta —instalar en `tmpci/` y
mover el resultado a su sitio— y no pudo ejecutarla: la familia `file` era
exactamente read/write/delete/list, así que de los TRES pasos de su plan el
único que la plataforma sabía hacer era el destructivo. Cuatro pasos después
borró `app/` entera: 85 ficheros que eran el deliverable ya commiteado de la
tarea anterior.

`move_file` (ADR 0164, 2026-08-31) cierra ese hueco, y el catálogo de fábrica ya
la reparte a los nueve roles que escriben en el workspace. Pero **un seed no toca
los datos de tenant**: los equipos ya adoptados son COPIAS
(`forked_from_agent_id` → un `global_builtin`), viven en la tabla `agents` de
cada tenant y no los reescribe ningún re-seed. Sin esta migración, el proyecto
donde ocurrió el incidente —que corre sobre copias adoptadas— seguiría sin poder
mover y seguiría intentando vaciar. O sea: el arreglo entero no llegaría
justamente a quien lo pagó.

## La condición que hace esto seguro: sólo a quien YA puede escribir Y borrar

La regla es la misma de la 0145 y sigue siendo dura y correcta: si un tenant
quitó una tool a propósito, una migración no puede devolvérsela. Y **no hay
registro** de qué quitó nadie — `agent_tools` no tiene `deleted_at` ni auditoría
de cambios, así que «nunca la tuvo» y «la tuvo y la quitó» son indistinguibles
desde los datos.

Por eso la migración no reparte por rol a secas, sino sólo a los agentes que ya
tienen concedidas **las dos** puertas de escritura de ficheros que existían
antes de ella, y el argumento es de AUTORIDAD, no estadístico:

  * Mover es exactamente escribir y borrar, en un paso. `move_file(a, b)` deja
    bytes en `b` —donde no había nada— y retira `a` de su sitio.
  * Quien ya tiene `write_file` ya está autorizado a poner bytes en cualquier
    ruta bajo su worktree; quien ya tiene `delete_file` ya está autorizado a
    retirar de su sitio cualquier ruta bajo su worktree, árbol entero incluido.
  * Sobre quien tiene las dos, `move_file` no añade ni un permiso: es la
    composición de dos que ya se le concedieron. De hecho el propio catálogo lo
    dice al revés y por eso la tool existe — `write_file` + `delete_file` **ya
    reproducen un move**, fichero a fichero y con una ventana en la que el
    trabajo sólo existe en el contexto del modelo.

**Por qué la PAREJA y no sólo `delete_file`.** Se consideró usar `delete_file`
como puerta equivalente, por analogía con la 0145 (donde una sola tool,
`shell_exec`, bastaba porque `stack_exec` no ampliaba el conjunto de comandos
autorizados ni en uno). Aquí no vale, y falla por los dos lados:

  * A un agente con `delete_file` y SIN `write_file`, `move_file` le regala
    poner bytes en una ruta donde no había nada. Borrar no sabe hacer eso.
  * A un agente con `write_file` y SIN `delete_file`, le regala retirar un árbol
    entero de su sitio de una vez. `move_file("app", "app.old")` deja `app/`
    fuera igual que borrarla.

O sea que media pareja SÍ es autoridad nueva, y una migración no es el sitio
para decidir concederla. Esos casos se ven en el diff de fork
(`GET /agents/{id}/diff`, que ya expone las capacidades de las dos partes) y los
resuelve un humano.

Y una nota sobre la dirección del riesgo, porque conviene tenerla escrita: sobre
la población elegida `move_file` no sólo no amplía autoridad, sino que llega con
una guarda que la pareja no tenía — ni ella ni `delete_file` recursivo pueden
destruir un árbol de PRIMER NIVEL versionado en la rama (`AGENT_TRACKED_PATHS`,
ADR 0164). El camino que esta migración abre está MÁS protegido que el que ya
estaba abierto.

## Los mismos dos filtros del precedente, por las mismas razones

  * Sólo COPIAS de un built-in (`forked_from_agent_id` → `scope =
    'global_builtin'`). Un agente que un tenant creó desde cero es suyo entero.
  * Sólo roles que ESCRIBEN ficheros. El `reviewer` queda FUERA a propósito: el
    ADR 0095 le monta el worktree del implementador en READ-ONLY, así que un
    `move_file` desde ahí rebota con EROFS — y rebotar es la trampa del ADR
    0162, porque el agente no distingue «no me dejan» de «me equivoqué de ruta»
    y reintenta. La lista va copiada aquí, no importada: ninguna migración de
    este repo importa código de la app.

Ese segundo filtro **no es defensivo en el vacío**, y es la diferencia práctica
con la 0145: hasta el 2026-08-30 el seed del equipo CodeIgniter le daba
`write-file` y `delete-file` a su reviewer mientras `ROLE_DEFAULT_TOOLS` le daba
`_READ`. Las copias adoptadas antes de esa fecha tienen las dos puertas, así que
sin el filtro por rol la condición de la pareja sola le concedería `move_file` a
exactamente el agente que el ADR 0095 quiere sin escritura.

**Y la lista de roles NO es la de la 0145.** Aquélla repartía una puerta de
EJECUCIÓN y su población eran los roles que corren el toolchain
(`ROLES_THAT_EXECUTE_TOOLCHAIN`, siete). Ésta reparte una puerta de ESCRITURA:
el `technical_writer` y el `researcher` escriben ficheros y no ejecutan nada, de
modo que reutilizar aquella lista los habría dejado fuera del arreglo. Son nueve
— todos los roles del mapa menos el `project_manager` (`_READ`) y el `reviewer`.

## Por qué esta migración además CREA la fila del catálogo (y la 0145 no)

Aquí hay una diferencia de orden con el precedente que decide si esta migración
sirve de algo o no hace nada. La 0145 repartía `stack_exec`, cuya fila de `tools`
llevaba sembrada desde el ADR 0093: buscarla por nombre siempre encontraba algo.
`move_file` es una fila NUEVA, del mismo día que esta migración, y el orden de
arranque es inamovible:

    one-shot `migrations` (alembic upgrade head)
        → api-server arranca
            → `seed_builtin_tools` inserta `move_file` en `tools`

Lo fija el compose (`depends_on: {migrations: service_completed_successfully}`) y
lo vuelve a exigir `bootstrap/database.py`, que se niega a sembrar si las
revisiones esperadas no están aplicadas. O sea que **cuando esta migración corre,
la tool todavía no existe**. Una versión que se limitara a buscarla por nombre
encontraría cero filas, el `CROSS JOIN` se quedaría vacío, no insertaría nada, y
`alembic_version` la marcaría aplicada para siempre: un no-op silencioso que deja
el arreglo sin llegar justo al proyecto que pagó el incidente. Es el §4 de
`docs/03-guides/verificar-antes-de-implementar.md` — una guarda que pasa vacía —
y no se ve en los tests si el banco siembra el catálogo antes de migrar.

Por eso el `upgrade` crea la fila si falta, con el `id` DETERMINISTA del catálogo
(`uuid5(TOOL_SEED_NAMESPACE, "tool:move-file")`). Ese id es el que usa
`seed_builtin_tools`, que hace `ON CONFLICT (id) DO UPDATE`, así que en el
siguiente arranque el seed COMPLETA esta fila en vez de crear una gemela — que
además chocaría con el índice único parcial `uq_tools_tenant_name`.

La fila nace MÍNIMA a propósito: `description`, `input_schema` y `output_schema`
se quedan sin poner. La definición del catálogo vive en `builtin_tools.py` y
copiarla aquí sería la cuarta aparición del defecto que ese módulo lleva
documentado —dos declaraciones del mismo hecho, ninguna derivada—, con el
agravante de que una migración no se vuelve a ejecutar y la copia envejecería
congelada. El seed la completa antes de que el api-server sirva una sola
petición, porque siembra en el arranque.

Y `description IS NULL` hace doble servicio: es exactamente la marca de «esta
fila sigue como la dejó el `upgrade`, el seed no la ha tocado», que es lo que el
`downgrade` necesita para saber si la fila es suya. Dos cosas que NO hace por si
alguien busca aquí el caso: no resucita una `move_file` que la plataforma haya
retirado a propósito (soft-delete, como hizo la 0122 con los `run_*` — el
`ON CONFLICT (id) DO NOTHING` la deja borrada y entonces no hay a quién colgar
los grants), y no toca una fila que ya sembró el seed.

## Reversible de verdad

Cada fila insertada se anota en `agent_tools_backfill_0146` y el `downgrade`
borra EXACTAMENTE esas. Reconstruir el conjunto por inferencia no valdría: tras
el `upgrade`, un grant que puso esta migración y uno que puso un administrador
del tenant al día siguiente son idénticos, y un `downgrade` que se llevara los
dos destruiría trabajo ajeno. Es el mismo patrón de la 0133 y de la 0145.

La tabla de respaldo nace SIN acceso para la aplicación (`REVOKE`), aprendido de
la 0138: los default privileges de `docker/postgres/init/02-roles.sh` alcanzan a
toda tabla que Alembic cree, así que una tabla de bookkeeping sin `tenant_id` ni
RLS nacería legible cross-tenant si no se le quita el permiso aquí mismo.

La fila de catálogo se deshace con la misma cautela y sin respaldo porque no le
hace falta: se borra sólo si sigue con `description IS NULL` —o sea, tal como la
dejó el `upgrade`— y si NO queda ningún `agent_tools` apuntándola después de
retirar los grants del respaldo. Con un solo grant ajeno vivo, la fila se queda:
llevársela arrastraría ese grant por la clave ajena.

Idempotente: re-ejecutar no inserta nada la segunda vez (el `NOT EXISTS` ya no
encuentra a nadie) y el respaldo tolera el conflicto.

Revision ID: 0146_move_file_builtin_forks
Revises: 0145_stack_exec_builtin_forks
Create Date: 2026-08-31
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0146_move_file_builtin_forks"
down_revision: str | Sequence[str] | None = "0145_stack_exec_builtin_forks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Tabla de respaldo. La escribe el `upgrade`, la lee y la borra el `downgrade`.
#: Es el ÚNICO sitio donde consta qué filas puso esta migración.
BACKFILL_TABLE = "agent_tools_backfill_0146"

_BACKFILL_COMMENT = (
    "Respaldo de la 0146: los grants de move_file que esta migracion anadio a "
    "copias de agentes built-in. El downgrade borra exactamente estos. Sin ella la "
    "migracion es irreversible."
)

#: Roles cuyo trabajo INCLUYE dejar ficheros escritos en el workspace. Espejo de
#: los roles a los que `api_server.seeds.builtin_role_capabilities.ROLE_DEFAULT_TOOLS`
#: reparte `move-file`; va copiado porque una migración no importa código de la
#: app (misma decisión que la 0133 y la 0145). `project_manager` y `reviewer`
#: quedan fuera — ver la cabecera.
_WRITING_ROLES = (
    "architect",
    "backend_dev",
    "frontend_dev",
    "qa",
    "devops",
    "security",
    "specialist",
    "researcher",
    "technical_writer",
)

#: Las puertas de escritura de ficheros que ya existían antes de `move_file`, por
#: su `tools.name`. La población de esta migración es quien tiene TODAS: sobre
#: ella mover no concede autoridad nueva porque es su composición. Media pareja
#: no basta — el argumento entero está en la cabecera.
_REQUIRED_TOOLS = ("delete_file", "write_file")

#: Roles de APLICACIÓN a los que se retira el acceso al respaldo. `migrations_user`
#: NO está: es quien la escribe y quien la lee al bajar.
_APPLICATION_ROLES = ("app_user", "service_user")

#: El `id` de `move_file` en el catálogo, que es DETERMINISTA:
#: ``uuid5(TOOL_SEED_NAMESPACE, "tool:move-file")``. Va literal porque una
#: migración no importa código de la app; un test unitario lo recalcula desde las
#: constantes del seed y se pone rojo si alguna vez dejan de coincidir — si no,
#: el seed crearía una fila gemela y estos grants colgarían de la equivocada.
_MOVE_TOOL_ID = "bf961ec4-edc8-545f-8eb8-d9c3365c9ea7"
_MOVE_TOOL_NAME = "move_file"

#: `PLATFORM_TENANT_ID`. El catálogo built-in cuelga del tenant de plataforma.
_PLATFORM_TENANT_ID = "00000000-0000-0000-0000-000000000001"

_ROLE_LIST_SQL = ", ".join(f"'{role}'" for role in _WRITING_ROLES)

#: Un `EXISTS` por cada tool exigida, encadenados con `AND`. Se construye así, y
#: no con un `IN (...)` y un `count(*) = 2`, porque la conjunción es literalmente
#: la condición de seguridad: con un `IN`, «tiene las dos» se degrada a «tiene
#: alguna», que es justo la población a la que mover SÍ le concede autoridad
#: nueva. Un test unitario cuenta los `EXISTS` para que no se pueda colapsar en
#: uno sin ponerse rojo.
_REQUIRED_TOOLS_SQL = "\n".join(
    f"""               AND EXISTS (
                     SELECT 1
                       FROM agent_tools held
                       JOIN tools req ON req.id = held.tool_id
                      WHERE held.agent_id = a.id
                        AND req.name = '{tool}'
                        AND req.is_builtin = true
                        AND req.deleted_at IS NULL
                   )"""
    for tool in _REQUIRED_TOOLS
)


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

    # La fila del catálogo, si el seed todavía no ha corrido — que es el caso en
    # el despliegue REAL de esta migración (ver la cabecera). Mínima a propósito:
    # `description` se queda NULL y es además la marca que lee el `downgrade`.
    #
    # El `WHERE NOT EXISTS` mira por (tenant, nombre) y no por `id`: cubre el caso
    # raro de que alguien tenga una `move_file` VIVA con otro id, donde insertar
    # reventaría contra el índice único parcial `uq_tools_tenant_name`. El
    # `ON CONFLICT (id)` cubre el otro: una fila con ESTE id ya presente —
    # sembrada, o retirada con soft-delete, que se queda retirada.
    op.execute(f"""
        INSERT INTO tools (
            id, tenant_id, name, category, implementation_type, security_level, is_builtin
        )
        SELECT '{_MOVE_TOOL_ID}'::uuid, '{_PLATFORM_TENANT_ID}'::uuid,
               '{_MOVE_TOOL_NAME}', 'file', 'builtin', 'sandboxed', true
         WHERE NOT EXISTS (
                 SELECT 1 FROM tools
                  WHERE tenant_id = '{_PLATFORM_TENANT_ID}'::uuid
                    AND name = '{_MOVE_TOOL_NAME}'
                    AND deleted_at IS NULL
               )
        ON CONFLICT (id) DO NOTHING
        """)

    # `tenant_id` va explícito aunque el trigger `agent_tools_set_tenant_id`
    # (migración 0124) lo derive igualmente del agente: el trigger ABORTA si el
    # valor contradice al del padre, así que pasarlo convierte un error de
    # razonamiento sobre multi-tenancy en un fallo ruidoso en vez de una fila
    # colgada del tenant equivocado.
    op.execute(f"""
        WITH move_tool AS (
            SELECT id FROM tools
             WHERE name = '{_MOVE_TOOL_NAME}' AND is_builtin = true AND deleted_at IS NULL
             LIMIT 1
        ),
        targets AS (
            SELECT a.id AS agent_id, a.tenant_id AS tenant_id, m.id AS tool_id
              FROM agents a
              JOIN agents src ON src.id = a.forked_from_agent_id
             CROSS JOIN move_tool m
             WHERE a.deleted_at IS NULL
               AND src.scope = 'global_builtin'
               AND a.role IN ({_ROLE_LIST_SQL})
{_REQUIRED_TOOLS_SQL}
               AND NOT EXISTS (
                     SELECT 1 FROM agent_tools absent
                      WHERE absent.agent_id = a.id AND absent.tool_id = m.id
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

    # Y la fila del catálogo, SÓLO si sigue siendo la que creó el `upgrade`. Las
    # dos condiciones son la misma cautela que el respaldo, aplicada a lo que no
    # cabía en él:
    #
    #   * `description IS NULL` — el seed rellena ese campo en cada arranque, así
    #     que si tiene texto la fila ya es del catálogo, la afirma el CÓDIGO y
    #     borrarla sería deshacer trabajo que esta migración no hizo.
    #   * `NOT EXISTS (agent_tools)` — va DESPUÉS del borrado de arriba, de modo
    #     que lo que quede apuntando a la tool son grants de otros. Con uno solo
    #     vivo, la fila se queda: borrarla arrastraría ese grant por la FK.
    op.execute(f"""
        DELETE FROM tools t
         WHERE t.id = '{_MOVE_TOOL_ID}'::uuid
           AND t.name = '{_MOVE_TOOL_NAME}'
           AND t.description IS NULL
           AND NOT EXISTS (SELECT 1 FROM agent_tools g WHERE g.tool_id = t.id)
        """)

    op.execute(f"DROP TABLE IF EXISTS {BACKFILL_TABLE}")
