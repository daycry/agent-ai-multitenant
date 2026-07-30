"""Integration tests for `/admin/llm/copilot/device-flow` (Plan 11.2 task_11_2_03).

Drives the System-Admin GitHub-Copilot Device Flow surface end to end
against the real Postgres, with GitHub MOCKED via an injected httpx
transport (no network) and Vault faked with the in-memory store. Asserts
the ADR-0028 / CLAUDE.md security contract:

  * ``/start`` returns the operator codes (``user_code`` +
    ``verification_uri``) for a ``copilot`` provider;
  * ``/poll`` while pending keeps waiting (``status=pending``,
    ``authorized=false``) and writes NOTHING to Vault;
  * ``/poll`` once authorised writes the minted GitHub OAuth token to
    Vault at ``platform/llm/<provider_id>`` and NEVER returns it (not in
    the response body, not in the DB row);
  * ``slow_down`` backs off the interval; a non-copilot provider is a 422;
  * a ``tenant_admin`` CANNOT touch the surface (403 — ``require_system_admin``).

Fixture wiring mirrors ``test_llm_providers_admin.py``: seed via the
BYPASSRLS migrations role, mint a System-Admin JWT, drive via AsyncClient,
inject the in-memory Vault store and a mock GitHub transport via
``app.dependency_overrides``.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_OAUTH_TOKEN = "gho_super-secret-device-flow-token-DO-NOT-LEAK"

_DEVICE_CODE_URL = "https://github.com/login/device/code"
_OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token"


# ---------------------------------------------------------------------------
# Seed: one tenant with an admin, plus a System Admin user.
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant_a": uuid4(),
        "admin_a": uuid4(),
        "sysadmin": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE llm_providers, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            ids["tenant_a"],
            "Tenant A",
            "tenant-a-copilot",
        )
        await conn.execute(
            # prod-09 task_prod09_04: `require_system_admin` re-reads
            # `users.is_system_admin` from the DB, so the System Admin fixture
            # must actually CARRY the flag — a `sys` JWT claim over a row whose
            # flag is false is exactly the privilege the gate now refuses.
            "INSERT INTO users (id, email, password_hash, is_system_admin) VALUES"
            " ($1, $2, $3, false), ($4, $5, $6, true)",
            ids["admin_a"],
            "admin-a@copilot.test",
            "h",
            ids["sysadmin"],
            "sysadmin@copilot.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            ids["tenant_a"],
            ids["admin_a"],
        )
    finally:
        await conn.close()
    return ids


# ---------------------------------------------------------------------------
# A mock GitHub transport whose token-endpoint reply is steerable per test.
# ---------------------------------------------------------------------------
class _GitHubMock:
    """Mock GitHub device-flow endpoints; the token reply is mutable so a
    test can flip from 'pending' to 'authorized' between polls."""

    def __init__(self) -> None:
        # First the flow is pending; tests set this to authorise.
        self.token_reply: dict[str, object] = {"error": "authorization_pending"}
        self.device_code_calls = 0
        self.token_calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == _DEVICE_CODE_URL:
            self.device_code_calls += 1
            return httpx.Response(
                200,
                json={
                    "device_code": "dev-code-xyz",
                    "user_code": "WXYZ-1234",
                    "verification_uri": "https://github.com/login/device",
                    "expires_in": 900,
                    "interval": 5,
                },
            )
        if url == _OAUTH_TOKEN_URL:
            self.token_calls += 1
            return httpx.Response(200, json=self.token_reply)
        raise AssertionError(f"unexpected GitHub url {url}")

    def client_factory(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------
@pytest.fixture()
def in_memory_vault():
    from api_server.llm_providers.vault import InMemoryLLMProviderVaultStore

    return InMemoryLLMProviderVaultStore()


@pytest.fixture()
def github_mock() -> _GitHubMock:
    return _GitHubMock()


@pytest.fixture()
def configured_app(
    alembic_config,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    in_memory_vault,
    github_mock: _GitHubMock,
    monkeypatch: pytest.MonkeyPatch,
):
    command.upgrade(alembic_config, "head")

    from tests.integration.conftest import _flush_redis, _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())
    asyncio.run(_flush_redis(test_redis_url))

    monkeypatch.setenv("API_SERVER_DATABASE_URL", app_database_url)
    monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")

    from api_server.auth.deps import reset_redis_cache
    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache
    from api_server.routers.copilot_device_flow import get_device_flow_client_factory
    from api_server.routers.llm_providers import (
        get_provider_vault_store,
        reset_provider_vault_store_cache,
    )

    get_settings.cache_clear()
    reset_engine_cache()
    reset_redis_cache()
    reset_provider_vault_store_cache()

    from api_server.main import create_app

    app = create_app()
    app.dependency_overrides[get_provider_vault_store] = lambda: in_memory_vault
    app.dependency_overrides[get_device_flow_client_factory] = lambda: github_mock.client_factory
    try:
        yield app
    finally:
        app.dependency_overrides.clear()
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()
        reset_provider_vault_store_cache()


async def _mint_token(
    user_id: UUID, tenant_id: UUID | None, *, is_system_admin: bool = False
) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(
        user_id=user_id,
        session_id=sid,
        tenant_id=tenant_id,
        is_system_admin=is_system_admin,
    )


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _sysadmin_headers(seeded: dict[str, UUID]) -> dict[str, str]:
    token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)
    return {"Authorization": f"Bearer {token}"}


async def _create_copilot_provider(client: AsyncClient, headers: dict[str, str]) -> str:
    """Create a copilot provider via the CRUD surface; return its id."""
    resp = await client.post(
        "/admin/llm-providers",
        json={
            "kind": "copilot",
            "slug": "copilot",
            "display_name": "Copilot",
            "oauth_token": "bootstrap",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


# ===========================================================================
# Auth gate
# ===========================================================================
@pytest.mark.asyncio
async def test_start_unauthenticated_is_401(configured_app) -> None:
    async with _client(configured_app) as client:
        resp = await client.post(
            "/admin/llm/copilot/device-flow/start", json={"provider_id": str(uuid4())}
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_tenant_admin_forbidden(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"], is_system_admin=False)
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(configured_app) as client:
        resp = await client.post(
            "/admin/llm/copilot/device-flow/start",
            json={"provider_id": str(uuid4())},
            headers=headers,
        )
    assert resp.status_code == 403


# ===========================================================================
# /start — returns the operator codes
# ===========================================================================
@pytest.mark.asyncio
async def test_start_returns_user_code(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = await _sysadmin_headers(seeded)
    async with _client(configured_app) as client:
        pid = await _create_copilot_provider(client, headers)
        resp = await client.post(
            "/admin/llm/copilot/device-flow/start",
            json={"provider_id": pid},
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user_code"] == "WXYZ-1234"
    assert body["verification_uri"] == "https://github.com/login/device"
    assert body["device_code"] == "dev-code-xyz"
    assert body["interval"] == 5
    assert body["provider_id"] == pid


@pytest.mark.asyncio
async def test_start_rejects_non_copilot_provider(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = await _sysadmin_headers(seeded)
    async with _client(configured_app) as client:
        created = await client.post(
            "/admin/llm-providers",
            json={
                "kind": "ollama",
                "slug": "o",
                "display_name": "O",
                "base_url": "http://o:11434",
            },
            headers=headers,
        )
        pid = created.json()["id"]
        resp = await client.post(
            "/admin/llm/copilot/device-flow/start",
            json={"provider_id": pid},
            headers=headers,
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_start_unknown_provider_is_404(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = await _sysadmin_headers(seeded)
    async with _client(configured_app) as client:
        resp = await client.post(
            "/admin/llm/copilot/device-flow/start",
            json={"provider_id": str(uuid4())},
            headers=headers,
        )
    assert resp.status_code == 404


# ===========================================================================
# /poll — pending keeps waiting; authorized lands the token in Vault only
# ===========================================================================
@pytest.mark.asyncio
async def test_poll_pending_keeps_waiting_writes_nothing(
    configured_app, migrations_pg_dsn: str, github_mock: _GitHubMock, in_memory_vault
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = await _sysadmin_headers(seeded)
    github_mock.token_reply = {"error": "authorization_pending"}
    async with _client(configured_app) as client:
        pid = await _create_copilot_provider(client, headers)
        # The bootstrap credential from create() is in Vault; we assert the
        # poll does not OVERWRITE it while pending.
        before = in_memory_vault.read_secret(f"platform/llm/{pid}")
        resp = await client.post(
            "/admin/llm/copilot/device-flow/poll",
            json={"provider_id": pid, "device_code": "dev-code-xyz"},
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert body["authorized"] is False
    # Nothing new written: the Vault entry is unchanged by a pending poll.
    assert in_memory_vault.read_secret(f"platform/llm/{pid}") == before


@pytest.mark.asyncio
async def test_poll_slow_down_backs_off_interval(
    configured_app, migrations_pg_dsn: str, github_mock: _GitHubMock
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = await _sysadmin_headers(seeded)
    github_mock.token_reply = {"error": "slow_down"}
    async with _client(configured_app) as client:
        pid = await _create_copilot_provider(client, headers)
        resp = await client.post(
            "/admin/llm/copilot/device-flow/poll",
            json={"provider_id": pid, "device_code": "dev-code-xyz", "interval": 5},
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "slow_down"
    assert body["authorized"] is False
    assert body["interval"] == 10


@pytest.mark.asyncio
async def test_poll_authorized_stores_token_in_vault_never_returns_it(
    configured_app, migrations_pg_dsn: str, github_mock: _GitHubMock, in_memory_vault
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = await _sysadmin_headers(seeded)
    github_mock.token_reply = {"access_token": _OAUTH_TOKEN}
    async with _client(configured_app) as client:
        pid = await _create_copilot_provider(client, headers)
        resp = await client.post(
            "/admin/llm/copilot/device-flow/poll",
            json={"provider_id": pid, "device_code": "dev-code-xyz"},
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "authorized"
    assert body["authorized"] is True
    # The token is NEVER in the response.
    assert _OAUTH_TOKEN not in resp.text
    assert "token" not in body
    # The token IS in Vault at platform/llm/<id>, field oauth_token.
    stored = in_memory_vault.read_secret(f"platform/llm/{pid}")
    assert stored == {"oauth_token": _OAUTH_TOKEN}


@pytest.mark.asyncio
async def test_poll_authorized_secret_absent_from_db_row(
    configured_app, migrations_pg_dsn: str, github_mock: _GitHubMock
) -> None:
    """The minted token never appears in any column of the provider row."""
    seeded = await _seed(migrations_pg_dsn)
    headers = await _sysadmin_headers(seeded)
    github_mock.token_reply = {"access_token": _OAUTH_TOKEN}
    async with _client(configured_app) as client:
        pid = await _create_copilot_provider(client, headers)
        resp = await client.post(
            "/admin/llm/copilot/device-flow/poll",
            json={"provider_id": pid, "device_code": "dev-code-xyz"},
            headers=headers,
        )
    assert resp.status_code == 200, resp.text

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await conn.fetchrow("SELECT * FROM llm_providers WHERE id = $1", UUID(pid))
    finally:
        await conn.close()
    assert row is not None
    full_dump = " ".join(str(v) for v in dict(row).values())
    assert _OAUTH_TOKEN not in full_dump
    assert row["secret_vault_path"] == f"platform/llm/{pid}"


@pytest.mark.asyncio
async def test_poll_pending_then_authorized(
    configured_app, migrations_pg_dsn: str, github_mock: _GitHubMock, in_memory_vault
) -> None:
    """The realistic flow: a pending poll, then the operator authorises and
    the next poll lands the token in Vault."""
    seeded = await _seed(migrations_pg_dsn)
    headers = await _sysadmin_headers(seeded)
    async with _client(configured_app) as client:
        pid = await _create_copilot_provider(client, headers)

        github_mock.token_reply = {"error": "authorization_pending"}
        first = await client.post(
            "/admin/llm/copilot/device-flow/poll",
            json={"provider_id": pid, "device_code": "dev-code-xyz"},
            headers=headers,
        )
        assert first.json()["status"] == "pending"

        github_mock.token_reply = {"access_token": _OAUTH_TOKEN}
        second = await client.post(
            "/admin/llm/copilot/device-flow/poll",
            json={"provider_id": pid, "device_code": "dev-code-xyz"},
            headers=headers,
        )
    assert second.json()["authorized"] is True
    assert in_memory_vault.read_secret(f"platform/llm/{pid}") == {"oauth_token": _OAUTH_TOKEN}
