"""Integration tests for the personal-assistant model selection (ADR 0053).

Covers the end-to-end config surface the chat path resolves:

  * GET/PUT ``/assistant/model`` — the tenant override (set/clear, validated).
  * GET ``/assistant/model/options`` — the active-provider + model dropdown.
  * GET/PUT ``/assistant/default-model`` — the platform default (System Admin).
  * Inheritance: override wins; cleared override falls back to the platform
    default; nothing configured → the chat endpoint returns 503.
  * Validation: an inactive/unknown provider or an uncatalogued model is 422.
  * Cross-tenant isolation of the override.

The DB is the real throwaway ``agentic_platform_test`` (see conftest). We
seed one ACTIVE provider + one current ``model_prices`` row so a valid
selection exists.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration


async def _seed(dsn: str) -> dict[str, UUID]:
    """Two toggle-ON tenants (A, B) each with a Tenant Admin, a member in A,
    a System Admin user, plus one ACTIVE ollama provider and a current
    ``model_prices`` row (model ``llama3.1``) so a valid selection exists."""
    tenant_a, tenant_b = uuid4(), uuid4()
    admin_a, member_a, admin_b, sysadmin = uuid4(), uuid4(), uuid4(), uuid4()
    provider_id = uuid7()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE model_prices, llm_providers, platform_settings, tenant_settings,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug, personal_assistant_enabled) VALUES"
            " ($1, $2, $3, true), ($4, $5, $6, true)",
            tenant_a,
            "Tenant A",
            "tenant-a-model",
            tenant_b,
            "Tenant B",
            "tenant-b-model",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_admin) VALUES"
            " ($1, $2, $3, false), ($4, $5, $6, false), ($7, $8, $9, false),"
            " ($10, $11, $12, true)",
            admin_a,
            "admin-a@model.test",
            "argon2-placeholder",
            member_a,
            "member-a@model.test",
            "argon2-placeholder",
            admin_b,
            "admin-b@model.test",
            "argon2-placeholder",
            sysadmin,
            "sys@model.test",
            "argon2-placeholder",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, $4), ($5, $6, $7, $8), ($9, $10, $11, $12)",
            uuid4(),
            tenant_a,
            admin_a,
            "tenant_admin",
            uuid4(),
            tenant_a,
            member_a,
            "tenant_user",
            uuid4(),
            tenant_b,
            admin_b,
            "tenant_admin",
        )
        # One active provider with two SYNCED models in config.models (as if a
        # prior /sync-models ran) + one current price (catalogued 'llama3.1').
        await conn.execute(
            "INSERT INTO llm_providers (id, kind, display_name, base_url, is_active, config)"
            " VALUES ($1, 'ollama', 'Ollama local', 'http://ollama:11434/v1', true, $2::jsonb)",
            provider_id,
            '{"models": ["glm-5.1", "gemma3:4b"]}',
        )
        await conn.execute(
            "INSERT INTO model_prices"
            " (id, provider, model_id, input_price, output_price, source, provider_id,"
            "  effective_from)"
            " VALUES ($1, 'ollama', 'llama3.1', 0, 0, 'manual', $2, now())",
            uuid7(),
            provider_id,
        )
    finally:
        await conn.close()

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "admin_a": admin_a,
        "member_a": member_a,
        "admin_b": admin_b,
        "sysadmin": sysadmin,
        "provider_id": provider_id,
    }


async def _mint(user_id: UUID, tenant_id: UUID | None, *, system: bool = False) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id, is_system_admin=system)


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ===========================================================================
# GET default + set/clear override roundtrip
# ===========================================================================
@pytest.mark.asyncio
async def test_model_unset_then_set_override_then_clear(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        # Nothing configured yet.
        got = await client.get("/assistant/model", headers=headers)
        assert got.status_code == 200, got.text
        assert got.json() == {
            "provider_id": None,
            "model_id": None,
            "source": None,
            "provider_kind": None,
            "provider_display_name": None,
            "has_tenant_override": False,
        }

        # Set a valid override.
        put = await client.put(
            "/assistant/model",
            json={"provider_id": str(seeded["provider_id"]), "model_id": "llama3.1"},
            headers=headers,
        )
        assert put.status_code == 200, put.text
        body = put.json()
        assert body["source"] == "tenant_override"
        assert body["model_id"] == "llama3.1"
        assert body["provider_kind"] == "ollama"
        assert body["has_tenant_override"] is True

        # Persisted.
        got2 = await client.get("/assistant/model", headers=headers)
        assert got2.json()["source"] == "tenant_override"

        # Clear → back to nothing (no platform default set).
        cleared = await client.put(
            "/assistant/model",
            json={"provider_id": None, "model_id": None},
            headers=headers,
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["has_tenant_override"] is False
        assert cleared.json()["source"] is None


# ===========================================================================
# Validation
# ===========================================================================
@pytest.mark.asyncio
async def test_put_model_uncatalogued_model_is_422(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.put(
            "/assistant/model",
            json={"provider_id": str(seeded["provider_id"]), "model_id": "gpt-9-imaginary"},
            headers=headers,
        )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_put_model_unknown_provider_is_422(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.put(
            "/assistant/model",
            json={"provider_id": str(uuid4()), "model_id": "llama3.1"},
            headers=headers,
        )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_put_model_only_provider_without_model_is_422(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Schema rule: both fields or neither."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.put(
            "/assistant/model",
            json={"provider_id": str(seeded["provider_id"])},
            headers=headers,
        )
    assert resp.status_code == 422, resp.text


# ===========================================================================
# Options
# ===========================================================================
@pytest.mark.asyncio
async def test_model_options_lists_active_provider_and_models(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.get("/assistant/model/options", headers=headers)
    assert resp.status_code == 200, resp.text
    providers = resp.json()["providers"]
    assert len(providers) == 1
    assert providers[0]["provider_id"] == str(seeded["provider_id"])
    assert providers[0]["kind"] == "ollama"
    models = providers[0]["models"]
    # Catalogue model + the two synced models (config.models) all appear.
    assert "llama3.1" in models
    assert "glm-5.1" in models
    assert "gemma3:4b" in models


@pytest.mark.asyncio
async def test_put_model_accepts_a_synced_non_catalogued_model(
    configured_app, migrations_pg_dsn: str
) -> None:
    """A model the provider synced into config.models (but absent from the
    price catalogue) is a valid selection."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(configured_app) as client:
        resp = await client.put(
            "/assistant/model",
            json={"provider_id": str(seeded["provider_id"]), "model_id": "glm-5.1"},
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["model_id"] == "glm-5.1"
    assert resp.json()["source"] == "tenant_override"


# ===========================================================================
# Platform default + inheritance
# ===========================================================================
@pytest.mark.asyncio
async def test_platform_default_set_and_inherited_by_tenant(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    sys_token = await _mint(seeded["sysadmin"], None, system=True)
    sys_headers = {"Authorization": f"Bearer {sys_token}"}
    tenant_token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    tenant_headers = {"Authorization": f"Bearer {tenant_token}"}

    async with _client(configured_app) as client:
        # System Admin sets the platform default.
        put = await client.put(
            "/assistant/default-model",
            json={"provider_id": str(seeded["provider_id"]), "model_id": "llama3.1"},
            headers=sys_headers,
        )
        assert put.status_code == 200, put.text
        assert put.json()["is_valid"] is True

        # GET reflects it.
        got = await client.get("/assistant/default-model", headers=sys_headers)
        assert got.json()["model_id"] == "llama3.1"

        # A tenant with NO override inherits the platform default.
        eff = await client.get("/assistant/model", headers=tenant_headers)
        assert eff.status_code == 200, eff.text
        assert eff.json()["source"] == "platform_default"
        assert eff.json()["model_id"] == "llama3.1"
        assert eff.json()["has_tenant_override"] is False


@pytest.mark.asyncio
async def test_default_model_options_for_system_admin(
    configured_app, migrations_pg_dsn: str
) -> None:
    """The System-Admin default-model dropdown source lists active providers
    without needing a tenant context."""
    seeded = await _seed(migrations_pg_dsn)
    sys_token = await _mint(seeded["sysadmin"], None, system=True)
    async with _client(configured_app) as client:
        resp = await client.get(
            "/assistant/default-model/options",
            headers={"Authorization": f"Bearer {sys_token}"},
        )
    assert resp.status_code == 200, resp.text
    providers = resp.json()["providers"]
    assert any(
        p["provider_id"] == str(seeded["provider_id"]) and "llama3.1" in p["models"]
        for p in providers
    )


@pytest.mark.asyncio
async def test_default_model_put_requires_system_admin(
    configured_app, migrations_pg_dsn: str
) -> None:
    """A Tenant Admin cannot set the platform default."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.put(
            "/assistant/default-model",
            json={"provider_id": str(seeded["provider_id"]), "model_id": "llama3.1"},
            headers=headers,
        )
    assert resp.status_code == 403, resp.text


# ===========================================================================
# Sync models endpoint (System Admin)
# ===========================================================================
@pytest.mark.asyncio
async def test_sync_models_requires_system_admin(configured_app, migrations_pg_dsn: str) -> None:
    """A Tenant Admin cannot sync a provider's models."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    async with _client(configured_app) as client:
        resp = await client.post(
            f"/admin/llm-providers/{seeded['provider_id']}/sync-models",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_sync_models_degrades_gracefully_when_unreachable(
    configured_app, migrations_pg_dsn: str
) -> None:
    """The seeded provider points at an unreachable host, so discovery finds
    nothing — the endpoint returns 200 with count 0 (no crash) and leaves the
    existing config.models untouched."""
    seeded = await _seed(migrations_pg_dsn)
    sys_token = await _mint(seeded["sysadmin"], None, system=True)
    async with _client(configured_app) as client:
        resp = await client.post(
            f"/admin/llm-providers/{seeded['provider_id']}/sync-models",
            headers={"Authorization": f"Bearer {sys_token}"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["count"] == 0


# ===========================================================================
# Override wins over default; cross-tenant isolation
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_override_is_per_tenant_and_wins_over_default(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    sys_token = await _mint(seeded["sysadmin"], None, system=True)
    a_headers = {"Authorization": f"Bearer {await _mint(seeded['admin_a'], seeded['tenant_a'])}"}
    b_headers = {"Authorization": f"Bearer {await _mint(seeded['admin_b'], seeded['tenant_b'])}"}

    async with _client(configured_app) as client:
        # Platform default for everyone.
        await client.put(
            "/assistant/default-model",
            json={"provider_id": str(seeded["provider_id"]), "model_id": "llama3.1"},
            headers={"Authorization": f"Bearer {sys_token}"},
        )
        # Tenant A sets an override (same provider/model, but as an override).
        await client.put(
            "/assistant/model",
            json={"provider_id": str(seeded["provider_id"]), "model_id": "llama3.1"},
            headers=a_headers,
        )
        # A sees its override; B (no override) inherits the default.
        a_eff = (await client.get("/assistant/model", headers=a_headers)).json()
        b_eff = (await client.get("/assistant/model", headers=b_headers)).json()

    assert a_eff["source"] == "tenant_override"
    assert a_eff["has_tenant_override"] is True
    assert b_eff["source"] == "platform_default"
    assert b_eff["has_tenant_override"] is False


# ===========================================================================
# Access + chat-without-model
# ===========================================================================
@pytest.mark.asyncio
async def test_model_endpoint_member_is_403(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["member_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(configured_app) as client:
        resp = await client.get("/assistant/model", headers=headers)
    assert resp.status_code == 403, resp.text


def _install_raising_model(app, exc: Exception) -> None:
    """Override get_assistant_model with a model whose ``decide`` raises
    ``exc`` — simulates a provider call failing (e.g. Ollama 401)."""
    from api_server.routers.assistant import get_assistant_model

    class _RaisingModel:
        async def decide(self, state):
            raise exc

    app.dependency_overrides[get_assistant_model] = lambda: _RaisingModel()


@pytest.mark.asyncio
async def test_chat_provider_auth_error_is_502_not_500(
    configured_app, migrations_pg_dsn: str
) -> None:
    """A provider auth failure must surface as a handled 502 (which carries
    CORS headers and a helpful message) — NOT an unhandled 500 (which the
    browser sees as an opaque 'Failed to fetch')."""
    from shared_llm.exceptions import AuthError

    seeded = await _seed(migrations_pg_dsn)
    _install_raising_model(configured_app, AuthError("ollama: auth failed (401) unauthorized"))
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(configured_app) as client:
        resp = await client.post("/assistant/chat", json={"message": "hola"}, headers=headers)
    assert resp.status_code == 502, resp.text
    assert "auth" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_chat_without_configured_model_is_503(configured_app, migrations_pg_dsn: str) -> None:
    """No override, no platform default → the real ``get_assistant_model``
    resolves nothing and the chat endpoint returns a clear 503 (NOT a
    fabricated answer). ``get_assistant_model`` is NOT overridden here."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(configured_app) as client:
        resp = await client.post("/assistant/chat", json={"message": "hola"}, headers=headers)
    assert resp.status_code == 503, resp.text
    assert "model" in resp.json()["detail"].lower()
