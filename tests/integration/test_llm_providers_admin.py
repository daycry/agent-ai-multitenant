"""Integration tests for `/admin/llm-providers` (Plan 11.2 task_11_2_02).

Drives the System-Admin provider CRUD + test-connection surface end to end
against the real Postgres, asserting the ADR-0028 / CLAUDE.md security
contract at every step:

  * CRUD persists (create / list / get / update / delete) for a System
    Admin on the BYPASSRLS admin session;
  * the credential lands in **Vault** and is ABSENT from BOTH the API
    response (no secret field; only ``has_credential`` + the pointer
    ``secret_vault_path``) AND the DB row (no column holds the value);
  * a ``tenant_admin`` / plain ``tenant_user`` CANNOT touch the surface
    (403 — ``require_system_admin``);
  * ``/test`` returns ok (Ollama 2xx) and classified errors (auth /
    connection / config) WITHOUT leaking the secret.

Vault is faked with an in-memory store injected via
``app.dependency_overrides`` (hvac need not be installed). The liveness
probe is driven with a mock httpx transport so no network is hit. A final
test asserts the secret value never appears in the raw DB row's text dump.

Fixture wiring mirrors ``test_prices_endpoints.py`` (seed via the BYPASSRLS
migrations role, mint JWTs incl. a System-Admin ``sys`` token, drive via
AsyncClient).
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

_OAUTH_TOKEN = "super-secret-oauth-token-DO-NOT-LEAK-claude"
_API_KEY = "super-secret-apim-key-DO-NOT-LEAK-azure"
_BEARER = "super-secret-bearer-DO-NOT-LEAK-ollama"


# ---------------------------------------------------------------------------
# Seed: one tenant with an admin + a plain member, plus a System Admin user.
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant_a": uuid4(),
        "admin_a": uuid4(),
        "member_a": uuid4(),
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
            "tenant-a-llm",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            ids["admin_a"],
            "admin-a@llm.test",
            "h",
            ids["member_a"],
            "member-a@llm.test",
            "h",
            ids["sysadmin"],
            "sysadmin@llm.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, 'tenant_admin'), ($4, $5, $6, 'tenant_user')",
            uuid4(),
            ids["tenant_a"],
            ids["admin_a"],
            uuid4(),
            ids["tenant_a"],
            ids["member_a"],
        )
    finally:
        await conn.close()
    return ids


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------
@pytest.fixture()
def in_memory_vault():
    """A fresh in-memory provider Vault store for each test."""
    from api_server.llm_providers.vault import InMemoryLLMProviderVaultStore

    return InMemoryLLMProviderVaultStore()


@pytest.fixture()
def configured_app(
    alembic_config,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    in_memory_vault,
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
    # Inject the in-memory Vault store so the secret is captured, never a
    # real hvac client (which is not installed in this dev venv).
    app.dependency_overrides[get_provider_vault_store] = lambda: in_memory_vault
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


# ===========================================================================
# Auth gate
# ===========================================================================
@pytest.mark.asyncio
async def test_unauthenticated_is_401(configured_app) -> None:
    async with _client(configured_app) as client:
        resp = await client.get("/admin/llm-providers")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_tenant_admin_forbidden(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"], is_system_admin=False)
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(configured_app) as client:
        listed = await client.get("/admin/llm-providers", headers=headers)
        created = await client.post(
            "/admin/llm-providers",
            json={"kind": "ollama", "display_name": "X", "base_url": "http://o:11434"},
            headers=headers,
        )
    assert listed.status_code == 403
    assert created.status_code == 403


@pytest.mark.asyncio
async def test_tenant_user_forbidden(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["member_a"], seeded["tenant_a"], is_system_admin=False)
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(configured_app) as client:
        resp = await client.get("/admin/llm-providers", headers=headers)
    assert resp.status_code == 403


# ===========================================================================
# Create + secret-to-Vault, never echoed
# ===========================================================================
@pytest.mark.asyncio
async def test_create_ollama_persists_no_secret(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = await _sysadmin_headers(seeded)
    async with _client(configured_app) as client:
        resp = await client.post(
            "/admin/llm-providers",
            json={
                "kind": "ollama",
                "display_name": "Ollama local",
                "base_url": "http://ollama:11434",
                "bearer_token": _BEARER,
            },
            headers=headers,
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["kind"] == "ollama"
    assert body["display_name"] == "Ollama local"
    assert body["base_url"] == "http://ollama:11434"
    assert body["is_active"] is True
    assert body["has_credential"] is True
    assert body["secret_vault_path"] == f"platform/llm/{body['id']}"
    # The secret VALUE is never in the response, anywhere.
    assert _BEARER not in resp.text
    assert "bearer_token" not in body
    assert "oauth_token" not in body
    assert "api_key" not in body


@pytest.mark.asyncio
async def test_create_writes_secret_to_vault(
    configured_app, migrations_pg_dsn: str, in_memory_vault
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = await _sysadmin_headers(seeded)
    async with _client(configured_app) as client:
        resp = await client.post(
            "/admin/llm-providers",
            json={
                "kind": "azure_foundry",
                "display_name": "Azure prod",
                "base_url": "https://apim.example.test",
                "api_key": _API_KEY,
            },
            headers=headers,
        )
    assert resp.status_code == 201, resp.text
    provider_id = resp.json()["id"]
    # The secret landed in Vault under platform/llm/<id> with field api_key.
    stored = in_memory_vault.read_secret(f"platform/llm/{provider_id}")
    assert stored == {"api_key": _API_KEY}


@pytest.mark.asyncio
async def test_secret_absent_from_db_row(configured_app, migrations_pg_dsn: str) -> None:
    """The credential value never appears in any column of the persisted row."""
    seeded = await _seed(migrations_pg_dsn)
    headers = await _sysadmin_headers(seeded)
    async with _client(configured_app) as client:
        resp = await client.post(
            "/admin/llm-providers",
            json={
                "kind": "claude_sdk",
                "display_name": "Claude prod",
                "oauth_token": _OAUTH_TOKEN,
            },
            headers=headers,
        )
    assert resp.status_code == 201, resp.text
    provider_id = UUID(resp.json()["id"])

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await conn.fetchrow("SELECT * FROM llm_providers WHERE id = $1", provider_id)
    finally:
        await conn.close()
    assert row is not None
    # No column anywhere holds the secret value — only the Vault pointer.
    full_dump = " ".join(str(v) for v in dict(row).values())
    assert _OAUTH_TOKEN not in full_dump
    assert row["secret_vault_path"] == f"platform/llm/{provider_id}"


@pytest.mark.asyncio
async def test_create_claude_requires_oauth_token(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = await _sysadmin_headers(seeded)
    async with _client(configured_app) as client:
        resp = await client.post(
            "/admin/llm-providers",
            json={"kind": "claude_sdk", "display_name": "Claude no-creds"},
            headers=headers,
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_azure_requires_base_url_and_key(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = await _sysadmin_headers(seeded)
    async with _client(configured_app) as client:
        no_url = await client.post(
            "/admin/llm-providers",
            json={"kind": "azure_foundry", "display_name": "Az", "api_key": _API_KEY},
            headers=headers,
        )
        no_key = await client.post(
            "/admin/llm-providers",
            json={
                "kind": "azure_foundry",
                "display_name": "Az",
                "base_url": "https://apim.example.test",
            },
            headers=headers,
        )
    assert no_url.status_code == 422
    assert no_key.status_code == 422


# ===========================================================================
# List / get
# ===========================================================================
@pytest.mark.asyncio
async def test_list_and_get(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = await _sysadmin_headers(seeded)
    async with _client(configured_app) as client:
        created = await client.post(
            "/admin/llm-providers",
            json={"kind": "ollama", "display_name": "O1", "base_url": "http://o:11434"},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        pid = created.json()["id"]

        listed = await client.get("/admin/llm-providers", headers=headers)
        got = await client.get(f"/admin/llm-providers/{pid}", headers=headers)
    assert listed.status_code == 200
    assert any(p["id"] == pid for p in listed.json())
    assert got.status_code == 200
    assert got.json()["display_name"] == "O1"


@pytest.mark.asyncio
async def test_get_unknown_is_404(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = await _sysadmin_headers(seeded)
    async with _client(configured_app) as client:
        resp = await client.get(f"/admin/llm-providers/{uuid4()}", headers=headers)
    assert resp.status_code == 404


# ===========================================================================
# Update (edit fields + rotate credential)
# ===========================================================================
@pytest.mark.asyncio
async def test_update_fields(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = await _sysadmin_headers(seeded)
    async with _client(configured_app) as client:
        created = await client.post(
            "/admin/llm-providers",
            json={"kind": "ollama", "display_name": "O1", "base_url": "http://o:11434"},
            headers=headers,
        )
        pid = created.json()["id"]
        updated = await client.put(
            f"/admin/llm-providers/{pid}",
            json={"display_name": "O1 renamed", "is_active": False},
            headers=headers,
        )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["display_name"] == "O1 renamed"
    assert body["is_active"] is False
    assert body["base_url"] == "http://o:11434"  # untouched


@pytest.mark.asyncio
async def test_update_rotates_credential_in_vault(
    configured_app, migrations_pg_dsn: str, in_memory_vault
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = await _sysadmin_headers(seeded)
    async with _client(configured_app) as client:
        created = await client.post(
            "/admin/llm-providers",
            json={"kind": "claude_sdk", "display_name": "Claude", "oauth_token": "old-token"},
            headers=headers,
        )
        pid = created.json()["id"]
        assert in_memory_vault.read_secret(f"platform/llm/{pid}") == {"oauth_token": "old-token"}

        rotated = await client.put(
            f"/admin/llm-providers/{pid}",
            json={"oauth_token": _OAUTH_TOKEN},
            headers=headers,
        )
    assert rotated.status_code == 200, rotated.text
    assert _OAUTH_TOKEN not in rotated.text
    assert in_memory_vault.read_secret(f"platform/llm/{pid}") == {"oauth_token": _OAUTH_TOKEN}


@pytest.mark.asyncio
async def test_empty_update_is_422(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = await _sysadmin_headers(seeded)
    async with _client(configured_app) as client:
        created = await client.post(
            "/admin/llm-providers",
            json={"kind": "ollama", "display_name": "O", "base_url": "http://o:11434"},
            headers=headers,
        )
        pid = created.json()["id"]
        resp = await client.put(f"/admin/llm-providers/{pid}", json={}, headers=headers)
    assert resp.status_code == 422


# ===========================================================================
# Delete (removes the row + the Vault secret)
# ===========================================================================
@pytest.mark.asyncio
async def test_delete_removes_row_and_secret(
    configured_app, migrations_pg_dsn: str, in_memory_vault
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = await _sysadmin_headers(seeded)
    async with _client(configured_app) as client:
        created = await client.post(
            "/admin/llm-providers",
            json={"kind": "claude_sdk", "display_name": "Claude", "oauth_token": _OAUTH_TOKEN},
            headers=headers,
        )
        pid = created.json()["id"]
        assert in_memory_vault.has_secret(f"platform/llm/{pid}")

        deleted = await client.delete(f"/admin/llm-providers/{pid}", headers=headers)
        assert deleted.status_code == 204
        got = await client.get(f"/admin/llm-providers/{pid}", headers=headers)
    assert got.status_code == 404
    assert not in_memory_vault.has_secret(f"platform/llm/{pid}")


# ===========================================================================
# /test — liveness probe (ok + classified error)
# ===========================================================================
def _ollama_ok_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/api/tags")
        return httpx.Response(200, json={"models": []})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_probe_ok(configured_app, migrations_pg_dsn: str, monkeypatch) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = await _sysadmin_headers(seeded)

    # Patch the probe to use a mock transport so no network is hit.
    from api_server.llm_providers import liveness

    real_probe = liveness.probe_provider

    async def patched(*, kind, base_url, secret, http_client=None):  # type: ignore[no-untyped-def]
        async with httpx.AsyncClient(transport=_ollama_ok_transport()) as c:
            return await real_probe(kind=kind, base_url=base_url, secret=secret, http_client=c)

    monkeypatch.setattr("api_server.routers.llm_providers.probe_provider", patched)

    async with _client(configured_app) as client:
        created = await client.post(
            "/admin/llm-providers",
            json={"kind": "ollama", "display_name": "O", "base_url": "http://o:11434"},
            headers=headers,
        )
        pid = created.json()["id"]
        resp = await client.post(f"/admin/llm-providers/{pid}/test", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "ok"


@pytest.mark.asyncio
async def test_probe_auth_error(configured_app, migrations_pg_dsn: str, monkeypatch) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = await _sysadmin_headers(seeded)

    from api_server.llm_providers import liveness

    real_probe = liveness.probe_provider

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    async def patched(*, kind, base_url, secret, http_client=None):  # type: ignore[no-untyped-def]
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await real_probe(kind=kind, base_url=base_url, secret=secret, http_client=c)

    monkeypatch.setattr("api_server.routers.llm_providers.probe_provider", patched)

    async with _client(configured_app) as client:
        created = await client.post(
            "/admin/llm-providers",
            json={
                "kind": "azure_foundry",
                "display_name": "Az",
                "base_url": "https://apim.example.test",
                "api_key": _API_KEY,
            },
            headers=headers,
        )
        pid = created.json()["id"]
        resp = await client.post(f"/admin/llm-providers/{pid}/test", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["status"] == "auth_error"
    # The secret never leaks into the error detail.
    assert _API_KEY not in resp.text


@pytest.mark.asyncio
async def test_probe_connection_error(configured_app, migrations_pg_dsn: str, monkeypatch) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = await _sysadmin_headers(seeded)

    from api_server.llm_providers import liveness

    real_probe = liveness.probe_provider

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    async def patched(*, kind, base_url, secret, http_client=None):  # type: ignore[no-untyped-def]
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await real_probe(kind=kind, base_url=base_url, secret=secret, http_client=c)

    monkeypatch.setattr("api_server.routers.llm_providers.probe_provider", patched)

    async with _client(configured_app) as client:
        created = await client.post(
            "/admin/llm-providers",
            json={"kind": "ollama", "display_name": "O", "base_url": "http://unreachable:11434"},
            headers=headers,
        )
        pid = created.json()["id"]
        resp = await client.post(f"/admin/llm-providers/{pid}/test", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "connection_error"


@pytest.mark.asyncio
async def test_probe_claude_credential_present(configured_app, migrations_pg_dsn: str) -> None:
    """claude_sdk has no live endpoint — ok means the credential is in Vault."""
    seeded = await _seed(migrations_pg_dsn)
    headers = await _sysadmin_headers(seeded)
    async with _client(configured_app) as client:
        created = await client.post(
            "/admin/llm-providers",
            json={"kind": "claude_sdk", "display_name": "Claude", "oauth_token": _OAUTH_TOKEN},
            headers=headers,
        )
        pid = created.json()["id"]
        resp = await client.post(f"/admin/llm-providers/{pid}/test", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "ok"
    assert _OAUTH_TOKEN not in resp.text
