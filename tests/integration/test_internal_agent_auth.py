"""Tests for `/internal/agent/*` sandbox auth (Plan 04.5 task_04_5_01).

Covers the three pieces shipped by the task:

  - :func:`mint_agent_token` -> :func:`decode_agent_token` round-trip.
  - The discriminator: a *human* JWT (no `kind=agent` claim) is
    rejected even when signed with the same secret.
  - The FastAPI dependency `get_agent_principal` works end-to-end:
    the smoke endpoint `/internal/agent/_health` returns 200 with a
    valid agent token + 401/403 for the rejection paths, including
    the soft-delete DB check.

Pre-condition: postgres test container is up (same fixtures as the
other integration tests).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from joserfc import jwt
from joserfc.jwk import OctKey

pytestmark = pytest.mark.integration


def _mint(claims: dict[str, object], *, secret: str, algorithm: str) -> str:
    """Sign a token with an ARBITRARY key — the point of these tests.

    Deliberately does not go through `sign_claims`: several cases here mint with
    the wrong secret or without the `kind` claim, which the production helper
    would never do. Uses `joserfc` directly since prod-09 task_prod09_17 retired
    python-jose from the tree.
    """
    signed: str = jwt.encode({"alg": algorithm, "typ": "JWT"}, claims, OctKey.import_key(secret))
    return signed


_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


async def _seed_agent(dsn: str) -> dict[str, UUID]:
    """Seed an org + project + agent so `_agent_exists` returns True
    for the minted token. Returns the ids the tests need."""
    tenant_id = uuid4()
    project_id = uuid4()
    agent_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE memory_entries, executions, tasks, plans, conversations,"
            " projects, agents, teams, user_org_memberships, organizations,"
            " users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_id,
            "Tenant Internal",
            "tenant-internal",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-internal",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)",
            project_id,
            tenant_id,
            "Internal Agent Project",
        )
        await conn.execute(
            "INSERT INTO agents"
            " (id, tenant_id, project_id, name, role, system_prompt, memory_scope, scope)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, 'project_local')",
            agent_id,
            tenant_id,
            project_id,
            "Test Agent",
            "backend_dev",
            "You are a test agent.",
            "private",
        )
    finally:
        await conn.close()
    return {
        "tenant_id": tenant_id,
        "project_id": project_id,
        "agent_id": agent_id,
    }


async def _soft_delete_agent(dsn: str, agent_id: UUID) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "UPDATE agents SET deleted_at = now() WHERE id = $1",
            agent_id,
        )
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
    """Same scaffolding pattern as the other integration tests."""
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
# Pure unit-ish: mint / decode round-trip + kind-claim guard
# ---------------------------------------------------------------------------
def test_mint_decode_roundtrip(configured_app) -> None:
    """A freshly-minted token decodes back to the same agent_id +
    tenant_id, and `kind=agent` survives the round-trip."""
    from api_server.auth.internal_agent import decode_agent_token, mint_agent_token

    agent_id = uuid4()
    tenant_id = uuid4()
    token = mint_agent_token(agent_id=agent_id, tenant_id=tenant_id)
    principal = decode_agent_token(token)

    assert principal.agent_id == agent_id
    assert principal.tenant_id == tenant_id
    # iat is freshly stamped; loosely check it's within a minute.
    assert abs((datetime.now(tz=UTC) - principal.issued_at).total_seconds()) < 60


def test_decode_rejects_human_jwt(configured_app) -> None:
    """A token signed with the same secret but lacking `kind=agent`
    must be rejected — humans must not be able to walk into
    /internal/agent/* with a normal session JWT."""
    from api_server.auth.internal_agent import (
        InvalidAgentTokenError,
        decode_agent_token,
    )
    from api_server.config import get_settings

    settings = get_settings()
    now = datetime.now(tz=UTC)
    human_claims = {
        "sub": str(uuid4()),
        "sid": str(uuid4()),
        "tid": str(uuid4()),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=15)).timestamp()),
    }
    human_token = _mint(
        human_claims,
        secret=settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )

    # Since prod-09 task_prod09_03 the two token families are signed with
    # DIFFERENT keys, so a human JWT no longer even reaches the `kind` check —
    # it dies at the signature. That is strictly stronger, so assert the
    # rejection without pinning the (now signature-level) message.
    with pytest.raises(InvalidAgentTokenError):
        decode_agent_token(human_token)

    # And the `kind=agent` discriminator still holds INSIDE the internal signing
    # domain: a token signed with the internal secret but without the claim
    # (i.e. an internal caller trying to pass a session-shaped token) is
    # rejected for what it is, not just for its signature.
    same_key_no_kind = _mint(
        human_claims,
        secret=settings.internal_token_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(InvalidAgentTokenError, match="not an agent token"):
        decode_agent_token(same_key_no_kind)


def test_decode_rejects_expired(configured_app) -> None:
    from api_server.auth.internal_agent import (
        InvalidAgentTokenError,
        mint_agent_token,
    )

    # Negative TTL = already expired.
    token = mint_agent_token(agent_id=uuid4(), tenant_id=uuid4(), ttl=timedelta(seconds=-1))
    with pytest.raises(InvalidAgentTokenError):
        from api_server.auth.internal_agent import decode_agent_token

        decode_agent_token(token)


def test_decode_rejects_malformed(configured_app) -> None:
    from api_server.auth.internal_agent import (
        InvalidAgentTokenError,
        decode_agent_token,
    )

    with pytest.raises(InvalidAgentTokenError):
        decode_agent_token("not-a-jwt")


def test_decode_rejects_wrong_signature(configured_app) -> None:
    """A token signed with a *different* secret is rejected."""
    from api_server.auth.internal_agent import (
        InvalidAgentTokenError,
        decode_agent_token,
    )
    from api_server.config import get_settings

    settings = get_settings()
    now = datetime.now(tz=UTC)
    claims = {
        "sub": str(uuid4()),
        "tid": str(uuid4()),
        "kind": "agent",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    forged = _mint(claims, secret="wrong-secret", algorithm=settings.jwt_algorithm)
    with pytest.raises(InvalidAgentTokenError):
        decode_agent_token(forged)


# ---------------------------------------------------------------------------
# End-to-end through the smoke endpoint
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_health_endpoint_accepts_valid_agent_token(
    configured_app, migrations_pg_dsn: str
) -> None:
    """A valid agent token (seeded agent + signed correctly) succeeds
    and the endpoint echoes back the principal."""
    from api_server.auth.internal_agent import mint_agent_token

    seeded = await _seed_agent(migrations_pg_dsn)
    token = mint_agent_token(agent_id=seeded["agent_id"], tenant_id=seeded["tenant_id"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.get(
            "/internal/agent/_health",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {
        "status": "ok",
        "agent_id": str(seeded["agent_id"]),
        "tenant_id": str(seeded["tenant_id"]),
    }


@pytest.mark.asyncio
async def test_health_endpoint_rejects_missing_header(configured_app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/internal/agent/_health")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_health_endpoint_rejects_human_jwt(configured_app, migrations_pg_dsn: str) -> None:
    """Defence in depth: even if a human's JWT leaks into a sandbox
    container, /internal/agent/* refuses it.

    Since prod-09 task_prod09_03 the rejection happens at the SIGNATURE (the two
    token families use different HMAC keys) instead of at the ``kind`` claim, so
    the assertion is on the 401 + the fact that the token bought nothing, not on
    the old message. The `kind` discriminator is still tested inside the internal
    signing domain by ``test_decode_rejects_a_human_jwt``."""
    from api_server.auth.jwt import encode_jwt

    seeded = await _seed_agent(migrations_pg_dsn)
    human_token = encode_jwt(
        user_id=uuid4(),
        session_id=uuid4(),
        tenant_id=seeded["tenant_id"],
    )

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.get(
            "/internal/agent/_health",
            headers={"Authorization": f"Bearer {human_token}"},
        )

    assert resp.status_code == 401
    assert "invalid agent token" in resp.text


@pytest.mark.asyncio
async def test_health_endpoint_rejects_soft_deleted_agent(
    configured_app, migrations_pg_dsn: str
) -> None:
    """If the agent is soft-deleted between mint and use, the
    dependency rejects with 403 even though the token signature is
    still valid."""
    from api_server.auth.internal_agent import mint_agent_token

    seeded = await _seed_agent(migrations_pg_dsn)
    token = mint_agent_token(agent_id=seeded["agent_id"], tenant_id=seeded["tenant_id"])

    await _soft_delete_agent(migrations_pg_dsn, seeded["agent_id"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.get(
            "/internal/agent/_health",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 403, resp.text
    assert "agent not found" in resp.text


@pytest.mark.asyncio
async def test_health_endpoint_rejects_token_with_wrong_tenant(
    configured_app, migrations_pg_dsn: str
) -> None:
    """A token for a real agent_id but the wrong tenant_id is rejected
    — the `(id, tenant_id)` AND in `_agent_exists` blocks the cross-
    tenant impersonation case."""
    from api_server.auth.internal_agent import mint_agent_token

    seeded = await _seed_agent(migrations_pg_dsn)
    token = mint_agent_token(
        agent_id=seeded["agent_id"],
        tenant_id=uuid4(),  # garbage tenant
    )

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.get(
            "/internal/agent/_health",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 403, resp.text
