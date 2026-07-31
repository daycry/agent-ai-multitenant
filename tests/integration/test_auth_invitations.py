"""Registro por invitación — ADR 0134 opción C, end-to-end.

Cubre las cinco cosas que la decisión exige y que no se pueden verificar sin
base de datos:

  1. **Arranque**: con ``users`` VACÍA el registro sigue abierto (si no, una
     instalación nueva quedaría inaccesible para siempre) y promociona al primer
     usuario a System Admin **y** System Owner.
  2. **Cerrado**: con al menos un usuario, un registro sin invitación devuelve
     **403**, y el mismo cuerpo exacto tanto si el email existe como si no —
     cerrar el registro tiene que cerrar también el oráculo de enumeración del
     409, no moverlo de sitio.
  3. **Canje**: una invitación válida da de alta al usuario Y le crea la
     membresía del tenant/rol que llevaba (sin eso el invitado aterrizaría en
     `no_access` y no habríamos entregado nada).
  4. **Un solo uso + caducidad + revocación**.
  5. **El token nunca se persiste en claro**.

DEUDA CONOCIDA (reportada, no oculta): la tabla ``user_invitations`` necesita
una migración Alembic que este carril NO puede crear (solo el carril
`aprobaciones` emite migraciones desde la cabeza `0126_perf_indexes_uniqueness`).
Mientras llega, estos tests crean la tabla con el DDL EXACTO que se ha
reportado para esa migración —RLS y todo—, de forma que el día que exista la
migración estos tests sigan valiendo sin tocarlos.

Pre-condición: postgres y redis del docker-compose sanos en el host.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# DDL de `user_invitations` — copia literal de la migración reportada.
# ---------------------------------------------------------------------------
_INVITATIONS_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS user_invitations (
        id UUID NOT NULL,
        tenant_id UUID NOT NULL,
        email VARCHAR(320) NOT NULL,
        token_hash VARCHAR(64) NOT NULL,
        token_prefix VARCHAR(32) NOT NULL,
        role VARCHAR(32) NOT NULL,
        expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
        redeemed_at TIMESTAMP WITH TIME ZONE,
        redeemed_by_user_id UUID,
        revoked_at TIMESTAMP WITH TIME ZONE,
        created_by UUID,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT pk_user_invitations PRIMARY KEY (id),
        CONSTRAINT uq_user_invitation_token_hash UNIQUE (token_hash),
        CONSTRAINT fk_user_invitations_tenant FOREIGN KEY (tenant_id)
            REFERENCES organizations (id) ON DELETE CASCADE,
        CONSTRAINT fk_user_invitations_created_by FOREIGN KEY (created_by)
            REFERENCES users (id) ON DELETE SET NULL,
        CONSTRAINT fk_user_invitations_redeemed_by FOREIGN KEY (redeemed_by_user_id)
            REFERENCES users (id) ON DELETE SET NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_user_invitations_tenant_id" " ON user_invitations (tenant_id)",
    "CREATE INDEX IF NOT EXISTS ix_user_invitations_tenant_pending"
    " ON user_invitations (tenant_id, email)"
    " WHERE redeemed_at IS NULL AND revoked_at IS NULL",
    "ALTER TABLE user_invitations ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE user_invitations FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON user_invitations",
    "CREATE POLICY tenant_isolation ON user_invitations FOR ALL"
    " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
)


async def _create_invitations_table(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        for stmt in _INVITATIONS_DDL:
            await conn.execute(stmt)
    finally:
        await conn.close()


async def _reset(dsn: str) -> None:
    """Vacía usuarios, membresías, invitaciones y organizaciones.

    La BD de test es de sesión, así que el estado por test tiene que ser
    explícito — y aquí importa mucho más que de costumbre, porque la puerta de
    arranque depende de que ``users`` esté literalmente vacía.
    """
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE user_invitations, user_org_memberships, users, organizations"
            " RESTART IDENTITY CASCADE"
        )
    finally:
        await conn.close()


async def _seed_tenant(dsn: str, *, name: str = "Acme", slug: str = "acme") -> str:
    conn = await asyncpg.connect(dsn)
    try:
        tenant_id = str(uuid4())
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1::uuid, $2, $3)",
            tenant_id,
            name,
            slug,
        )
        return tenant_id
    finally:
        await conn.close()


async def _insert_invitation(
    dsn: str,
    *,
    tenant_id: str,
    email: str,
    token_hash: str,
    token_prefix: str,
    role: str = "tenant_user",
    expires_at: datetime | None = None,
    redeemed_at: datetime | None = None,
    revoked_at: datetime | None = None,
) -> str:
    conn = await asyncpg.connect(dsn)
    try:
        invitation_id = str(uuid4())
        await conn.execute(
            "INSERT INTO user_invitations"
            " (id, tenant_id, email, token_hash, token_prefix, role, expires_at,"
            "  redeemed_at, revoked_at)"
            " VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9)",
            invitation_id,
            tenant_id,
            email,
            token_hash,
            token_prefix,
            role,
            expires_at or datetime.now(UTC) + timedelta(days=7),
            redeemed_at,
            revoked_at,
        )
        return invitation_id
    finally:
        await conn.close()


async def _count_users(dsn: str) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return int(await conn.fetchval("SELECT count(*) FROM users"))
    finally:
        await conn.close()


@pytest.fixture()
def configured_app(
    alembic_config,
    app_database_url: str,
    admin_database_url: str,
    migrations_pg_dsn: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    """Misma forma que ``test_auth.configured_app`` + la tabla de invitaciones."""
    command.upgrade(alembic_config, "head")

    from tests.integration.conftest import _flush_redis, _grant_app_user_existing_tables

    asyncio.run(_create_invitations_table(migrations_pg_dsn))
    asyncio.run(_grant_app_user_existing_tables())
    asyncio.run(_flush_redis(test_redis_url))

    monkeypatch.setenv("API_SERVER_DATABASE_URL", app_database_url)
    monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")

    # Registro Prometheus PROPIO por test. `install_metrics` declara los
    # colectores contra `get_default_registry()`, que es el `REGISTRY` global
    # del proceso, así que el SEGUNDO `create_app()` de un mismo proceso pytest
    # revienta con `DuplicateTimeseries` — el módulo de métricas soporta
    # inyectar un registro pero su call-site no lo usa. Mientras eso se arregla
    # en su sitio, aquí se hace lo que su propio docstring dice que debería
    # pasar ("cada test usa el suyo"). Cuando se arregle, esta línea es inocua.
    from prometheus_client import CollectorRegistry

    monkeypatch.setattr(
        "api_server.metrics.get_default_registry",
        lambda: CollectorRegistry(),
    )

    from api_server.auth.deps import reset_redis_cache
    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache

    get_settings.cache_clear()
    reset_engine_cache()
    reset_redis_cache()

    from api_server.main import create_app

    app = create_app()
    try:
        yield app
    finally:
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# 1. Arranque de la primera instalación
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_register_is_open_when_users_table_is_empty(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Sin esta puerta, una instalación nueva queda inaccesible PARA SIEMPRE.

    Y el primer usuario tiene que salir System Admin **y** System Owner: es la
    otra vía (junto al seed del instalador) por la que el rol del ADR 0074 llega
    a existir. Sin owner, el córtex entero es inalcanzable.
    """
    await _reset(migrations_pg_dsn)
    async with _client(configured_app) as client:
        resp = await client.post(
            "/auth/register",
            json={"email": "founder@example.com", "password": "longenoughpw", "full_name": "F"},
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["is_system_admin"] is True
    assert body["is_system_owner"] is True


# ---------------------------------------------------------------------------
# 2. Cerrado al público + sin oráculo de enumeración
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_register_without_invitation_is_403_once_a_user_exists(
    configured_app, migrations_pg_dsn: str
) -> None:
    await _reset(migrations_pg_dsn)
    async with _client(configured_app) as client:
        first = await client.post(
            "/auth/register",
            json={"email": "founder@example.com", "password": "longenoughpw"},
        )
        assert first.status_code == 201, first.text

        resp = await client.post(
            "/auth/register",
            json={"email": "stranger@example.com", "password": "longenoughpw"},
        )

    assert resp.status_code == 403, resp.text
    # Y no ha creado nada.
    assert await _count_users(migrations_pg_dsn) == 1


@pytest.mark.asyncio
async def test_closed_403_is_identical_for_known_and_unknown_email(
    configured_app, migrations_pg_dsn: str
) -> None:
    """El 403 no puede sustituir un oráculo por otro.

    Antes del ADR 0134 el registro abierto confirmaba direcciones: 409 si el
    email existía, 201 si no. Si al cerrarlo devolviéramos 409 para el conocido
    y 403 para el desconocido, habríamos movido el oráculo, no cerrado. La
    respuesta debe ser **byte a byte la misma**.
    """
    await _reset(migrations_pg_dsn)
    async with _client(configured_app) as client:
        await client.post(
            "/auth/register",
            json={"email": "founder@example.com", "password": "longenoughpw"},
        )
        known = await client.post(
            "/auth/register",
            json={"email": "founder@example.com", "password": "longenoughpw"},
        )
        unknown = await client.post(
            "/auth/register",
            json={"email": "nobody-at-all@example.com", "password": "longenoughpw"},
        )

    assert known.status_code == 403, known.text
    assert unknown.status_code == known.status_code
    assert known.json() == unknown.json()


@pytest.mark.asyncio
async def test_unknown_invitation_token_is_the_same_403(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Un token inventado no distingue: mismo 403 que no traer ninguno."""
    await _reset(migrations_pg_dsn)
    async with _client(configured_app) as client:
        await client.post(
            "/auth/register",
            json={"email": "founder@example.com", "password": "longenoughpw"},
        )
        no_token = await client.post(
            "/auth/register",
            json={"email": "x@example.com", "password": "longenoughpw"},
        )
        bad_token = await client.post(
            "/auth/register",
            json={
                "email": "x@example.com",
                "password": "longenoughpw",
                "invitation_token": "aainv_deadbeef_totally-made-up",
            },
        )

    assert bad_token.status_code == 403
    assert bad_token.json() == no_token.json()


# ---------------------------------------------------------------------------
# 3. Canje: alta + membresía
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_valid_invitation_creates_user_and_membership(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Una feature no está hecha porque exista el mecanismo, sino cuando alguien
    ve el resultado: el invitado tiene que quedar DENTRO de su tenant."""
    from api_server.auth.invitations import generate_invitation_token

    await _reset(migrations_pg_dsn)
    tenant_id = await _seed_tenant(migrations_pg_dsn)
    minted = generate_invitation_token()

    async with _client(configured_app) as client:
        await client.post(
            "/auth/register",
            json={"email": "founder@example.com", "password": "longenoughpw"},
        )
        await _insert_invitation(
            migrations_pg_dsn,
            tenant_id=tenant_id,
            email="invitee@example.com",
            token_hash=minted.token_hash,
            token_prefix=minted.prefix,
            role="tenant_admin",
        )
        resp = await client.post(
            "/auth/register",
            json={
                "email": "invitee@example.com",
                "password": "longenoughpw",
                "invitation_token": minted.token,
            },
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    # Un invitado NUNCA hereda los poderes del arranque.
    assert body["is_system_admin"] is False
    assert body["is_system_owner"] is False

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT m.tenant_id, m.role, m.is_active FROM user_org_memberships m"
            " JOIN users u ON u.id = m.user_id WHERE u.email = 'invitee@example.com'"
        )
        invitation = await conn.fetchrow(
            "SELECT redeemed_at, redeemed_by_user_id FROM user_invitations"
            " WHERE token_hash = $1",
            minted.token_hash,
        )
    finally:
        await conn.close()

    assert row is not None, "el canje no creó la membresía: el invitado vería no_access"
    assert str(row["tenant_id"]) == tenant_id
    assert row["role"] == "tenant_admin"
    assert row["is_active"] is True
    # La invitación queda sellada y apuntando a quién entró con ella.
    assert invitation["redeemed_at"] is not None
    assert str(invitation["redeemed_by_user_id"]) == body["id"]


@pytest.mark.asyncio
async def test_invitation_email_must_match_the_registered_email(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Si no, una invitación filtrada dejaría entrar a cualquiera con el correo
    que le apeteciera elegir."""
    from api_server.auth.invitations import generate_invitation_token

    await _reset(migrations_pg_dsn)
    tenant_id = await _seed_tenant(migrations_pg_dsn)
    minted = generate_invitation_token()

    async with _client(configured_app) as client:
        await client.post(
            "/auth/register",
            json={"email": "founder@example.com", "password": "longenoughpw"},
        )
        await _insert_invitation(
            migrations_pg_dsn,
            tenant_id=tenant_id,
            email="invitee@example.com",
            token_hash=minted.token_hash,
            token_prefix=minted.prefix,
        )
        resp = await client.post(
            "/auth/register",
            json={
                "email": "someone-else@example.com",
                "password": "longenoughpw",
                "invitation_token": minted.token,
            },
        )

    assert resp.status_code == 403, resp.text
    assert await _count_users(migrations_pg_dsn) == 1


@pytest.mark.asyncio
async def test_invitation_email_match_is_case_insensitive(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Los emails se normalizan a minúsculas al registrar; la comparación de la
    invitación tiene que normalizar igual o un invitado que teclee su correo
    con mayúsculas se quedaría fuera sin entender por qué."""
    from api_server.auth.invitations import generate_invitation_token

    await _reset(migrations_pg_dsn)
    tenant_id = await _seed_tenant(migrations_pg_dsn)
    minted = generate_invitation_token()

    async with _client(configured_app) as client:
        await client.post(
            "/auth/register",
            json={"email": "founder@example.com", "password": "longenoughpw"},
        )
        await _insert_invitation(
            migrations_pg_dsn,
            tenant_id=tenant_id,
            email="invitee@example.com",
            token_hash=minted.token_hash,
            token_prefix=minted.prefix,
        )
        resp = await client.post(
            "/auth/register",
            json={
                "email": "Invitee@Example.com",
                "password": "longenoughpw",
                "invitation_token": minted.token,
            },
        )

    assert resp.status_code == 201, resp.text
    assert resp.json()["email"] == "invitee@example.com"


# ---------------------------------------------------------------------------
# 4. Un solo uso + caducidad + revocación
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_invitation_is_single_use(configured_app, migrations_pg_dsn: str) -> None:
    from api_server.auth.invitations import generate_invitation_token

    await _reset(migrations_pg_dsn)
    tenant_id = await _seed_tenant(migrations_pg_dsn)
    minted = generate_invitation_token()

    async with _client(configured_app) as client:
        await client.post(
            "/auth/register",
            json={"email": "founder@example.com", "password": "longenoughpw"},
        )
        await _insert_invitation(
            migrations_pg_dsn,
            tenant_id=tenant_id,
            email="invitee@example.com",
            token_hash=minted.token_hash,
            token_prefix=minted.prefix,
        )
        first = await client.post(
            "/auth/register",
            json={
                "email": "invitee@example.com",
                "password": "longenoughpw",
                "invitation_token": minted.token,
            },
        )
        assert first.status_code == 201, first.text

        # Mismo token, otro email: canjeada es inservible.
        second = await client.post(
            "/auth/register",
            json={
                "email": "second-comer@example.com",
                "password": "longenoughpw",
                "invitation_token": minted.token,
            },
        )

    assert second.status_code == 403, second.text
    # founder + invitee, y nadie más.
    assert await _count_users(migrations_pg_dsn) == 2


@pytest.mark.asyncio
async def test_expired_invitation_is_rejected(configured_app, migrations_pg_dsn: str) -> None:
    from api_server.auth.invitations import generate_invitation_token

    await _reset(migrations_pg_dsn)
    tenant_id = await _seed_tenant(migrations_pg_dsn)
    minted = generate_invitation_token()

    async with _client(configured_app) as client:
        await client.post(
            "/auth/register",
            json={"email": "founder@example.com", "password": "longenoughpw"},
        )
        await _insert_invitation(
            migrations_pg_dsn,
            tenant_id=tenant_id,
            email="invitee@example.com",
            token_hash=minted.token_hash,
            token_prefix=minted.prefix,
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        resp = await client.post(
            "/auth/register",
            json={
                "email": "invitee@example.com",
                "password": "longenoughpw",
                "invitation_token": minted.token,
            },
        )

    assert resp.status_code == 403, resp.text
    assert await _count_users(migrations_pg_dsn) == 1


@pytest.mark.asyncio
async def test_revoked_invitation_is_rejected(configured_app, migrations_pg_dsn: str) -> None:
    from api_server.auth.invitations import generate_invitation_token

    await _reset(migrations_pg_dsn)
    tenant_id = await _seed_tenant(migrations_pg_dsn)
    minted = generate_invitation_token()

    async with _client(configured_app) as client:
        await client.post(
            "/auth/register",
            json={"email": "founder@example.com", "password": "longenoughpw"},
        )
        await _insert_invitation(
            migrations_pg_dsn,
            tenant_id=tenant_id,
            email="invitee@example.com",
            token_hash=minted.token_hash,
            token_prefix=minted.prefix,
            revoked_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        resp = await client.post(
            "/auth/register",
            json={
                "email": "invitee@example.com",
                "password": "longenoughpw",
                "invitation_token": minted.token,
            },
        )

    assert resp.status_code == 403, resp.text
    assert await _count_users(migrations_pg_dsn) == 1


# ---------------------------------------------------------------------------
# 5. El token nunca se persiste en claro
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_admin_can_issue_list_and_revoke_invitations(
    configured_app, migrations_pg_dsn: str
) -> None:
    """El circuito completo del admin, y la propiedad que lo hace seguro: el
    token en claro se devuelve UNA vez al emitir y no vuelve a aparecer ni en la
    BD ni en el listado."""
    await _reset(migrations_pg_dsn)
    tenant_id = await _seed_tenant(migrations_pg_dsn)

    async with _client(configured_app) as client:
        reg = await client.post(
            "/auth/register",
            json={"email": "founder@example.com", "password": "longenoughpw"},
        )
        assert reg.status_code == 201, reg.text
        login = await client.post(
            "/auth/login",
            json={"email": "founder@example.com", "password": "longenoughpw"},
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        issued = await client.post(
            "/admin/invitations",
            headers=headers,
            json={
                "email": "Invitee@Example.com",
                "tenant_id": tenant_id,
                "role": "tenant_user",
                "expires_in_hours": 48,
            },
        )
        assert issued.status_code == 201, issued.text
        body = issued.json()
        token = body["token"]
        assert token and token.startswith("aainv_")
        assert body["email"] == "invitee@example.com"  # normalizado
        assert body["role"] == "tenant_user"

        listed = await client.get("/admin/invitations", headers=headers)
        assert listed.status_code == 200, listed.text
        rows = listed.json()
        assert len(rows) == 1
        # El listado NUNCA devuelve el token: solo su prefijo.
        assert "token" not in rows[0]
        assert rows[0]["token_prefix"] == body["token_prefix"]
        assert rows[0]["status"] == "pending"

        revoked = await client.post(f"/admin/invitations/{body['id']}/revoke", headers=headers)
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["status"] == "revoked"

        # Y una invitación revocada ya no canjea nada.
        attempt = await client.post(
            "/auth/register",
            json={
                "email": "invitee@example.com",
                "password": "longenoughpw",
                "invitation_token": token,
            },
        )
    assert attempt.status_code == 403, attempt.text

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await conn.fetchrow("SELECT token_hash, token_prefix, email FROM user_invitations")
    finally:
        await conn.close()
    # El valor crudo NO está en ninguna columna de la fila.
    assert token not in {row["token_hash"], row["token_prefix"], row["email"]}
    from api_server.auth.invitations import hash_invitation_token

    assert row["token_hash"] == hash_invitation_token(token)


@pytest.mark.asyncio
async def test_issuing_an_invitation_requires_a_system_admin(
    configured_app, migrations_pg_dsn: str
) -> None:
    """La guarda del router: un usuario corriente no emite invitaciones.

    Se comprueba SIN token también, para que el test no pueda pasar vacíamente
    por una ruta inexistente (un 404 delataría que el endpoint no está montado).
    """
    from api_server.auth.invitations import generate_invitation_token

    await _reset(migrations_pg_dsn)
    tenant_id = await _seed_tenant(migrations_pg_dsn)
    minted = generate_invitation_token()

    async with _client(configured_app) as client:
        await client.post(
            "/auth/register",
            json={"email": "founder@example.com", "password": "longenoughpw"},
        )
        await _insert_invitation(
            migrations_pg_dsn,
            tenant_id=tenant_id,
            email="plain@example.com",
            token_hash=minted.token_hash,
            token_prefix=minted.prefix,
        )
        await client.post(
            "/auth/register",
            json={
                "email": "plain@example.com",
                "password": "longenoughpw",
                "invitation_token": minted.token,
            },
        )
        login = await client.post(
            "/auth/login",
            json={"email": "plain@example.com", "password": "longenoughpw"},
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        anonymous = await client.post(
            "/admin/invitations",
            json={"email": "x@example.com", "tenant_id": tenant_id, "role": "tenant_user"},
        )
        as_plain_user = await client.post(
            "/admin/invitations",
            headers=headers,
            json={"email": "x@example.com", "tenant_id": tenant_id, "role": "tenant_user"},
        )

    assert anonymous.status_code == 401, anonymous.text
    assert as_plain_user.status_code == 403, as_plain_user.text
