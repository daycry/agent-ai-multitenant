"""Córtex F1 — configuración por UI del modelo del córtex (``cortex.default_model``).

Ejercita los endpoints ``/owner/cortex/model*`` que dan al System Owner un selector
de modelo en el panel (igual que el modelo por defecto del asistente) en vez de
tener que escribir ``platform_settings`` a mano:

  * ``test_owner_model_unset_then_set_then_clear`` — el owner GET (sin configurar)
    → PUT con una selección válida → GET la devuelve (``is_valid=True``) → PUT clear
    → GET vuelve a sin configurar.
  * ``test_owner_model_options_lists_active_provider`` — ``/model-options`` lista el
    proveedor activo + sus modelos (misma fuente que el asistente, sin secretos).
  * ``test_owner_put_uncatalogued_model_is_422`` — un modelo fuera del catálogo es 422
    (reutiliza la validación del asistente, catálogo cerrado ADR 0021).
  * ``test_non_owner_gets_403`` — un ``tenant_admin`` que NO es System Owner (aunque
    forje el claim ``own``) recibe 403 en GET y PUT (el gate es DB-authoritative).

Reutiliza las fixtures compartidas del conftest (``configured_app`` migra la DB +
flushea Redis; ``migrations_pg_dsn`` siembra como BYPASSRLS).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration


async def _seed(dsn: str) -> dict[str, UUID]:
    """Un System Owner (que además es System Admin — el owner es el primer
    usuario del despliegue, ADR 0074, y ``set_platform_setting`` exige admin), un
    ``tenant_admin`` que NO es owner, y un proveedor ollama ACTIVO cuyos modelos
    sincronizados (``config.models``) son la lista elegible."""
    tenant_id = uuid4()
    owner_id, other_admin_id = uuid4(), uuid4()
    provider_id = uuid7()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE model_prices, llm_providers, platform_settings, tenant_settings,"
            " cortex_turns, cortex_conversations, user_org_memberships, organizations,"
            " users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            "Cortex Model Tenant",
            "cortex-model-tenant",
        )
        # The owner is also a System Admin (production reality: first user).
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_admin, is_system_owner)"
            " VALUES ($1, $2, $3, true, true), ($4, $5, $6, false, false)",
            owner_id,
            "owner@cortex-model.test",
            "h",
            other_admin_id,
            "admin@cortex-model.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, 'tenant_admin'), ($4, $5, $6, 'tenant_admin')",
            uuid4(),
            tenant_id,
            owner_id,
            uuid4(),
            tenant_id,
            other_admin_id,
        )
        # Active provider whose synced config.models is the selectable set.
        await conn.execute(
            "INSERT INTO llm_providers (id, kind, slug, display_name, base_url, is_active, config)"
            " VALUES ($1, 'ollama', 'ollama-local', 'Ollama local',"
            " 'http://ollama:11434/v1', true, $2::jsonb)",
            provider_id,
            '{"models": ["llama3.1", "glm-5.1"]}',
        )
    finally:
        await conn.close()

    return {
        "tenant_id": tenant_id,
        "owner_id": owner_id,
        "other_admin_id": other_admin_id,
        "provider_id": provider_id,
    }


async def _mint(user_id: UUID, tenant_id: UUID, *, owner_claim: bool = False) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    await SessionStore(get_redis()).create(
        sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
    )
    return encode_jwt(
        user_id=user_id, session_id=sid, tenant_id=tenant_id, is_system_owner=owner_claim
    )


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ===========================================================================
# Owner roundtrip: unset → set → clear
# ===========================================================================
@pytest.mark.asyncio
async def test_owner_model_unset_then_set_then_clear(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["owner_id"], seeded["tenant_id"], owner_claim=True)
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        # Nothing configured yet.
        got = await client.get("/owner/cortex/model", headers=headers)
        assert got.status_code == 200, got.text
        assert got.json() == {
            "provider_id": None,
            "model_id": None,
            "is_valid": False,
            "provider_display_name": None,
            "reasoning_effort": None,
        }

        # Set a valid selection (with a reasoning effort valid for ollama).
        put = await client.put(
            "/owner/cortex/model",
            json={
                "provider_id": str(seeded["provider_id"]),
                "model_id": "llama3.1",
                "reasoning_effort": "high",
            },
            headers=headers,
        )
        assert put.status_code == 200, put.text
        body = put.json()
        assert body["provider_id"] == str(seeded["provider_id"])
        assert body["model_id"] == "llama3.1"
        assert body["is_valid"] is True
        assert body["provider_display_name"] == "Ollama local"
        assert body["reasoning_effort"] == "high"

        # GET reflects the stored selection.
        got2 = await client.get("/owner/cortex/model", headers=headers)
        assert got2.status_code == 200, got2.text
        assert got2.json()["model_id"] == "llama3.1"
        assert got2.json()["is_valid"] is True
        assert got2.json()["reasoning_effort"] == "high"

        # Clear → back to unset.
        cleared = await client.put(
            "/owner/cortex/model",
            json={"provider_id": None, "model_id": None},
            headers=headers,
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["provider_id"] is None

        got3 = await client.get("/owner/cortex/model", headers=headers)
        assert got3.json()["provider_id"] is None
        assert got3.json()["is_valid"] is False


# ===========================================================================
# Options reuse the assistant builder (active provider + synced models)
# ===========================================================================
@pytest.mark.asyncio
async def test_owner_model_options_lists_active_provider(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["owner_id"], seeded["tenant_id"], owner_claim=True)
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.get("/owner/cortex/model-options", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    providers = body["providers"]
    assert len(providers) == 1
    assert providers[0]["provider_id"] == str(seeded["provider_id"])
    assert providers[0]["kind"] == "ollama"
    assert set(providers[0]["models"]) == {"llama3.1", "glm-5.1"}
    # ADR 0070: opciones de razonamiento por kind activo.
    assert body["reasoning_by_kind"]["ollama"] == ["off", "low", "medium", "high"]


# ===========================================================================
# Validation reuses the assistant's closed catalogue (ADR 0021)
# ===========================================================================
@pytest.mark.asyncio
async def test_owner_put_uncatalogued_model_is_422(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["owner_id"], seeded["tenant_id"], owner_claim=True)
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.put(
            "/owner/cortex/model",
            json={"provider_id": str(seeded["provider_id"]), "model_id": "gpt-9-imaginary"},
            headers=headers,
        )
    assert resp.status_code == 422, resp.text


# ===========================================================================
# DB-authoritative owner gate: a non-owner tenant_admin is 403 on GET + PUT
# ===========================================================================
@pytest.mark.asyncio
async def test_non_owner_gets_403(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    # tenant_admin but NOT system owner — forge the `own` claim; the DB must reject.
    token = await _mint(seeded["other_admin_id"], seeded["tenant_id"], owner_claim=True)
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        got = await client.get("/owner/cortex/model", headers=headers)
        assert got.status_code == 403, got.text

        opts = await client.get("/owner/cortex/model-options", headers=headers)
        assert opts.status_code == 403, opts.text

        put = await client.put(
            "/owner/cortex/model",
            json={"provider_id": str(seeded["provider_id"]), "model_id": "llama3.1"},
            headers=headers,
        )
        assert put.status_code == 403, put.text
