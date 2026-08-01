"""prod-07 task_prod07_05 (llm-8): el chat del asistente no puede fugar el provider.

``get_assistant_model`` construye un ``LLMProvider`` por request. Hasta esta
tarea hacía ``return`` del modelo: el ``httpx.AsyncClient`` con su pool de
keep-alives quedaba a merced del recolector de basura, que en CPython lo cierra
«cuando le toca» y en un event loop ya cerrado ni eso. El patrón correcto ya
estaba en el repo (``factory.list_provider_models`` cierra en su ``finally``),
solo que el camino caliente —el chat— no lo usaba.

Lo que fijan estos tests:

  1. la dependencia es un **async generator**: FastAPI la ejecuta con un
     ``finally`` real después de enviar la respuesta;
  2. atravesando el app de verdad (sin ``dependency_overrides`` del modelo),
     el ``aclose()`` del provider se llama **una vez** por request;
  3. también se cierra cuando el turno **falla** — que es justo cuando más se
     fugaba, porque el camino de error no pasaba por ningún cierre;
  4. el builder plano sigue existiendo para el WebSocket de voz, que no puede
     usar una dependencia con ``yield``.

El DB es la BD desechable de integración (ver conftest). No se contacta con
ningún proveedor real: ``build_llm_provider`` se sustituye por un doble que
registra sus cierres.
"""

from __future__ import annotations

import inspect
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from shared_llm.types import CompletionResponse, Usage
from uuid6 import uuid7

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Doble de provider que cuenta sus cierres
# ---------------------------------------------------------------------------
class _RecordingProvider:
    """``LLMProvider`` mínimo que cuenta cuántas veces se le cerró."""

    name = "recording"

    def __init__(self, *, fail: Exception | None = None) -> None:
        self.closes = 0
        self.completes = 0
        self._fail = fail

    async def complete(self, messages: Any, **kwargs: Any) -> CompletionResponse:
        self.completes += 1
        if self._fail is not None:
            raise self._fail
        return CompletionResponse(
            content="hola",
            model="llama3.1",
            provider=self.name,
            usage=Usage(input_tokens=1, output_tokens=1),
        )

    def stream(self, messages: Any, **kwargs: Any) -> Any:  # pragma: no cover - sin uso
        raise NotImplementedError

    async def aclose(self) -> None:
        self.closes += 1


async def _seed(dsn: str) -> dict[str, UUID]:
    """Un tenant con el asistente ENCENDIDO, su Tenant Admin y un provider activo."""
    tenant, admin = uuid4(), uuid4()
    provider_id = uuid7()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE model_prices, llm_providers, platform_settings, tenant_settings,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug, personal_assistant_enabled)"
            " VALUES ($1, $2, $3, true)",
            tenant,
            "Tenant Teardown",
            "tenant-teardown",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_admin)"
            " VALUES ($1, $2, $3, false)",
            admin,
            "admin@teardown.test",
            "argon2-placeholder",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            tenant,
            admin,
        )
        await conn.execute(
            "INSERT INTO llm_providers (id, kind, slug, display_name, base_url, is_active, config)"
            " VALUES ($1, 'ollama', 'ollama-teardown', 'Ollama', 'http://ollama:11434/v1',"
            " true, $2::jsonb)",
            provider_id,
            '{"models": ["llama3.1"]}',
        )
    finally:
        await conn.close()
    return {"tenant": tenant, "admin": admin, "provider_id": provider_id}


async def _mint(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id, is_system_admin=False)


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _install_provider(monkeypatch: pytest.MonkeyPatch, provider: _RecordingProvider) -> None:
    """Sustituye la resolución + construcción del provider en el router.

    Se parchea el módulo del router (no el de origen) porque es donde viven los
    nombres que la dependencia usa.
    """
    from api_server.assistant.model_config import ResolvedAssistantModel
    from api_server.routers import assistant as assistant_router

    resolved = ResolvedAssistantModel(
        provider_id=uuid7(),
        model_id="ollama/llama3.1",
        source="platform_default",
        provider_kind="ollama",
        provider_display_name="Ollama",
        reasoning_effort=None,
    )

    async def _fake_resolve(_session: Any, _tenant_id: UUID) -> ResolvedAssistantModel:
        return resolved

    async def _fake_build(*_args: Any, **_kwargs: Any) -> _RecordingProvider:
        return provider

    monkeypatch.setattr(assistant_router, "resolve_assistant_model", _fake_resolve)
    monkeypatch.setattr(assistant_router, "build_llm_provider", _fake_build)


# ---------------------------------------------------------------------------
# 1. La forma de la dependencia
# ---------------------------------------------------------------------------
def test_get_assistant_model_is_an_async_generator_dependency() -> None:
    """Con ``return`` no hay sitio donde cerrar nada: el cierre EXIGE ``yield``.

    Este test es la guarda barata contra la regresión: revertir a ``return``
    vuelve a fugar el pool y esto se pone rojo sin necesidad de BD.
    """
    from api_server.routers.assistant import get_assistant_model

    assert inspect.isasyncgenfunction(get_assistant_model), (
        "get_assistant_model debe ser async generator (yield + finally: aclose); "
        "con `return` FastAPI no tiene teardown donde cerrar el provider"
    )


def test_the_plain_builder_still_exists_for_the_voice_socket() -> None:
    """El WS de voz no puede usar una dependencia con ``yield``: necesita el builder."""
    from api_server.routers.assistant import build_assistant_model

    assert inspect.iscoroutinefunction(build_assistant_model)
    assert not inspect.isasyncgenfunction(build_assistant_model)


# ---------------------------------------------------------------------------
# 2 y 3. El cierre de verdad, atravesando el app
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_chat_request_closes_the_provider_exactly_once(
    configured_app: Any, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    provider = _RecordingProvider()
    _install_provider(monkeypatch, provider)
    token = await _mint(seeded["admin"], seeded["tenant"])

    async with _client(configured_app) as client:
        resp = await client.post(
            "/assistant/chat",
            json={"message": "hola"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200, resp.text
    assert provider.completes >= 1, "el turno no llegó a usar el provider: el test no prueba nada"
    assert provider.closes == 1, f"se esperaba UN aclose() por request, hubo {provider.closes}"


@pytest.mark.asyncio
async def test_the_provider_is_closed_even_when_the_turn_fails(
    configured_app: Any, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El camino de error es el que más fugaba: nadie lo cerraba."""
    from shared_llm.exceptions import AuthError

    seeded = await _seed(migrations_pg_dsn)
    provider = _RecordingProvider(fail=AuthError("ollama: auth failed (401)"))
    _install_provider(monkeypatch, provider)
    token = await _mint(seeded["admin"], seeded["tenant"])

    async with _client(configured_app) as client:
        resp = await client.post(
            "/assistant/chat",
            json={"message": "hola"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 502, resp.text
    assert provider.closes == 1, f"el provider se fugó en el camino de error ({provider.closes})"


@pytest.mark.asyncio
async def test_two_requests_do_not_share_nor_leak_the_provider(
    configured_app: Any, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dos chats consecutivos → dos cierres. Si el cierre estuviera en el sitio
    equivocado (p.ej. al construir), el contador se quedaría en 1."""
    seeded = await _seed(migrations_pg_dsn)
    provider = _RecordingProvider()
    _install_provider(monkeypatch, provider)
    token = await _mint(seeded["admin"], seeded["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        first = await client.post("/assistant/chat", json={"message": "uno"}, headers=headers)
        second = await client.post("/assistant/chat", json={"message": "dos"}, headers=headers)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert provider.closes == 2, f"se esperaban 2 cierres (uno por request), hubo {provider.closes}"
