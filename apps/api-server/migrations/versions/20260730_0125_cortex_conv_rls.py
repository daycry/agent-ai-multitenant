"""RLS de `cortex_conversations` (eje OWNER) + el `FORCE` que faltaba en tres tablas.

El meta-invariante `tests/integration/test_rls_invariant.py`, la primera vez que
se ejecutó (2026-07-30), destapó cuatro desviaciones reales de cobertura RLS.
Esta migración cierra las cuatro.

## 1. `cortex_conversations`: tenía `tenant_id` y CERO protección

Ni `relrowsecurity`, ni `FORCE`, ni una sola policy. Medido antes de tocar nada:
un `app_user` (NOBYPASSRLS) veía los hilos de TODOS los owners y podía INSERTAR
un hilo a nombre de otro owner. La migración 0092 lo había declarado así a
propósito —«tenant-less sobre BYPASSRLS… el aislamiento es un filtro
`owner_user_id` explícito en TODO SQL (defensa en profundidad, **sin RLS de
respaldo**)»— y ese «sin RLS de respaldo» es exactamente lo que se añade aquí.

### Por qué la policy NO es `tenant_id = app.tenant_id`

Porque el tenant no es el eje de autorización de esta tabla, y escribirlo en la
base de datos sería consagrar el eje equivocado. Palabra por palabra del modelo
(`db/cortex.py`), `tenant_id` es «the physical discriminator the owner's memory
needs — NOT an authorisation axis»: se resuelve una vez como la membresía activa
MÁS ANTIGUA del owner (`resolve_cortex_tenant_id`, Decisión D1 del ADR 0074).
Una policy por tenant tendría dos defectos, uno de seguridad y uno funcional:

  * **Demasiado permisiva**: afirmaría que pertenecer al tenant A basta para leer
    el hilo PRIVADO del System Owner. El `tenant_admin` de A no debería verlo.
  * **Silenciosamente vacía**: `open_tenant_session` fija `app.tenant_id` al
    tenant ELEGIDO en la request, no al de la conversación. Un System Owner que
    entrase con contexto del tenant B (o sin contexto) dejaría de ver su propio
    historial — el fallo que hay que evitar por encima de la desviación que se
    arregla.

La policy correcta es la del patrón `session_owner_only` de la migración 0001:
`owner_user_id = app.user_id`. Es estrictamente MÁS restrictiva que la de tenant
y no puede dejar al owner a oscuras, porque `open_tenant_session` fija
`app.user_id` SIEMPRE, en sus dos variantes de sesión (app_user y admin).

### Qué caminos leen y escriben la tabla, y por qué ninguno se rompe

Verificado uno a uno con grep, no extrapolado:

  * `routers/cortex.py` (11 aperturas de sesión) y `routers/cortex_voice.py` →
    `get_admin_sessionmaker()` → `API_SERVER_ADMIN_DATABASE_URL` →
    `migrations_user`;
  * `cortex/threads.py` y `cortex/voice_turn.py` no abren sesión: reciben la del
    llamante (los dos routers de arriba, y el worker de iniciativa);
  * workers `cortex_initiative`, `cortex_affect`, `cortex_curiosity` y
    `cortex_reflection` → `WORKERS_DATABASE_URL` → `migrations_user`.

`migrations_user` es BYPASSRLS, y **BYPASSRLS se salta la RLS incluso con
`FORCE`**. Eso no es una lectura de la documentación: se midió contra este
PostgreSQL 16.13 antes de escribir la migración (tabla con ENABLE + FORCE +
policy por tenant, sin fijar el GUC → el propietario BYPASSRLS veía 2 de 2 filas
y podía insertar; `app_user` veía 0). Conclusión: para los consumidores actuales
esta policy es inerte, y lo único que cambia es que el camino `app_user` —hoy sin
usar para esta tabla, y completamente abierto— pasa a ser fail-closed.

### Lo que esta migración NO cierra

La conversación es el índice; el CONTENIDO del chat del owner vive en
`cortex_turns`, y sus vecinas `cortex_identity`, `cortex_identity_history`,
`cortex_affect_snapshots` y `cortex_curiosity_pursuits` tampoco tienen RLS.
Ninguna tiene columna `tenant_id`, así que el meta-invariante no las mira y
quedan fuera del alcance de este arreglo. Cerrar ese hueco de verdad pide la
misma policy owner-only en esas cinco tablas, que es un cambio de postura sobre
todo el subsistema del córtex y contradice explícitamente el diseño escrito en
el ADR 0074 → se reporta como hallazgo para que se decida con un ADR, no se
decide aquí de tapadillo.

## 2. Tres tablas con RLS y policy pero sin `FORCE`

`tenant_settings` (0023), `review_sessions` (0024) y `task_audit_events` (0025)
se escribieron con `ENABLE ROW LEVEL SECURITY` + su `tenant_isolation`, pero sin
`ALTER TABLE … FORCE`. Sin `FORCE`, el PROPIETARIO de la tabla se salta sus
propias policies. Hoy es inocuo porque el propietario (`migrations_user`) es
además BYPASSRLS, así que ya se las saltaba por otra vía: añadir `FORCE` es un
no-op medible sobre los cuatro roles actuales. Se añade igualmente porque es la
postura que tienen las otras ~60 tablas tenant-scoped y la que empieza a valer el
día que el propietario deje de ser BYPASSRLS, que es la dirección declarada en
`docker/postgres/init/04-service-role.sql`.

No se toca su policy: sigue siendo la de tenant, que ahí SÍ es el eje correcto.

## 3. `marketplace_sources` no se toca

Tiene `owner_tenant_id` y ninguna RLS, y es deliberado: su docstring declara que
una source es un endpoint de registro de PLATAFORMA. Queda catalogada en la
allowlist del meta-test, no arreglada.

Revision ID: 0125_cortex_conv_rls
Revises: 0124_junction_tenant_rls
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0125_cortex_conv_rls"
down_revision: str | Sequence[str] | None = "0124_junction_tenant_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Tablas que ya tenían ENABLE + policy y solo les faltaba FORCE. NO se les toca
# la policy: el eje tenant es el correcto en las tres.
_FORCE_ONLY: tuple[str, ...] = (
    "tenant_settings",
    "review_sessions",
    "task_audit_events",
)

# Mismo predicado que `session_owner_only` (0001): `NULLIF(..., '')` para que una
# sesión que NO fija el GUC compare contra NULL y no vea NADA (fail-closed), en
# vez de reventar con un error de cast desde la cadena vacía.
_OWNER_PREDICATE = "owner_user_id = NULLIF(current_setting('app.user_id', true), '')::uuid"


def upgrade() -> None:
    # --- 1. cortex_conversations: ENABLE + FORCE + policy owner-only ---------
    op.execute("ALTER TABLE cortex_conversations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE cortex_conversations FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY cortex_conversations_owner_only ON cortex_conversations FOR ALL"
        f" USING ({_OWNER_PREDICATE})"
        f" WITH CHECK ({_OWNER_PREDICATE})"
    )

    # --- 2. El FORCE que faltaba en las tres tablas de 0023/0024/0025 -------
    for table in _FORCE_ONLY:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    """Deshace exactamente lo que hizo el upgrade, y nada más.

    En particular NO apaga la RLS de las tres tablas de `_FORCE_ONLY`: su
    `ENABLE` y su policy vienen de las migraciones 0023/0024/0025 y sobreviven
    al downgrade. Lo único que se revierte ahí es el `FORCE`.
    """
    for table in reversed(_FORCE_ONLY):
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS cortex_conversations_owner_only ON cortex_conversations")
    op.execute("ALTER TABLE cortex_conversations NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE cortex_conversations DISABLE ROW LEVEL SECURITY")
