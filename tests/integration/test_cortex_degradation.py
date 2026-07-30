"""Córtex F1 Tarea 5 — degradación honesta del modelo del córtex: 503, NUNCA 500.

El plan pedía este test de INTEGRACIÓN y nunca se escribió (auditoría del córtex
2026-07-27): la degradación sólo estaba probada a nivel del builder puro
(``tests/unit/test_cortex_model_factory.py``), que verifica que
``build_cortex_model`` levanta ``CortexModelUnavailableError``. Pero NADIE
ejercitaba el camino HTTP real, que es donde vive el defecto que importa: si el
router olvidase traducir esa excepción (o si la resolución del modelo se moviese
fuera del ``try``), el owner recibiría un **500** — un fallo opaco que parece un
bug de la plataforma — en vez del **503 honesto** que le dice exactamente qué le
falta al despliegue (el SDK, o un proveedor configurado).

Por qué 503 y no 500: un 500 es "me he roto"; un 503 es "no estoy disponible,
esto es lo que falta". La diferencia la paga el operador a las 3 de la mañana.

Dos ramas del mismo criterio de aceptación ("sin SDK ni alternativa → 503 claro;
nunca 500"):

  * ``test_503_when_claude_sdk_missing`` — el nombre EXACTO que enumera el plan:
    ``cortex.default_model`` apunta a un proveedor ``claude_sdk`` ACTIVO pero el
    Claude Agent SDK no está en este proceso (build sin ``WITH_CLAUDE=1``).
  * ``test_503_when_no_model_configured`` — la otra rama del 503 del builder:
    ``cortex.default_model`` sin configurar.

En ambos casos el owner es un System Owner legítimo (el gate 403 se prueba en
``test_cortex_turns_endpoint.py``): aquí lo que se afirma es que un despliegue
incompleto degrada limpio y NO se disfraza de error interno. ``get_cortex_model``
NO se sobreescribe — el objeto de este test es precisamente la dependencia real.
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration


async def _seed(dsn: str, *, model_configured: bool) -> dict[str, UUID]:
    """Un System Owner + un proveedor ``claude_sdk`` ACTIVO.

    ``model_configured`` decide si además se escribe la fila
    ``cortex.default_model`` en ``platform_settings`` (la selección se inserta
    directa como BYPASSRLS: el endpoint de escritura ya tiene su propio test en
    ``test_cortex_model_settings.py``)."""
    tenant_id = uuid4()
    owner_id = uuid4()
    provider_id = uuid7()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE llm_providers, platform_settings, cortex_turns, cortex_conversations,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            "Cortex Degradation Tenant",
            "cortex-degradation-tenant",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_admin, is_system_owner)"
            " VALUES ($1, $2, $3, true, true)",
            owner_id,
            "owner@cortex-degradation.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            tenant_id,
            owner_id,
        )
        # Proveedor claude_sdk ACTIVO: la resolución lo encuentra utilizable, así
        # que el 503 sólo puede venir de la AUSENCIA del SDK en este proceso.
        await conn.execute(
            "INSERT INTO llm_providers (id, kind, slug, display_name, is_active, config)"
            " VALUES ($1, 'claude_sdk', 'claude-sub', 'Claude (suscripción)', true, $2::jsonb)",
            provider_id,
            '{"models": ["claude-sonnet-4-5"]}',
        )
        if model_configured:
            await conn.execute(
                "INSERT INTO platform_settings (key, value) VALUES ('cortex.default_model',"
                " $1::jsonb)",
                json.dumps(
                    {
                        "provider_id": str(provider_id),
                        "model_id": "claude-sonnet-4-5",
                        "reasoning_effort": "high",
                    }
                ),
            )
    finally:
        await conn.close()

    return {"tenant_id": tenant_id, "owner_id": owner_id, "provider_id": provider_id}


async def _mint(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    await SessionStore(get_redis()).create(
        sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
    )
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id, is_system_owner=True)


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ===========================================================================
# claude_sdk configurado + SDK ausente en el proceso → 503 honesto
# ===========================================================================
@pytest.mark.asyncio
async def test_503_when_claude_sdk_missing(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un api-server sin el Claude Agent SDK y con el córtex apuntando a
    ``claude_sdk`` responde 503 al turno, no 500.

    El SDK se declara ausente donde el router lo consulta (``routers.cortex``
    importa ``_claude_sdk_available`` a su propio namespace), de modo que el test
    afirma lo mismo tanto en un build WITH_CLAUDE=1 como sin él."""
    seeded = await _seed(migrations_pg_dsn, model_configured=True)

    import api_server.routers.cortex as cortex_router

    monkeypatch.setattr(cortex_router, "_claude_sdk_available", lambda: False)

    token = await _mint(seeded["owner_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post(
            "/owner/cortex/turns", json={"message": "hola córtex"}, headers=headers
        )

    assert resp.status_code != 500, (
        "un despliegue sin el Claude Agent SDK debe degradar limpio, no romperse "
        f"con un ImportError disfrazado de 500: {resp.text}"
    )
    assert resp.status_code == 503, resp.text
    # El detalle tiene que decirle al operador QUÉ falta (no un texto genérico).
    detail = resp.json()["detail"]
    assert "claude_sdk" in detail
    assert "WITH_CLAUDE" in detail


# ===========================================================================
# Nada configurado → también 503 (la otra rama del builder)
# ===========================================================================
@pytest.mark.asyncio
async def test_503_when_no_model_configured(configured_app, migrations_pg_dsn: str) -> None:
    """Sin ``cortex.default_model`` el turno es 503, no 500 ni 200 con respuesta
    inventada: el córtex no tiene con qué deliberar y lo dice."""
    seeded = await _seed(migrations_pg_dsn, model_configured=False)
    token = await _mint(seeded["owner_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post(
            "/owner/cortex/turns", json={"message": "hola córtex"}, headers=headers
        )

    assert resp.status_code != 500, resp.text
    assert resp.status_code == 503, resp.text
    assert "cortex.default_model" in resp.json()["detail"]
