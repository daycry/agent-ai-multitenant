"""/admin/platform-settings — edit operator-tunable platform defaults.

Drives the System-Admin surface end to end against the real Postgres:
registry shape, listing values, persisting a valid value (incl. the
``model.default_config`` model spec), and the 422/404 guards. A System-Admin
user is seeded so the PUT (which loads the actor) succeeds.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration


async def _seed_sysadmin(dsn: str) -> UUID:
    sysadmin = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("TRUNCATE platform_settings RESTART IDENTITY CASCADE")
        await conn.execute("DELETE FROM users WHERE email = $1", "psadmin@plat.test")
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_admin)"
            " VALUES ($1, $2, $3, true)",
            sysadmin,
            "psadmin@plat.test",
            "h",
        )
    finally:
        await conn.close()
    return sysadmin


async def _sysadmin_headers(user_id: UUID) -> dict[str, str]:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    await SessionStore(get_redis()).create(sid, user_id=user_id, tenant_id=None, ttl_seconds=3600)
    token = encode_jwt(user_id=user_id, session_id=sid, tenant_id=None, is_system_admin=True)
    return {"Authorization": f"Bearer {token}"}


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_registry_lists_model_default(configured_app, migrations_pg_dsn: str) -> None:
    user = await _seed_sysadmin(migrations_pg_dsn)
    headers = await _sysadmin_headers(user)
    async with _client(configured_app) as client:
        resp = await client.get("/admin/platform-settings/_registry", headers=headers)
    assert resp.status_code == 200, resp.text
    cats = resp.json()["categories"]
    assert cats["modelos"]["settings"]["model.default_config"]["type"] == "model_config"


@pytest.mark.asyncio
async def test_put_model_default_persists(configured_app, migrations_pg_dsn: str) -> None:
    user = await _seed_sysadmin(migrations_pg_dsn)
    headers = await _sysadmin_headers(user)
    body = {"value": {"provider": "ollama", "model": "qwen3-coder:480b", "temperature": 0.2}}
    async with _client(configured_app) as client:
        put = await client.put(
            "/admin/platform-settings/model.default_config", json=body, headers=headers
        )
        listed = await client.get("/admin/platform-settings", headers=headers)
    assert put.status_code == 200, put.text
    assert put.json()["value"]["model"] == "qwen3-coder:480b"
    by_key = {s["key"]: s for s in listed.json()}
    assert by_key["model.default_config"]["value"]["model"] == "qwen3-coder:480b"
    assert by_key["model.default_config"]["is_default"] is False


@pytest.mark.asyncio
async def test_put_invalid_model_is_422(configured_app, migrations_pg_dsn: str) -> None:
    user = await _seed_sysadmin(migrations_pg_dsn)
    headers = await _sysadmin_headers(user)
    async with _client(configured_app) as client:
        resp = await client.put(
            "/admin/platform-settings/model.default_config",
            json={"value": {"provider": "openai", "model": "gpt-4o"}},
            headers=headers,
        )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_put_int_out_of_bounds_is_422(configured_app, migrations_pg_dsn: str) -> None:
    user = await _seed_sysadmin(migrations_pg_dsn)
    headers = await _sysadmin_headers(user)
    async with _client(configured_app) as client:
        resp = await client.put(
            "/admin/platform-settings/max_review_retries",
            json={"value": 99},
            headers=headers,
        )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_put_unknown_key_is_404(configured_app, migrations_pg_dsn: str) -> None:
    user = await _seed_sysadmin(migrations_pg_dsn)
    headers = await _sysadmin_headers(user)
    async with _client(configured_app) as client:
        resp = await client.put(
            "/admin/platform-settings/does.not.exist", json={"value": 1}, headers=headers
        )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_model_options_uses_newest_active_provider_per_kind(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Per kind the dropdown shows ONLY the newest-active provider's models (what
    the agent dispatch resolves to), NOT a union across same-kind providers."""
    user = await _seed_sysadmin(migrations_pg_dsn)
    older, newer = uuid7(), uuid7()  # newer id => newest-active (id DESC) => dispatch target
    assert newer > older
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute("TRUNCATE llm_providers RESTART IDENTITY CASCADE")
        await conn.execute(
            "INSERT INTO llm_providers (id, kind, slug, display_name, base_url, is_active, config)"
            " VALUES ($1,'ollama','ollama-cloud','Ollama Cloud','https://ollama.com/v1',true,$2::jsonb)",
            older,
            '{"models": ["qwen3-coder:480b", "glm-5"]}',
        )
        await conn.execute(
            "INSERT INTO llm_providers (id, kind, slug, display_name, base_url, is_active, config)"
            " VALUES ($1,'ollama','ollama-local','Ollama Local','http://localhost:11434/v1',true,$2::jsonb)",
            newer,
            '{"models": ["llama3.2:1b"]}',
        )
    finally:
        await conn.close()
    headers = await _sysadmin_headers(user)
    async with _client(configured_app) as client:
        resp = await client.get("/admin/platform-settings/model-options", headers=headers)
    assert resp.status_code == 200, resp.text
    ollama_models = resp.json()["by_kind"]["ollama"]
    # Newest active = ollama-local -> only its model; cloud's are NOT unioned in.
    assert ollama_models == ["llama3.2:1b"]
    assert "glm-5" not in ollama_models
    assert "qwen3-coder:480b" not in ollama_models


@pytest.mark.asyncio
async def test_requires_system_admin(configured_app) -> None:
    async with _client(configured_app) as client:
        resp = await client.get("/admin/platform-settings/_registry")
    assert resp.status_code in (401, 403)
