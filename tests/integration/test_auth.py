"""End-to-end tests for the /auth/* router.

Covers:
  - POST /auth/register bootstraps the FIRST user (201 + UserResponse) —
    la única alta sin invitación que queda tras el ADR 0134.
  - POST /auth/register de cualquier otro devuelve un 403 genérico.
    El circuito completo de la invitación (emisión, canje, un solo uso,
    caducidad, membresía) vive en ``test_auth_invitations.py``.
  - POST /auth/login with valid credentials returns 200 + JWT/expires_in
    and provisions a server-side session in Redis.
  - POST /auth/login with bad password returns 401 (and never leaks
    whether the email exists).
  - POST /auth/login with an unknown email returns 401.
  - GET  /auth/me returns the principal's User row.
  - POST /auth/logout returns 204 and the JWT no longer works.
  - 6th login attempt within the rate-limit window returns 429.

Pre-condition: postgres and redis from docker-compose are healthy on
the host (15432 + 6379). The test session creates a throwaway DB,
flushes Redis DB 15, and tears both down on exit.
"""

from __future__ import annotations

import asyncio

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration


async def _truncate_users(dsn: str) -> None:
    """Wipe the users + memberships tables so first-user promotion
    behaves deterministically regardless of test ordering. The
    session-scoped test DB persists between tests, so per-test state
    has to be explicit."""
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("TRUNCATE user_org_memberships, users RESTART IDENTITY CASCADE")
    finally:
        await conn.close()


@pytest.fixture()
def configured_app(
    alembic_config,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    """Same shape as test_isolation.configured_app — duplicated here to
    keep each test module self-contained."""
    command.upgrade(alembic_config, "head")

    from tests.integration.conftest import _flush_redis, _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())
    asyncio.run(_flush_redis(test_redis_url))

    monkeypatch.setenv("API_SERVER_DATABASE_URL", app_database_url)
    # The local-login MFA probe (user_mfa_methods) runs on the ADMIN engine
    # (BYPASSRLS, no tenant context yet). Without this override the admin engine
    # falls back to the default DSN — locally that happens to be the migrated
    # dev DB (so it passed by accident), but in CI it points at an unmigrated DB
    # and the probe fails with `relation "user_mfa_totp" does not exist`. Point
    # it at the throwaway test DB, exactly like tests/integration/conftest.py.
    monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")
    # Force a tiny rate-limit window so test_rate_limit doesn't have
    # to wait 15 minutes in CI.
    monkeypatch.setenv("API_SERVER_LOGIN_RATE_LIMIT_COUNT", "5")
    monkeypatch.setenv("API_SERVER_LOGIN_RATE_LIMIT_WINDOW_SECONDS", "60")

    # Registro Prometheus PROPIO por test. `install_metrics` declara los
    # colectores contra `get_default_registry()`, que es el `REGISTRY` global del
    # proceso, así que el SEGUNDO `create_app()` de un mismo proceso pytest
    # revienta con `DuplicateTimeseries`. El módulo soporta inyectar un registro
    # pero su call-site no lo usa; mientras se arregla allí, aquí se hace lo que
    # su propio docstring dice ("cada test usa el suyo"). Inocuo tras el arreglo.
    from prometheus_client import CollectorRegistry

    monkeypatch.setattr(
        "api_server.metrics.get_default_registry",
        CollectorRegistry,
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


# ---------------------------------------------------------------------------
# /auth/register
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_register_first_user_becomes_system_admin(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Fresh install: the very first registered user is auto-promoted to system
    admin **y system owner** so the operator has a way in.

    Tras el ADR 0134 ésta es la ÚNICA alta que no exige invitación, y por eso es
    también la puerta de arranque de una instalación nueva: si dejara de
    funcionar, un despliegue recién levantado quedaría inaccesible para siempre.
    """
    await _truncate_users(migrations_pg_dsn)
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            "/auth/register",
            json={
                "email": "alice@example.com",
                "password": "correct-horse-battery-staple",
                "full_name": "Alice",
            },
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["email"] == "alice@example.com"
    assert body["full_name"] == "Alice"
    assert body["is_system_admin"] is True
    # ADR 0134 / ADR 0074: y también System Owner, o el córtex entero queda
    # inalcanzable en cuanto se cierra el registro público.
    assert body["is_system_owner"] is True
    assert body["is_active"] is True
    assert "id" in body
    # Password must never round-trip.
    assert "password" not in body
    assert "password_hash" not in body


@pytest.mark.asyncio
async def test_register_is_closed_once_a_user_exists(
    configured_app, migrations_pg_dsn: str
) -> None:
    """ADR 0134: con la tabla ``users`` poblada, el registro exige invitación.

    Éste era ``test_register_subsequent_user_is_not_admin`` y afirmaba que el
    segundo usuario se creaba (sin ser admin). Ya no se crea: el segundo usuario
    entra por ``/admin/invitations``. Que un invitado NO salga admin ni owner se
    comprueba en ``test_auth_invitations.py``, donde hay invitación de verdad
    que canjear.
    """
    await _truncate_users(migrations_pg_dsn)
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        # Seed a first user; this one becomes admin + owner.
        first = await client.post(
            "/auth/register",
            json={"email": "operator@example.com", "password": "longenoughpw"},
        )
        assert first.status_code == 201
        assert first.json()["is_system_admin"] is True

        # Anyone after is turned away, generically.
        resp = await client.post(
            "/auth/register",
            json={"email": "alice@example.com", "password": "longenoughpw"},
        )

    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_register_duplicate_email_no_longer_leaks_a_409(
    configured_app, migrations_pg_dsn: str
) -> None:
    """El oráculo de enumeración por 409 queda cerrado, no movido de sitio.

    Éste era ``test_register_duplicate_email_returns_409`` y fijaba justo el
    comportamiento que el ADR 0134 señala como problema: un email ya registrado
    devolvía 409 y uno nuevo 201, con lo que cualquiera podía confirmar
    direcciones. Sin invitación, ahora ambos casos devuelven la MISMA respuesta.
    """
    await _truncate_users(migrations_pg_dsn)
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        first = await client.post(
            "/auth/register",
            json={"email": "dup@example.com", "password": "longenoughpw"},
        )
        assert first.status_code == 201

        known = await client.post(
            "/auth/register",
            json={"email": "dup@example.com", "password": "anotherlongpw"},
        )
        unknown = await client.post(
            "/auth/register",
            json={"email": "never-seen@example.com", "password": "anotherlongpw"},
        )

    assert known.status_code == 403, known.text
    assert unknown.status_code == known.status_code
    assert known.json() == unknown.json()
    assert "already registered" not in known.text.lower()


# ---------------------------------------------------------------------------
# /auth/login
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_login_returns_jwt_with_expires_in(configured_app, migrations_pg_dsn: str) -> None:
    # ADR 0134: el alta solo pasa con `users` vacía (o con invitación), así que
    # los tests de login siembran su usuario por la puerta de arranque.
    await _truncate_users(migrations_pg_dsn)
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        await client.post(
            "/auth/register",
            json={"email": "bob@example.com", "password": "longenoughpw"},
        )
        resp = await client.post(
            "/auth/login",
            json={"email": "bob@example.com", "password": "longenoughpw"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    assert isinstance(body["access_token"], str) and body["access_token"]


@pytest.mark.asyncio
async def test_login_wrong_password_is_401(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_users(migrations_pg_dsn)
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        await client.post(
            "/auth/register",
            json={"email": "carol@example.com", "password": "rightpassword"},
        )
        resp = await client.post(
            "/auth/login",
            json={"email": "carol@example.com", "password": "wrongpassword"},
        )

    assert resp.status_code == 401
    # Generic message — no leak of "user exists".
    assert "invalid email or password" in resp.text.lower()


@pytest.mark.asyncio
async def test_login_unknown_email_is_401(configured_app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            "/auth/login",
            json={"email": "nobody@example.com", "password": "anything"},
        )

    assert resp.status_code == 401
    assert "invalid email or password" in resp.text.lower()


@pytest.mark.asyncio
async def test_login_writes_an_audit_trail(configured_app, migrations_pg_dsn: str) -> None:
    """AUD16-16 (F6): el login deja rastro en audit_log — el docstring de
    write_audit_log afirmaba 'called from login' pero ningún call site existía
    en auth (audit_log llevaba 0 filas en toda la historia). success y failure
    quedan registrados con ip y sin credenciales en el payload."""
    await _truncate_users(migrations_pg_dsn)
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute("DELETE FROM audit_log")
    finally:
        await conn.close()

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        await client.post(
            "/auth/register",
            json={"email": "audit-trail@example.com", "password": "rightpassword"},
        )
        ok = await client.post(
            "/auth/login",
            json={"email": "audit-trail@example.com", "password": "rightpassword"},
        )
        bad = await client.post(
            "/auth/login",
            json={"email": "audit-trail@example.com", "password": "wrongpassword"},
        )
    assert ok.status_code == 200 and bad.status_code == 401

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        rows = await conn.fetch(
            "SELECT action, user_id, ip_address, changes::text AS changes FROM audit_log"
            " WHERE action LIKE 'auth.login.%' ORDER BY id"
        )
    finally:
        await conn.close()

    successes = [r for r in rows if r["action"] == "auth.login.success"]
    failures = [r for r in rows if r["action"] == "auth.login.failure"]
    assert len(successes) == 1, rows
    assert successes[0]["user_id"] is not None
    assert successes[0]["ip_address"]
    assert len(failures) == 1, rows
    # El fallo referencia al usuario conocido pero JAMÁS lleva la contraseña.
    assert "password" not in (failures[0]["changes"] or "").lower()


# ---------------------------------------------------------------------------
# /auth/me
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_me_returns_user_info(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_users(migrations_pg_dsn)
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        reg = await client.post(
            "/auth/register",
            json={
                "email": "dave@example.com",
                "password": "longenoughpw",
                "full_name": "Dave",
            },
        )
        user_id = reg.json()["id"]

        login = await client.post(
            "/auth/login",
            json={"email": "dave@example.com", "password": "longenoughpw"},
        )
        token = login.json()["access_token"]

        me = await client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert me.status_code == 200, me.text
    body = me.json()
    assert body["id"] == user_id
    assert body["email"] == "dave@example.com"
    assert body["full_name"] == "Dave"


# ---------------------------------------------------------------------------
# /auth/logout
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_logout_revokes_session_immediately(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_users(migrations_pg_dsn)
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        await client.post(
            "/auth/register",
            json={"email": "eve@example.com", "password": "longenoughpw"},
        )
        login = await client.post(
            "/auth/login",
            json={"email": "eve@example.com", "password": "longenoughpw"},
        )
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Sanity: the token works before logout.
        ok = await client.get("/auth/me", headers=headers)
        assert ok.status_code == 200

        logout = await client.post("/auth/logout", headers=headers)
        assert logout.status_code == 204

        # Same token, now revoked.
        after = await client.get("/auth/me", headers=headers)
    assert after.status_code == 401
    assert "revoked" in after.text.lower()


# ---------------------------------------------------------------------------
# Rate limiting — auto_00_10_b
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_rate_limit(configured_app, migrations_pg_dsn: str) -> None:
    """The 6th login attempt within the window returns 429.

    Limit is 5/window per IP and per email; we hit the IP limit first
    because all attempts come from the same fake client.
    """
    await _truncate_users(migrations_pg_dsn)
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        await client.post(
            "/auth/register",
            json={"email": "frank@example.com", "password": "longenoughpw"},
        )

        # 5 attempts with the wrong password — each returns 401, none
        # should trigger 429 yet.
        for _ in range(5):
            r = await client.post(
                "/auth/login",
                json={"email": "frank@example.com", "password": "wrongpassword"},
            )
            assert r.status_code == 401, r.text

        # 6th attempt within the same window — limit tripped.
        sixth = await client.post(
            "/auth/login",
            json={"email": "frank@example.com", "password": "wrongpassword"},
        )

    assert sixth.status_code == 429, sixth.text
    assert "Retry-After" in sixth.headers
