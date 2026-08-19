"""RLS de eje OWNER en las CINCO tablas del córtex que se quedaron sin ella (ADR 0156).

La migración **0125** le puso a `cortex_conversations` `ENABLE` + `FORCE` + una
policy `owner_user_id = app.user_id`, y dejó escrito en su propio docstring lo
que NO cerraba:

> «La conversación es el índice; el CONTENIDO del chat del owner vive en
> `cortex_turns`, y sus vecinas `cortex_identity`, `cortex_identity_history`,
> `cortex_affect_snapshots` y `cortex_curiosity_pursuits` tampoco tienen RLS.
> Ninguna tiene columna `tenant_id`, así que el meta-invariante no las mira.»

Esta migración cierra exactamente eso, con la decisión del ADR 0156 detrás.

## Qué estaba pasando de verdad

Las cinco tablas nacieron (migraciones 0092-0095) bajo una inferencia que parecía
razonable y no lo era: *el córtex no es un recurso de tenant ⇒ no lleva RLS*. De
«el eje no es el tenant» no se sigue «no hay eje», sino «el eje es otro». Lo que
quedaba en su lugar era un filtro `owner_user_id` escrito a mano en cada query.

Esa defensa es real —está verificada una por una, ver más abajo— pero es de
aplicación, no estructural, y detrás de ella no había NADA: `02-roles.sh` concede
a `app_user` (NOBYPASSRLS, el rol del tráfico normal de la api-server)
`SELECT/INSERT/UPDATE/DELETE` sobre **toda** tabla que cree Alembic, por
`ALTER DEFAULT PRIVILEGES`. Traducido: el día que un endpoint nuevo lea una tabla
del córtex con la sesión normal —o que una inyección lo haga por él— la base de
datos no tiene nada que oponer, y lo que se lee es la mente privada del System
Owner (su identidad, su estado afectivo, lo que está investigando y el texto
literal de sus conversaciones).

## Por qué la policy es por owner y no por tenant

Mismo razonamiento que la 0125, que aquí vale con más fuerza todavía: **estas
cinco tablas ni siquiera tienen `tenant_id`**. Una policy por tenant sería
imposible de escribir sin inventarse una columna, y si se inventase sería a la vez
demasiado permisiva (pertenecer al tenant bastaría para leer el hilo privado del
owner) y funcionalmente rota (`open_tenant_session` fija `app.tenant_id` al tenant
elegido en la request, no al de la conversación).

El predicado es el del patrón `session_owner_only` de la 0001, con el
`NULLIF(..., '')` que hace que una sesión que NO fija el GUC compare contra NULL y
vea CERO filas (fail-closed) en vez de reventar con un error de cast.

## Qué caminos leen y escriben estas tablas, y por qué ninguno se rompe

Verificado uno a uno con grep, no extrapolado (fichero:línea en el ADR 0156):

  * `routers/cortex.py` (12 aperturas) y `routers/cortex_voice.py` →
    `get_admin_sessionmaker()` → `API_SERVER_ADMIN_DATABASE_URL` → `migrations_user`;
  * `cortex/threads.py`, `affect_store.py`, `identity.py`, `self_context.py` y
    `voice_turn.py` no abren sesión: reciben la del llamante;
  * los workers `cortex_affect`, `cortex_curiosity`, `cortex_initiative`,
    `cortex_reflection` y `cortex_maintenance` → `WORKERS_DATABASE_URL`, que es
    `service_user` por defecto y `migrations_user` en el compose desplegado;
  * el `pg_dump` del backup → `WORKERS_BACKUP_DATABASE_URL` → `migrations_user`.

`service_user` y `migrations_user` son **BYPASSRLS**, y BYPASSRLS se salta la RLS
incluso con `FORCE` (medido contra este PostgreSQL 16 antes de escribir la 0125,
no leído de la documentación). O sea: para los consumidores de hoy esta policy es
inerte, incluido el backup —que si se saltase la RLS por ser el propietario sin
BYPASSRLS volcaría CERO filas del córtex y nadie lo notaría hasta el restore—. Lo
único que cambia es que el camino `app_user`, hoy sin usar para estas tablas y
completamente abierto, pasa a ser fail-closed.

## Lo que esta migración NO hace

  * **No renombra `cortex_conversations.tenant_id`.** El ADR 0156 descarta la
    sub-opción: el nombre no es el defecto, la ausencia de defensa estructural lo
    era, y renombrarlo a algo que no case con el patrón `%tenant_id` sacaría la
    tabla del descubrimiento del meta-invariante, que es estrictamente peor que
    estar dentro con una excepción justificada y visible en el diff.
  * **No toca `browse_sessions`** (0112), la tabla de navegación del córtex. Ya
    tiene `ENABLE` + `FORCE` + policy por tenant, y sus filas del córtex llevan
    `tenant_id IS NULL`, así que para `app_user` ya son invisibles (fail-closed).
    Añadirle una policy por owner sería AÑADIR permisividad —las policies
    permisivas se combinan con OR—, no quitarla.

Revision ID: 0140_cortex_owner_rls
Revises: 0139_executions_steps_rollup
Create Date: 2026-08-19
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0140_cortex_owner_rls"
down_revision: str | Sequence[str] | None = "0139_executions_steps_rollup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: Las cinco tablas del córtex sin defensa estructural hasta hoy. La sexta,
#: `cortex_conversations`, ya la protegió la 0125 y NO se toca aquí.
_OWNER_SCOPED_TABLES: tuple[str, ...] = (
    "cortex_turns",
    "cortex_identity",
    "cortex_identity_history",
    "cortex_affect_snapshots",
    "cortex_curiosity_pursuits",
)

#: Mismo predicado que `session_owner_only` (0001) y `cortex_conversations_owner_only`
#: (0125). El `NULLIF(..., '')` es lo que hace fail-closed a la sesión sin GUC.
_OWNER_PREDICATE = "owner_user_id = NULLIF(current_setting('app.user_id', true), '')::uuid"


# Las sentencias se escriben LITERALES, una por tabla, en vez de generarse en un
# bucle con f-strings sobre `_OWNER_SCOPED_TABLES`. Es a propósito: el detector
# estático de `tests/security/test_pentest_findings.py` busca `ALTER TABLE <nombre>
# ENABLE ROW LEVEL SECURITY` en el texto de las migraciones, y un bucle deja en el
# fichero `ALTER TABLE {table} …`, que no nombra a nadie. Una migración de
# seguridad que hay que ejecutar para saber qué protege es peor migración.
_UPGRADE_STATEMENTS: tuple[str, ...] = (
    "ALTER TABLE cortex_turns ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE cortex_turns FORCE ROW LEVEL SECURITY",
    "CREATE POLICY cortex_turns_owner_only ON cortex_turns FOR ALL"
    f" USING ({_OWNER_PREDICATE}) WITH CHECK ({_OWNER_PREDICATE})",
    "ALTER TABLE cortex_identity ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE cortex_identity FORCE ROW LEVEL SECURITY",
    "CREATE POLICY cortex_identity_owner_only ON cortex_identity FOR ALL"
    f" USING ({_OWNER_PREDICATE}) WITH CHECK ({_OWNER_PREDICATE})",
    "ALTER TABLE cortex_identity_history ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE cortex_identity_history FORCE ROW LEVEL SECURITY",
    "CREATE POLICY cortex_identity_history_owner_only ON cortex_identity_history FOR ALL"
    f" USING ({_OWNER_PREDICATE}) WITH CHECK ({_OWNER_PREDICATE})",
    "ALTER TABLE cortex_affect_snapshots ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE cortex_affect_snapshots FORCE ROW LEVEL SECURITY",
    "CREATE POLICY cortex_affect_snapshots_owner_only ON cortex_affect_snapshots FOR ALL"
    f" USING ({_OWNER_PREDICATE}) WITH CHECK ({_OWNER_PREDICATE})",
    "ALTER TABLE cortex_curiosity_pursuits ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE cortex_curiosity_pursuits FORCE ROW LEVEL SECURITY",
    "CREATE POLICY cortex_curiosity_pursuits_owner_only ON cortex_curiosity_pursuits FOR ALL"
    f" USING ({_OWNER_PREDICATE}) WITH CHECK ({_OWNER_PREDICATE})",
)


def upgrade() -> None:
    for statement in _UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Deshace exactamente lo que hizo el upgrade, y nada más.

    Las cinco tablas vuelven al estado en que las dejaron las migraciones
    0092-0095: sin policy, sin `FORCE` y sin `ENABLE`. No se toca
    `cortex_conversations`, cuya RLS es de la 0125 y sobrevive a este downgrade.
    """
    for table in reversed(_OWNER_SCOPED_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_owner_only ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
