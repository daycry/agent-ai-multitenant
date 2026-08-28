"""La mitad de base de datos del one-shot: pre-flight, tenant inicial y catálogo.

## El rol: `service_user`, y la forma de acertar es no elegirlo

Los nombres se parecen peligrosamente, así que la cadena entera, escrita:

===============================  ==========================  ======================
Variable (.env → servicio)       Rol                          Quién la usa
===============================  ==========================  ======================
``API_SERVER_DATABASE_URL``      ``app_user`` NOBYPASSRLS     `get_sessionmaker()`,
                                                             peticiones humanas
``API_SERVER_ADMIN_DATABASE_URL````service_user`` BYPASSRLS,  **`get_admin_sessionmaker()`
                                 sin DDL                      ← la siembra**
``DATABASE_URL`` de `migrations` ``migrations_user``, dueño   sólo Alembic y el
                                 del esquema, DDL             `pg_dump` del backup
===============================  ==========================  ======================

El cableado ya está hecho y es el correcto: `config_generators` mapea
``API_SERVER_ADMIN_DATABASE_URL`` → ``SERVICE_DATABASE_URL``, y el one-shot lo
hereda por el simple hecho de correr con la imagen del api-server y el
`_api_server_env()` entero. Lo que **no** se hace aquí es construir un engine
propio desde un `DATABASE_URL` suelto: el servicio `bootstrap` no recibe ninguno
sin prefijo, y si alguien lo añadiera apuntaría al PROPIETARIO del esquema —
devolviendo a la siembra el privilegio de `ALTER TABLE … DISABLE ROW LEVEL
SECURITY` que prod-14 le quitó a todo lo que no es Alembic.

**Por qué hace falta BYPASSRLS y no basta con fijar `app.tenant_id`:**
`organizations` tiene `FORCE ROW LEVEL SECURITY` con `org_self_only … USING (id =
current_setting('app.tenant_id'))`, y es `FOR ALL` **sin `WITH CHECK`**, así que
PostgreSQL usa el `USING` como check de INSERT: crear una org con `app_user`
exigiría conocer su UUID *antes* de crearla. Es un huevo-y-gallina de diseño, no
un descuido. Y `marketplace_listings` publica filas **globales**
(`tenant_id NULL`), que el check de una sesión de tenant rechaza por definición.

**`app.tenant_id` no se fija, y es deliberado.** BYPASSRLS exime al rol de la RLS
con independencia de `FORCE`, ningún seed del árbol lo fija, y cada uno lleva su
`PLATFORM_TENANT_ID` **escrito en la fila**. Ponerlo introduciría una variable de
sesión que nadie lee y que sugeriría, en falso, que la siembra está acotada a un
tenant. Esto NO contradice el principio 1 de CLAUDE.md: la siembra del catálogo
de plataforma es exactamente el caso al que sirve la excepción BYPASSRLS, y lo
hace con `tenant_id` en cada fila, no ausente.

## El pre-flight, y por qué va antes de Vault

El paso 1 de los seeds es `ensure_platform_tenant`, cuya primera sentencia es un
`INSERT INTO organizations …` en SQL crudo. Sin esquema, asyncpg levanta
`UndefinedTableError: relation "organizations" does not exist`. La guarda del
compose (`depends_on: {migrations: service_completed_successfully}`) cubre el
camino normal, pero tiene **dos límites que el compose no puede cubrir**:

* ``docker compose run --rm --no-deps bootstrap`` la evapora entera;
* `service_completed_successfully` prueba que el contenedor `migrations` salió
  con 0, **no** que el esquema esté en el `head` de ESTA imagen.

De ahí el sondeo propio. Y va **antes de tocar Vault** porque un fallo de esquema
descubierto después del `operator init` cuesta unas unseal keys irrecuperables;
descubierto antes cuesta un mensaje.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError

from api_server.bootstrap.errors import DatabaseError, SchemaNotReadyError, first_line
from api_server.db.session import get_admin_sessionmaker
from api_server.seeds.init_tenant import InitTenantResult, init_tenant

#: Las tablas sin las que la siembra muere en su primera sentencia. No es el
#: esquema entero a propósito: se busca una comprobación BARATA y RUIDOSA, no
#: una segunda implementación de Alembic.
#:
#:   * ``alembic_version`` — si no está, las migraciones no han corrido NUNCA.
#:   * ``organizations``   — la primera escritura de `ensure_platform_tenant`.
#:   * ``users`` / ``user_org_memberships`` — las otras dos de `init_tenant`.
REQUIRED_TABLES: tuple[str, ...] = (
    "alembic_version",
    "organizations",
    "users",
    "user_org_memberships",
)


@dataclass(frozen=True)
class SchemaState:
    """Lo que el esquema cuenta de sí mismo. Sin secretos: es seguro loguearlo."""

    missing_tables: tuple[str, ...]
    applied_revisions: tuple[str, ...]


class BootstrapDatabase(Protocol):
    """La costura de base de datos del one-shot.

    Existe para que el orden de los cuatro pasos —y sobre todo el hecho de que
    el revelado sale ANTES del catálogo— se pueda aseverar sin una PostgreSQL
    viva. El defecto que se protege no es un SQL mal escrito: es un orden.
    """

    async def schema_state(self) -> SchemaState:
        """El pre-flight: qué falta y en qué revisión está el esquema."""
        ...

    async def init_tenant(
        self, *, tenant_name: str, slug: str, admin_email: str, admin_password: str
    ) -> InitTenantResult:
        """Org + usuario admin + membresía `tenant_admin`. Idempotente."""
        ...

    async def seed_catalog(self) -> None:
        """Los 21 pasos del catálogo built-in. Minutos: es el paso caro."""
        ...


def assert_schema_ready(state: SchemaState, *, expected_revisions: tuple[str, ...]) -> None:
    """Levanta si el esquema no está listo para sembrar. Barato y ruidoso.

    *expected_revisions* vacío significa «no consta»: no se puede resolver el
    head del paquete desde este proceso. Se sigue adelante —bloquear una
    instalación por no poder COMPROBAR algo sería peor que la comprobación que
    falta—, pero el runner lo dice en un log.
    """

    if state.missing_tables:
        raise SchemaNotReadyError(
            "El esquema no está migrado: faltan las tablas "
            f"{', '.join(state.missing_tables)}. La siembra moriría en su primera "
            'sentencia con `relation "organizations" does not exist`. Corre las '
            "migraciones antes (`docker compose up migrations` las ejecuta, y el "
            "servicio `bootstrap` las espera con `depends_on` — salvo que lo hayas "
            "lanzado con `--no-deps`, que evapora esa guarda) y vuelve a ejecutar. "
            "Esto se comprueba ANTES de tocar Vault a propósito: descubrirlo "
            "después del `operator init` costaría unas unseal keys irrecuperables."
        )

    if not state.applied_revisions:
        raise SchemaNotReadyError(
            "La tabla `alembic_version` existe pero está vacía: ninguna migración "
            "ha llegado a aplicarse. Corre `alembic upgrade head` (el servicio "
            "`migrations` del compose) y vuelve a ejecutar."
        )

    if expected_revisions and set(state.applied_revisions) != set(expected_revisions):
        raise SchemaNotReadyError(
            "El esquema no está en el head de esta imagen: la base de datos está "
            f"en {', '.join(sorted(state.applied_revisions))} y esta imagen espera "
            f"{', '.join(sorted(expected_revisions))}. El `depends_on: migrations "
            "service_completed_successfully` sólo prueba que aquel contenedor "
            "salió con 0, no que el esquema sea el que esta imagen necesita: "
            "sembrar así escribiría contra un esquema que no es el suyo. Corre "
            "las migraciones de esta versión y vuelve a ejecutar."
        )


def packaged_alembic_heads() -> tuple[str, ...]:
    """El/los head(s) de las migraciones que viaja(n) con esta imagen.

    En la imagen, `alembic.ini` y `migrations/` viven en `/app` (que es el
    WORKDIR) porque el one-shot `migrations` del compose corre desde ahí; en el
    árbol de desarrollo, al lado de `apps/api-server/src/`. Se prueban los dos y,
    si no aparece ninguno —o si hay varias cabezas, que aquí sería una rama sin
    resolver—, se devuelve la tupla vacía: «no consta» es una respuesta honesta y
    :func:`assert_schema_ready` la trata como tal.
    """

    from pathlib import Path

    import api_server

    candidates = (
        Path.cwd() / "migrations",
        Path(api_server.__file__).resolve().parents[2] / "migrations",
    )
    for directory in candidates:
        if not (directory / "versions").is_dir():
            continue
        try:
            from alembic.script import ScriptDirectory

            heads = ScriptDirectory(str(directory)).get_heads()
        except Exception:  # pragma: no cover - entorno sin alembic o árbol raro
            return ()
        return tuple(str(h) for h in heads) if len(heads) == 1 else ()
    return ()


class SqlAlchemyBootstrapDatabase:
    """La implementación real, sobre el sessionmaker BYPASSRLS.

    No guarda el sessionmaker en el constructor: `get_admin_sessionmaker()` está
    cacheado con `lru_cache` y construye el engine perezosamente, así que pedirlo
    en cada uso es gratis y evita fijar un engine antes de que la configuración
    esté leída.
    """

    async def schema_state(self) -> SchemaState:
        present: set[str] = set()
        revisions: tuple[str, ...] = ()
        try:
            async with get_admin_sessionmaker()() as session:
                rows = await session.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public' AND tablename IN :names"
                    ).bindparams(bindparam("names", expanding=True)),
                    {"names": list(REQUIRED_TABLES)},
                )
                present = {str(row[0]) for row in rows}
                if "alembic_version" in present:
                    found = await session.execute(text("SELECT version_num FROM alembic_version"))
                    revisions = tuple(str(row[0]) for row in found)
        except SQLAlchemyError as exc:
            raise DatabaseError(
                "PostgreSQL no ha contestado al comprobar el esquema: "
                f"{first_line(exc)}. El servicio `postgres` tiene que estar sano "
                "(el `depends_on` del one-shot lo pide) y "
                "`API_SERVER_ADMIN_DATABASE_URL` tiene que apuntar a él con el rol "
                "`service_user`."
            ) from exc

        return SchemaState(
            missing_tables=tuple(t for t in REQUIRED_TABLES if t not in present),
            applied_revisions=revisions,
        )

    async def init_tenant(
        self, *, tenant_name: str, slug: str, admin_email: str, admin_password: str
    ) -> InitTenantResult:
        sessionmaker = get_admin_sessionmaker()
        async with sessionmaker() as session, session.begin():
            return await init_tenant(
                session,
                tenant_name=tenant_name,
                slug=slug,
                admin_email=admin_email,
                admin_password=admin_password,
            )

    async def seed_catalog(self) -> None:
        # Import diferido: `api_server.seeds.__main__` arrastra el árbol entero
        # de seeds (marketplace, ingestión, RAG…), y no tiene por qué pagarse al
        # importar este paquete — por ejemplo, al leerlo desde un test.
        from api_server.seeds.__main__ import run_seeds

        await run_seeds(get_admin_sessionmaker())

    async def aclose(self) -> None:
        """Cierra el pool ANTES de que muera el bucle de eventos.

        Sin esto, `asyncio.run` cierra el loop con conexiones asyncpg todavía
        vivas y Python escribe `Exception ignored in: ...` por stderr al salir.
        No cambia el código de salida, pero aparece justo debajo del revelado —y
        el instalador enseña la cola de la salida cuando algo falla—, así que un
        one-shot que ha terminado bien parece una avería. Va en un `finally`: el
        camino que más lo necesita es el que ya venía fallando.
        """

        from api_server.db.session import get_admin_engine

        await get_admin_engine().dispose()
