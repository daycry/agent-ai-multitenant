"""La mitad de PostgreSQL del arnés e2e con backend vivo.

Quién llama a esto
------------------
``scripts/dev/e2e-live-harness.ps1``, que es el guion que se usa. Este módulo no
se invoca a mano salvo para depurar: el .ps1 es el único que sabe leer
``docker/.env``, y sin las variables que él exporta esto aborta.

Por qué existe separado del .ps1
--------------------------------
Tres de los cinco pasos de aquí NO se pueden escribir en PowerShell sin
duplicar código que ya existe en Python y que tiene que decir lo mismo:

* los **REVOKE** salen de ``tests/integration/conftest.py``
  (``_APP_REVOKED_TABLES``). Si el arnés de Playwright y el de pytest no
  reproducen los MISMOS permisos, el más laxo de los dos deja pasar código que
  producción rechaza — que es exactamente el fallo que documenta el comentario
  largo de esa constante;
* el **hash de las contraseñas** tiene que ser el mismo argon2id que produce
  ``POST /auth/register``, y eso vive en ``api_server.auth.passwords``. Con el
  registro cerrado desde el ADR 0134, sembrar por SQL es la única vía que no
  pide montar el circuito de invitaciones entero;
* los **ids** son uuid7 (``uuid6.uuid7``), como el resto del dominio.

Ni una contraseña en este fichero
---------------------------------
Todas las credenciales llegan por entorno, en forma de DSN ya montado, desde el
.ps1 que las leyó de ``docker/.env`` (que está en .gitignore). No se imprimen
nunca: lo que se imprime es el nombre de la base, los roles y los correos.

Variables que espera (las pone el .ps1):

==============================  ================================================
``HARNESS_DB``                  nombre de la base DESECHABLE
``HARNESS_SUPERUSER_DSN``       DSN de ``postgres`` a la base ``postgres``
``HARNESS_TARGET_ADMIN_DSN``    DSN de ``postgres`` a ``HARNESS_DB``
``HARNESS_SEED_DSN``            DSN de ``migrations_user`` a ``HARNESS_DB``
``HARNESS_MIGRATIONS_ROLE``     rol dueño del esquema (DDL de Alembic)
``HARNESS_APP_ROLE``            rol de la aplicación (NOBYPASSRLS)
``HARNESS_SERVICE_ROLE``        rol de ``/admin/*`` (BYPASSRLS, sin DDL)
==============================  ================================================
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path

import asyncpg

RAIZ = Path(__file__).resolve().parents[2]

#: Bases que este guion NO puede tocar, pase lo que pase.
#:
#: ``agentic_platform`` va con nombre y apellidos porque es la base del STACK
#: VIVO del operador (110 tablas de trabajo real), y porque los 12 specs que no
#: mockean **crean y borran** proyectos, agentes y equipos: apuntar el arnés ahí
#: no ensucia la base, la vacía a trozos. El .ps1 añade además el valor de
#: ``POSTGRES_DB`` de ``docker/.env``, que es el mismo dato descubierto de forma
#: mecánica — así la guarda sigue siendo cierta el día que alguien renombre la
#: base del stack y se olvide de esta lista.
BASES_PROHIBIDAS = frozenset({"agentic_platform", "postgres", "template0", "template1"})

#: Extensiones que en producción crea ``docker/postgres/init/`` y que ninguna
#: migración crea por su cuenta. Sin ellas la primera migración que declara una
#: columna ``vector`` revienta.
EXTENSIONES = ("vector", "pg_trgm", "pgcrypto", "uuid-ossp")

#: El tenant del arnés y los cuatro usuarios que los 12 specs declaran en su
#: cabecera. Los correos y la contraseña son los DEFAULTS que los propios specs
#: llevan escritos (``process.env.E2E_… ?? "…"``), así que no son un secreto:
#: son parte del contrato del arnés. Cambiar uno aquí sin cambiarlo allí deja
#: los specs esperando un login que no existe.
TENANT_SLUG = "e2e-backend-vivo"
TENANT_NOMBRE = "E2E backend vivo"
CONTRASENA = "longenoughpw"

#: (correo, nombre, ¿system admin?, rol en el tenant o None)
#:
#: ``root@example.com`` es el que usan siete de los doce specs y necesita las dos
#: cosas: ``is_system_admin`` para ``/admin/system-health`` y el catálogo, y
#: membresía en el tenant para las pantallas de equipos y proyectos.
#:
#: ``sys@platform.example.com`` es el System Admin SIN tenant a propósito:
#: ``notification-config.spec.ts`` lo usa para el caso «plataforma», y darle
#: membresía le cambiaría el contexto de tenant por defecto.
#:
#: El rol del miembro es ``tenant_user`` y **no** ``member``: los valores válidos
#: son los de ``UserRole`` (``api_server.db.models``), la columna es un
#: ``varchar(32)`` sin CHECK, y ``member`` no lo reconoce nadie. Con ``member``
#: los casos de denegación pasan igual —``require_tenant_admin`` compara contra
#: ``tenant_admin`` y falla con cualquier otra cosa— pero pasan por el motivo
#: equivocado: no porque el usuario sea un ``tenant_user``, sino porque su rol no
#: existe. Un test verde sobre un rol inventado no dice nada del RBAC real.
USUARIOS: tuple[tuple[str, str, bool, str | None], ...] = (
    ("root@example.com", "Root E2E", True, "tenant_admin"),
    ("sys@platform.example.com", "Sys E2E", True, None),
    ("admin@tenant.example.com", "Tenant Admin E2E", False, "tenant_admin"),
    ("member@tenant.example.com", "Tenant Member E2E", False, "tenant_user"),
)


# ---------------------------------------------------------------------------
# Entorno y guardas
# ---------------------------------------------------------------------------
def _env(clave: str) -> str:
    valor = os.environ.get(clave, "").strip()
    if not valor:
        sys.exit(
            f"FALTA {clave}. Este módulo no se invoca a mano: lo llama "
            "scripts/dev/e2e-live-harness.ps1, que es quien lee docker/.env."
        )
    return valor


def _base_objetivo() -> str:
    """El nombre de la base, ya comprobado contra la lista de prohibidas.

    La comprobación se repite aquí aunque el .ps1 ya la haga. No es paranoia
    barata: cada subcomando de este módulo es un punto de entrada
    independiente, y el que se salte la guarda es el que hace el daño.
    """
    base = _env("HARNESS_DB")
    prohibidas = set(BASES_PROHIBIDAS) | {
        nombre.strip()
        for nombre in os.environ.get("HARNESS_FORBIDDEN_DBS", "").split(",")
        if nombre.strip()
    }
    if base in prohibidas:
        sys.exit(
            f"ABORTA: '{base}' es una base PROTEGIDA, no la desechable del arnés.\n"
            "\n"
            "Los 12 specs que no mockean no leen: CREAN y BORRAN proyectos, "
            "agentes, equipos y tenants. Apuntarlos a la base del stack vivo "
            "no la ensucia, se lleva trabajo real por delante.\n"
            "\n"
            f"Bases protegidas: {', '.join(sorted(prohibidas))}.\n"
            "Pasa -DbName con una base desechable (el default es e2e_vivo)."
        )
    return base


def _tablas_revocadas() -> tuple[str, ...]:
    """Las tablas cuyo acceso una migración RETIRA a la aplicación, leídas del
    arnés de pytest para que los dos no puedan divergir.

    Se lee con una expresión regular en vez de importando ``conftest.py``: ese
    módulo arrastra pytest y, al importar ``._redis_url``, dispara sus guardas
    de Redis. Aquí no hay Redis que comprobar.

    Si el descubrimiento no encuentra nada, se ABORTA. Una lista vacía haría
    que el retro-grant de ``grant`` —un ``ON ALL TABLES`` sin excepciones—
    devolviese a la aplicación el acceso que la migración 0138 le quitó, y el
    arnés quedaría MÁS PERMISIVO QUE PRODUCCIÓN sin que nada lo dijese. Ése es
    el modo de fallo que ``verificar-antes-de-implementar.md`` §4 llama «una
    guarda que no puede fallar no es una guarda»: cero coincidencias, cero
    infractores, verde.
    """
    conftest = RAIZ / "tests" / "integration" / "conftest.py"
    fuente = conftest.read_text(encoding="utf-8")
    bloque = re.search(r"_APP_REVOKED_TABLES:\s*tuple\[str,\s*\.\.\.\]\s*=\s*\(([^)]*)\)", fuente)
    if bloque is None:
        sys.exit(
            f"ABORTA: no se encuentra `_APP_REVOKED_TABLES` en {conftest}.\n"
            "Es la lista de tablas a las que una migración QUITA el acceso de la "
            "aplicación a propósito (hoy: approval_policy_backfill_0133, migración "
            "0138). Sin ella, el retro-grant de este guion se las volvería a "
            "conceder y el arnés sería más permisivo que producción.\n"
            "Si la constante se ha renombrado, actualiza esta expresión regular; "
            "si ha desaparecido, comprueba antes que los REVOKE ya no hacen falta."
        )
    tablas = tuple(sorted(set(re.findall(r'"([^"]+)"', bloque.group(1)))))
    if not tablas:
        sys.exit(
            "ABORTA: `_APP_REVOKED_TABLES` existe pero está VACÍA. Si de verdad ya "
            "no hay tablas revocadas, borra esta guarda a mano tras comprobarlo; "
            "mientras exista, una lista vacía es indistinguible de un regex roto."
        )
    return tablas


# ---------------------------------------------------------------------------
# Subcomandos
# ---------------------------------------------------------------------------
async def _existe(conn: asyncpg.Connection, base: str) -> bool:
    return bool(await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", base))


async def crear(base: str) -> None:
    """Crea la base desechable si no está, con extensiones y privilegios base.

    Idempotente: si la base ya existe no se recrea (los seeds y los usuarios
    tardan minutos y no hay razón para repetirlos en cada arranque). Lo que sí
    se reaplica siempre son las extensiones y los ALTER DEFAULT PRIVILEGES,
    porque son las dos cosas que producción pone en el init y que una migración
    aplicada a medias puede haber dejado a medio camino.
    """
    conn = await asyncpg.connect(_env("HARNESS_SUPERUSER_DSN"))
    try:
        if await _existe(conn, base):
            print(f"    la base '{base}' ya existe: no se recrea")
        else:
            # El OWNER es migrations_user porque es quien corre el DDL de
            # Alembic. Con owner `postgres` la migración pasa igual (es
            # superusuario) pero las tablas quedan de otro dueño y los ALTER
            # DEFAULT PRIVILEGES de abajo, que cuelgan del rol migrations_user,
            # dejan de aplicarse: el arnés acaba sin los GRANT y el login
            # revienta con «permission denied for table user_mfa_totp».
            dueno = _env("HARNESS_MIGRATIONS_ROLE")
            await conn.execute(f'CREATE DATABASE "{base}" OWNER "{dueno}"')
            print(f"    base '{base}' creada")
    finally:
        await conn.close()

    migraciones = _env("HARNESS_MIGRATIONS_ROLE")
    app = _env("HARNESS_APP_ROLE")
    servicio = _env("HARNESS_SERVICE_ROLE")

    conn = await asyncpg.connect(_env("HARNESS_TARGET_ADMIN_DSN"))
    try:
        for ext in EXTENSIONES:
            await conn.execute(f'CREATE EXTENSION IF NOT EXISTS "{ext}"')
        await conn.execute(f'GRANT ALL ON SCHEMA public TO "{migraciones}"')
        for rol in (app, servicio):
            await conn.execute(f'GRANT USAGE ON SCHEMA public TO "{rol}"')
            # Los defaults sólo alcanzan a lo que se cree DESPUÉS. Por eso hace
            # falta además el retro-grant de `grant`, tras migrar.
            await conn.execute(
                f'ALTER DEFAULT PRIVILEGES FOR ROLE "{migraciones}" IN SCHEMA public '
                f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{rol}"'
            )
            await conn.execute(
                f'ALTER DEFAULT PRIVILEGES FOR ROLE "{migraciones}" IN SCHEMA public '
                f'GRANT USAGE, SELECT ON SEQUENCES TO "{rol}"'
            )
        print(f"    extensiones + privilegios base para {app}, {servicio}")
    finally:
        await conn.close()


async def conceder(base: str) -> None:
    """Retro-grant sobre las tablas ya migradas, y sus REVOKE detrás.

    Éste es el paso que la migración NO trae y que costó la hora de prueba y
    error: migrar como ``postgres`` deja la base sin los privilegios que en
    producción pone ``docker/postgres/init/``, y el primer síntoma es un login
    que devuelve 500 con «permission denied for table user_mfa_totp» — un error
    que no menciona ni los GRANT ni el init.
    """
    app = _env("HARNESS_APP_ROLE")
    servicio = _env("HARNESS_SERVICE_ROLE")
    revocadas = _tablas_revocadas()

    conn = await asyncpg.connect(_env("HARNESS_TARGET_ADMIN_DSN"))
    try:
        tablas = await conn.fetchval("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
        if tablas < 50:
            sys.exit(
                f"ABORTA: '{base}' tiene {tablas} tablas en `public`. La migración no "
                "ha corrido (o no ha commiteado), y conceder permisos sobre un "
                "esquema vacío es la forma silenciosa de que el paso siguiente "
                "falle mucho más lejos. Mira el log de alembic."
            )
        for rol in (app, servicio):
            await conn.execute(
                f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{rol}"'
            )
            await conn.execute(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{rol}"')
        print(f"    DML + secuencias sobre {tablas} tablas para {app}, {servicio}")

        vistas = 0
        for tabla in revocadas:
            # `to_regclass` porque la tabla puede no existir todavía si el
            # esquema está a medio migrar; lo que no está no se revoca.
            existe = await conn.fetchval("SELECT to_regclass($1)", f"public.{tabla}")
            if existe is None:
                continue
            for rol in (app, servicio):
                await conn.execute(f'REVOKE ALL ON TABLE public."{tabla}" FROM "{rol}"')
            vistas += 1
        if vistas == 0:
            sys.exit(
                f"ABORTA: ninguna de las tablas revocadas ({', '.join(revocadas)}) "
                f"existe en '{base}'. O la migración no llegó al final, o la lista de "
                "`_APP_REVOKED_TABLES` habla de tablas que ya no están. En los dos "
                "casos el arnés quedaría con permisos que producción no da, y eso no "
                "se ve hasta que un test pasa donde producción falla."
            )
        print(f"    REVOKE reaplicado sobre {vistas} tabla(s): {', '.join(revocadas)}")
    finally:
        await conn.close()


async def sembrar_usuarios(base: str) -> None:
    """El tenant del arnés y sus cuatro usuarios. Idempotente."""
    sys.path.insert(0, str(RAIZ))
    sys.path.insert(0, str(RAIZ / "apps" / "api-server" / "src"))
    from uuid6 import uuid7

    from tests.integration._user_seeding import seed_user

    dsn = _env("HARNESS_SEED_DSN")
    ids: dict[str, str] = {}
    for correo, nombre, es_admin, _rol in USUARIOS:
        ids[correo] = await seed_user(
            dsn, correo, CONTRASENA, full_name=nombre, is_system_admin=es_admin
        )

    conn = await asyncpg.connect(dsn)
    try:
        tenant_id = await conn.fetchval(
            "INSERT INTO organizations (id, name, slug, personal_assistant_enabled)"
            " VALUES ($1::uuid, $2, $3, true)"
            " ON CONFLICT (slug) DO UPDATE SET"
            "   name = EXCLUDED.name,"
            # El toggle es una PRE-CONDICIÓN declarada por
            # `personal-assistant.spec.ts` («a tenant whose
            # personal_assistant_enabled toggle is ON»). Con el toggle apagado el
            # backend responde 403 y el spec falla por falta de siembra, no por
            # un defecto de la pantalla.
            "   personal_assistant_enabled = true"
            " RETURNING id",
            str(uuid7()),
            TENANT_NOMBRE,
            TENANT_SLUG,
        )
        for correo, _nombre, _es_admin, rol in USUARIOS:
            if rol is None:
                continue
            # `DO UPDATE` sobre (user_id, tenant_id) y no `DO NOTHING`: una
            # segunda pasada tiene que poder CORREGIR el rol. Con `DO NOTHING`,
            # la fila `member` que dejó la siembra de hoy sobreviviría a este
            # guion y el miembro seguiría con un rol que no existe en `UserRole`.
            await conn.execute(
                "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
                " VALUES ($1::uuid, $2::uuid, $3::uuid, $4)"
                " ON CONFLICT (user_id, tenant_id) DO UPDATE SET"
                "   role = EXCLUDED.role, is_active = true, deleted_at = NULL",
                str(uuid7()),
                str(tenant_id),
                ids[correo],
                rol,
            )
        filas = await conn.fetch(
            "SELECT u.email, m.role, u.is_system_admin FROM users u"
            " LEFT JOIN user_org_memberships m"
            "   ON m.user_id = u.id AND m.tenant_id = $1::uuid"
            " WHERE u.email = ANY($2::text[]) ORDER BY u.email",
            str(tenant_id),
            [correo for correo, _n, _a, _r in USUARIOS],
        )
    finally:
        await conn.close()

    print(f"    en '{base}': tenant '{TENANT_NOMBRE}' (slug {TENANT_SLUG}) = {tenant_id}")
    print("    personal_assistant_enabled = true")
    for fila in filas:
        rol = fila["role"] or "(sin tenant)"
        marca = " [system admin]" if fila["is_system_admin"] else ""
        print(f"      {fila['email']:28} {rol}{marca}")
    if len(filas) != len(USUARIOS):
        sys.exit(
            f"ABORTA: se esperaban {len(USUARIOS)} usuarios y la relectura ve "
            f"{len(filas)}. La siembra no dejó lo que dice haber dejado."
        )


async def tirar(base: str) -> None:
    """Borra la base desechable. La guarda de arriba es la que hace esto seguro."""
    conn = await asyncpg.connect(_env("HARNESS_SUPERUSER_DSN"))
    try:
        if not await _existe(conn, base):
            print(f"    la base '{base}' no existe: nada que borrar")
            return
        await conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
            " WHERE datname = $1 AND pid <> pg_backend_pid()",
            base,
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{base}"')
        print(f"    base '{base}' borrada")
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("accion", choices=("create", "grant", "seed-users", "drop"))
    accion = parser.parse_args().accion

    base = _base_objetivo()
    corredor = {
        "create": crear,
        "grant": conceder,
        "seed-users": sembrar_usuarios,
        "drop": tirar,
    }[accion]
    asyncio.run(corredor(base))


if __name__ == "__main__":
    main()
