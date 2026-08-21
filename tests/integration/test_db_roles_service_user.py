"""El rol `service_user`: DML cross-tenant sin DDL (plan prod-14, tenancy-2).

Los cuatro servicios (workers, orchestrator, notification-dispatcher y el engine
admin de la api-server) conectan hoy como `migrations_user`, el PROPIETARIO del
esquema con `GRANT ALL`. Eso significa que un servicio comprometido puede correr

    ALTER TABLE agents DISABLE ROW LEVEL SECURITY;

y apagar el aislamiento multi-tenant de toda la plataforma de una sentencia. Ese
privilegio no lo necesita ningún servicio: solo Alembic.

Este fichero comprueba el rol que arregla eso. **No re-escribe el DDL: LEE
`docker/postgres/init/04-service-role.sql`** y lo ejecuta, así que lo que se
verifica es el artefacto que se despliega. Si alguien le añade un `GRANT ALL`, le
quita el `NOSUPERUSER` o le da `CREATE` en el esquema, estos tests se ponen rojos.

Lo que NO cubre (y por qué): que los servicios *usen* ya este rol. Cambiar los
defaults de `database_url` vive en `apps/*/config.py`, fuera de este carril; el
paso está reportado como trabajo pendiente. El rol y sus privilegios —la parte
que se puede verificar sin desplegar— sí están aquí.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg
import pytest
from alembic import command

pytestmark = [pytest.mark.integration, pytest.mark.cross_tenant]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PG_INIT_DIR = _REPO_ROOT / "docker" / "postgres" / "init"
SERVICE_ROLE_SQL = _PG_INIT_DIR / "04-service-role.sql"
#: El script que fija la contraseña del rol desde el entorno en un arranque
#: LIMPIO. Va separado del `.sql` a propósito: ese fichero tiene que seguir
#: siendo SQL plano para poder ejecutarse también sobre una BD viva y desde este
#: test, y el SQL plano no puede leer variables de entorno.
SERVICE_ROLE_PASSWORD_SH = _PG_INIT_DIR / "05-service-role-password.sh"

SERVICE_USER = "service_user"
SERVICE_PASSWORD = "changeme-service-dev-only"


def _pg() -> tuple[str, int]:
    return (
        os.environ.get("TEST_PG_HOST", "localhost"),
        int(os.environ.get("TEST_PG_PORT", "15432")),
    )


def _admin_dsn(db: str) -> str:
    host, port = _pg()
    user = os.environ.get("TEST_PG_ADMIN_USER", "postgres")
    password = os.environ.get("TEST_PG_ADMIN_PASSWORD", "changeme-dev-only")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def _service_dsn(db: str) -> str:
    host, port = _pg()
    return f"postgresql://{SERVICE_USER}:{SERVICE_PASSWORD}@{host}:{port}/{db}"


async def _apply_service_role_sql(db: str) -> None:
    """Ejecuta el fichero SQL que se despliega, tal cual.

    asyncpg no admite varias sentencias en un `execute()` con parámetros, pero sí
    en uno sin ellos (usa el protocolo simple), y este fichero no lleva
    parámetros a propósito. Idempotente: se puede aplicar sobre una base de datos
    que ya tiene el rol.
    """
    sql = SERVICE_ROLE_SQL.read_text(encoding="utf-8")
    conn = await asyncpg.connect(_admin_dsn(db))
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


@pytest.fixture()
def service_role(alembic_config: object, test_database_url: str) -> str:
    """Base de datos migrada + `service_user` creado con el SQL de producción.

    Orden deliberado: primero `upgrade head`, DESPUÉS el SQL del rol. Es el caso
    difícil (base de datos viva), el que el `GRANT ... ON ALL TABLES` tiene que
    cubrir. En un contenedor nuevo el orden es el inverso y lo cubre el
    `ALTER DEFAULT PRIVILEGES`.
    """
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    db = os.environ.get("TEST_PG_DB_NAME", "agentic_platform_test")
    asyncio.run(_apply_service_role_sql(db))
    return db


# ===========================================================================
# 1. El fichero SQL existe y declara la postura que decimos que declara.
# ===========================================================================
def test_service_role_sql_declares_the_intended_posture() -> None:
    assert SERVICE_ROLE_SQL.exists(), f"falta {SERVICE_ROLE_SQL}"
    sql = SERVICE_ROLE_SQL.read_text(encoding="utf-8")
    lowered = sql.lower()

    for required in (
        "create role service_user",
        "bypassrls",
        "nosuperuser",
        "nocreatedb",
        "nocreaterole",
        "grant usage on schema public to service_user",
        "revoke create on schema public from service_user",
        "grant select, insert, update, delete on all tables",
        "alter default privileges for role migrations_user",
    ):
        assert required in lowered, f"04-service-role.sql ya no contiene: {required!r}"

    # Lo que NUNCA debe aparecer en el SQL EJECUTABLE (los comentarios del
    # fichero hablan de `GRANT ALL` justamente para explicar por qué no está).
    statements = " ".join(
        line.split("--", 1)[0] for line in lowered.splitlines() if not line.strip().startswith("--")
    )
    assert "grant" in statements, "el filtro de comentarios se comió todo el SQL"
    for forbidden in ("grant all", "createrole,", "owner to", "grant create"):
        assert forbidden not in statements, f"04-service-role.sql concede DDL: {forbidden!r}"
    # `superuser` solo puede aparecer negado.
    assert "nosuperuser" in statements and statements.count("superuser") == statements.count(
        "nosuperuser"
    ), "04-service-role.sql menciona SUPERUSER sin negarlo"


# ===========================================================================
# 1 bis. Un arranque LIMPIO honra `SERVICE_USER_PASSWORD`.
#
# Sin esto, `04-service-role.sql` deja la contraseña del rol clavada en el
# literal de dev — y ese literal está en el repositorio. Un despliegue nuevo
# tendría un rol BYPASSRLS con contraseña pública: no es «un default flojo»,
# es la llave que se salta la RLS de todos los tenants publicada en GitHub.
# El script de upgrade ya acepta la variable; lo que faltaba era el arranque
# limpio, que es justo el caso de una instalación nueva.
# ===========================================================================
def test_a_clean_start_honours_service_user_password_from_the_environment() -> None:
    assert SERVICE_ROLE_PASSWORD_SH.exists(), (
        "falta el script de init que fija la contraseña de `service_user` desde el"
        f" entorno ({SERVICE_ROLE_PASSWORD_SH.name}): un arranque limpio se queda con"
        " el literal de dev que está en el repositorio"
    )
    script = SERVICE_ROLE_PASSWORD_SH.read_text(encoding="utf-8")

    assert "SERVICE_USER_PASSWORD" in script, (
        "el script de init no lee `SERVICE_USER_PASSWORD`, así que no hay forma de"
        " darle una contraseña propia a un despliegue nuevo"
    )
    assert "ALTER ROLE service_user" in script, (
        "el script no aplica la contraseña al rol; solo la lee"
    )
    # El orden alfabético del entrypoint de la imagen de PostgreSQL es el
    # contrato: este script tiene que correr DESPUÉS del 04 que crea el rol.
    assert SERVICE_ROLE_PASSWORD_SH.name > SERVICE_ROLE_SQL.name, (
        "el script de contraseña ordena ANTES que el que crea el rol; el entrypoint"
        " de postgres ejecuta `init/` por orden alfabético, así que se aplicaría"
        " sobre un rol que todavía no existe"
    )
    # Y tiene que seguir habiendo un default, o un `docker compose up` de dev sin
    # `.env` dejaría el rol sin contraseña utilizable.
    assert SERVICE_PASSWORD in script, (
        "el script perdió el default de desarrollo: un arranque sin"
        f" SERVICE_USER_PASSWORD dejaría de casar con {SERVICE_PASSWORD!r}, que es lo"
        " que usan el compose de dev y esta suite"
    )


# ===========================================================================
# 2. Atributos del rol, leídos de pg_roles conectando de verdad.
# ===========================================================================
def test_service_user_role_attributes(service_role: str) -> None:
    async def _check() -> None:
        conn = await asyncpg.connect(_admin_dsn(service_role))
        try:
            row = await conn.fetchrow(
                "SELECT rolcanlogin, rolbypassrls, rolsuper, rolcreatedb, rolcreaterole"
                "  FROM pg_roles WHERE rolname = $1",
                SERVICE_USER,
            )
            assert row is not None, "el SQL de producción no creó service_user"
            assert row["rolcanlogin"], "service_user no puede conectar"
            assert row["rolbypassrls"], (
                "service_user debe ser BYPASSRLS: es su razón de ser (un worker"
                " procesa el tenant que le toque sin app.tenant_id de request)"
            )
            assert not row["rolsuper"], "service_user NO puede ser superusuario"
            assert not row["rolcreatedb"], "service_user NO puede crear bases de datos"
            assert not row["rolcreaterole"], "service_user NO puede crear roles"
        finally:
            await conn.close()

    asyncio.run(_check())


# ===========================================================================
# 3. Privilegios: DML sí, esquema NO. Y no es propietario de nada.
# ===========================================================================
def test_service_user_has_dml_but_no_schema_create(service_role: str) -> None:
    async def _check() -> None:
        conn = await asyncpg.connect(_admin_dsn(service_role))
        try:
            for priv in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                assert await conn.fetchval(
                    "SELECT has_table_privilege($1, 'agents', $2)", SERVICE_USER, priv
                ), f"service_user no tiene {priv} sobre agents"

            assert await conn.fetchval(
                "SELECT has_schema_privilege($1, 'public', 'USAGE')", SERVICE_USER
            )
            assert not await conn.fetchval(
                "SELECT has_schema_privilege($1, 'public', 'CREATE')", SERVICE_USER
            ), "service_user tiene CREATE en el esquema public: puede crear tablas"

            owned = await conn.fetchval(
                "SELECT count(*) FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner"
                " WHERE r.rolname = $1 AND c.relnamespace = 'public'::regnamespace",
                SERVICE_USER,
            )
            assert owned == 0, (
                f"service_user es propietario de {owned} objetos: el propietario"
                " puede alterar RLS aunque no tenga privilegios explícitos"
            )
        finally:
            await conn.close()

    asyncio.run(_check())


# ===========================================================================
# 4. Lo que este plan existe para impedir: nada de DDL ni de tocar la RLS.
# ===========================================================================
@pytest.mark.parametrize(
    ("label", "statement"),
    [
        ("disable_rls", "ALTER TABLE agents DISABLE ROW LEVEL SECURITY"),
        ("no_force_rls", "ALTER TABLE agents NO FORCE ROW LEVEL SECURITY"),
        ("drop_policy", "DROP POLICY agents_tenant_isolation ON agents"),
        ("create_policy", "CREATE POLICY pwned ON agents FOR ALL USING (true)"),
        ("create_table", "CREATE TABLE zz_pwned (id int)"),
        ("drop_table", "DROP TABLE agent_tools"),
        ("add_column", "ALTER TABLE agents ADD COLUMN pwned boolean"),
        ("drop_trigger", "DROP TRIGGER trg_agent_tools_set_tenant_id ON agent_tools"),
    ],
)
def test_service_user_cannot_touch_ddl_or_rls(
    service_role: str, label: str, statement: str
) -> None:
    async def _attempt() -> None:
        conn = await asyncpg.connect(_service_dsn(service_role))
        try:
            await conn.execute(statement)
        finally:
            await conn.close()

    with pytest.raises(asyncpg.PostgresError) as exc:
        asyncio.run(_attempt())
    # 42501 insufficient_privilege / 42P01 undefined_table («no existe» cuando
    # el rol no puede ni verla). Lo que NO vale es que la sentencia pase.
    assert exc.value.sqlstate in {"42501", "42P01"}, (
        f"{label}: la sentencia falló por una razón que no es la falta de"
        f" privilegios ({exc.value.sqlstate}: {exc.value})"
    )


# ===========================================================================
# 5. Su razón de ser: DML cross-tenant SIN app.tenant_id. Esta es la
#    contra-prueba de que el rol no está simplemente lisiado.
# ===========================================================================
def test_service_user_can_do_cross_tenant_dml(service_role: str) -> None:
    from uuid import uuid4

    tenant_a, tenant_b = uuid4(), uuid4()
    project_a, project_b = uuid4(), uuid4()

    async def _run() -> None:
        admin = await asyncpg.connect(_admin_dsn(service_role))
        try:
            await admin.execute(
                "INSERT INTO organizations (id, name, slug)"
                " VALUES ($1, 'SA', 'svc-a'), ($2, 'SB', 'svc-b')"
                " ON CONFLICT DO NOTHING",
                tenant_a,
                tenant_b,
            )
        finally:
            await admin.close()

        conn = await asyncpg.connect(_service_dsn(service_role))
        try:
            # Sin SET app.tenant_id: si el rol respetase la RLS, estos INSERT
            # fallarían y los SELECT devolverían 0 filas.
            await conn.execute(
                "INSERT INTO projects (id, tenant_id, name)"
                " VALUES ($1, $2, 'SvcA'), ($3, $4, 'SvcB')",
                project_a,
                tenant_a,
                project_b,
                tenant_b,
            )
            seen = await conn.fetch(
                "SELECT tenant_id FROM projects WHERE id = ANY($1::uuid[])",
                [project_a, project_b],
            )
            assert {r["tenant_id"] for r in seen} == {tenant_a, tenant_b}, (
                "service_user no ve las dos filas: sin lectura cross-tenant los"
                " workers no pueden procesar ejecuciones de varios tenants"
            )
            await conn.execute(
                "UPDATE projects SET description = 'touched' WHERE id = ANY($1::uuid[])",
                [project_a, project_b],
            )
        finally:
            await conn.close()

        cleanup = await asyncpg.connect(_admin_dsn(service_role))
        try:
            await cleanup.execute(
                "DELETE FROM organizations WHERE id = ANY($1::uuid[])", [tenant_a, tenant_b]
            )
        finally:
            await cleanup.close()

    asyncio.run(_run())


# ===========================================================================
# 6. Idempotencia: aplicar el SQL dos veces no rompe ni degrada la postura.
#    (El script de upgrade se re-ejecuta en cada despliegue.)
# ===========================================================================
def test_applying_the_sql_twice_is_idempotent(service_role: str) -> None:
    asyncio.run(_apply_service_role_sql(service_role))

    async def _still_correct() -> None:
        conn = await asyncpg.connect(_admin_dsn(service_role))
        try:
            row = await conn.fetchrow(
                "SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = $1", SERVICE_USER
            )
            assert row is not None and row["rolbypassrls"] and not row["rolsuper"]
            assert not await conn.fetchval(
                "SELECT has_schema_privilege($1, 'public', 'CREATE')", SERVICE_USER
            )
        finally:
            await conn.close()

    asyncio.run(_still_correct())
