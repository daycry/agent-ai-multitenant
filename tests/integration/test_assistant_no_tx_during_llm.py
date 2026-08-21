"""La transacción del request NO abarca el turno LLM (prod-13 `task_prod13_07`).

Qué se mide y por qué así
------------------------
El hallazgo perf-2/db-2 de la auditoría de producción no es «el asistente va
lento»: es que ``POST /assistant/chat`` retenía **una conexión del pool durante
todo el turno LLM** (hasta seis rondas de tools, cada una un ``complete()``
entero). Con el pool por defecto, ~15 chats concurrentes lo agotan y **toda** la
API —no solo el asistente— empieza a devolver `TimeoutError`.

La aserción tiene que ser sobre el POOL, no sobre el código: se cuenta
``engine.pool.checkedout()`` **desde dentro del modelo**, o sea en el instante
exacto en que el turno está esperando al LLM. Un test que sólo mirase la firma
del endpoint («ya no declara ``Depends(get_tenant_session)``») pasaría en verde
el día que una dependencia transitiva —hoy ``require_assistant_access``— vuelva
a abrir la sesión antes del handler, que es justo como estaba el defecto: no
basta con no USAR la sesión durante el turno, hay que no PEDIRLA.

Y la guarda contra el verde vacío (apartado 4 de
`verificar-antes-de-implementar`): se afirma que el modelo fue consultado al
menos dos veces y que las tools corrieron de verdad. Un doble que nunca se llama
no observa nada, y «ninguna observación» no es «ninguna conexión».

El último test es el que traduce la propiedad a su consecuencia (apartado 5:
un mecanismo sin efecto observable no está entregado): con el pool reducido a
UNA conexión, dos chats simultáneos tienen que salir los dos. Antes del cambio,
el segundo no llegaba ni a pasar la puerta de acceso.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    """Un tenant con el toggle del asistente ON, su Tenant Admin y un proyecto
    (para que la tool de lectura tenga algo real que devolver)."""
    tenant = uuid4()
    admin = uuid4()
    project = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE tasks, plans, messages, conversations, projects, agents,"
            " memory_entries, tenant_settings, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug, personal_assistant_enabled)"
            " VALUES ($1, $2, $3, true)",
            tenant,
            "Tenant NoTx",
            "tenant-notx",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            admin,
            "admin@notx.test",
            "argon2-placeholder",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, $4)",
            uuid4(),
            tenant,
            admin,
            "tenant_admin",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, is_template)"
            " VALUES ($1, $2, $3, $4, false)",
            project,
            tenant,
            "Proyecto NoTx",
            "active",
        )
    finally:
        await conn.close()

    return {"tenant": tenant, "admin": admin, "project": project}


async def _mint_token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


# ---------------------------------------------------------------------------
# El doble que mira el pool desde dentro del turno
# ---------------------------------------------------------------------------
class _PoolWatchingModel:
    """``ScriptedAssistantModel`` que, en CADA ``decide``, anota cuántas
    conexiones del pool de la aplicación están retenidas en ese instante.

    Ese instante es el que importa: el handler está esperando al modelo, que en
    producción es una llamada de red de segundos. Si ahí hay una conexión
    checked-out, es exactamente la que le falta a otro request."""

    def __init__(self, turns: list[Any]) -> None:
        from api_server.assistant.graph import ScriptedAssistantModel

        self._inner = ScriptedAssistantModel(turns=list(turns))
        self.checked_out: list[int] = []
        self.tool_results: list[list[dict[str, Any]]] = []
        self.histories: list[list[dict[str, Any]]] = []

    async def decide(self, state: Any) -> Any:
        from api_server.db.session import get_engine

        self.checked_out.append(get_engine().pool.checkedout())
        self.tool_results.append(list(state.tool_results))
        self.histories.append(list(state.chat_history))
        return await self._inner.decide(state)


def _install_model(app: Any, model: Any) -> None:
    from api_server.routers.assistant import get_assistant_model

    app.dependency_overrides[get_assistant_model] = lambda: model


def _two_round_script() -> list[Any]:
    """Ronda 1: una tool de lectura. Ronda 2: la respuesta final. Dos rondas a
    propósito — así el pool se observa ANTES y DESPUÉS de una llamada a tool,
    que es la otra mitad del riesgo (la tool abre su propia sesión corta y
    tiene que soltarla)."""
    from api_server.assistant.graph import ModelTurn, ToolInvocation

    return [
        ModelTurn(tool_calls=(ToolInvocation(name="tenant_projects_status"),)),
        ModelTurn(content="Tienes 1 proyecto activo."),
    ]


# ===========================================================================
# POST /assistant/chat
# ===========================================================================
@pytest.mark.asyncio
async def test_chat_holds_no_pooled_connection_while_the_llm_thinks(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    model = _PoolWatchingModel(_two_round_script())
    _install_model(configured_app, model)
    token = await _mint_token(seeded["admin"], seeded["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/assistant/chat",
            json={"message": "¿Cómo vamos?"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200, resp.text
    # La guarda contra el verde vacío: el modelo se consultó DE VERDAD, dos veces.
    assert len(model.checked_out) >= 2, model.checked_out
    assert model.checked_out == [0] * len(model.checked_out), (
        f"el turno LLM corre con una conexión del pool retenida: {model.checked_out}"
    )
    # …y la tool que corrió en medio SÍ vio los datos del tenant, o sea que la
    # sesión corta se abrió con el binding de tenant puesto.
    assert "tenant_projects_status" in resp.json()["tools_called"]
    assert model.tool_results[-1], "la tool no devolvió nada al modelo"
    assert model.tool_results[-1][-1]["result"]["total"] == 1, model.tool_results[-1]


@pytest.mark.asyncio
async def test_chat_still_persists_the_turn_after_splitting_the_transaction(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    """El riesgo de trocear la transacción es perder la escritura. El hilo y sus
    dos turnos tienen que quedar persistidos igual que antes."""
    seeded = await _seed(migrations_pg_dsn)
    model = _PoolWatchingModel(_two_round_script())
    _install_model(configured_app, model)
    token = await _mint_token(seeded["admin"], seeded["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/assistant/chat", json={"message": "¿Cómo vamos?"}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        conversation_id = resp.json()["conversation_id"]
        turns = await client.get(
            f"/assistant/conversations/{conversation_id}/turns", headers=headers
        )

    assert turns.status_code == 200, turns.text
    body = turns.json()
    assert [t["role"] for t in body] == ["user", "assistant"], body
    assert body[1]["content"] == "Tienes 1 proyecto activo."
    assert body[1]["tools_called"] == ["tenant_projects_status"]


@pytest.mark.asyncio
async def test_a_second_message_continues_the_same_conversation(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    """El historial se lee en la sesión corta de resolución. Si esa fase se
    rompiera, el segundo mensaje llegaría al modelo sin contexto — un fallo
    silencioso: la respuesta seguiría siendo 200."""
    seeded = await _seed(migrations_pg_dsn)
    model = _PoolWatchingModel(_two_round_script())
    _install_model(configured_app, model)
    token = await _mint_token(seeded["admin"], seeded["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        first = await client.post("/assistant/chat", json={"message": "primera"}, headers=headers)
        assert first.status_code == 200, first.text
        conversation_id = first.json()["conversation_id"]
        before = len(model.histories)
        second = await client.post(
            "/assistant/chat",
            json={"message": "segunda", "conversation_id": conversation_id},
            headers=headers,
        )
        assert second.status_code == 200, second.text
        assert second.json()["conversation_id"] == conversation_id
        turns = await client.get(
            f"/assistant/conversations/{conversation_id}/turns", headers=headers
        )

    # Los cuatro turnos están en el MISMO hilo. No se afirma su orden relativo:
    # los dos turnos de un mismo mensaje se insertan en el mismo flush y comparten
    # `created_at` al microsegundo, así que el desempate no es determinista — una
    # arista preexistente, ajena a esta tarea, que un assert por índice convertiría
    # en rojo intermitente.
    contents = sorted(t["content"] for t in turns.json())
    assert contents == sorted(
        ["primera", "segunda", "Tienes 1 proyecto activo.", "Tienes 1 proyecto activo."]
    ), contents
    # Y el segundo turno llegó al modelo CON el historial del primero: el mensaje
    # nuevo va SIEMPRE el último (lo añade el endpoint), y delante viaja el hilo.
    second_prompt = model.histories[before]
    assert second_prompt[-1] == {"role": "user", "content": "segunda"}, second_prompt
    assert "primera" in [m["content"] for m in second_prompt[:-1]], second_prompt


# ===========================================================================
# POST /assistant/chat/stream — el peor caso: la respuesta VIVE mientras el
# turno corre, así que la sesión del request se retenía todo el stream.
# ===========================================================================
@pytest.mark.asyncio
async def test_chat_stream_holds_no_pooled_connection_while_the_llm_thinks(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    model = _PoolWatchingModel(_two_round_script())
    _install_model(configured_app, model)
    token = await _mint_token(seeded["admin"], seeded["tenant"])

    frames: list[tuple[str, dict[str, Any]]] = []
    client = AsyncClient(transport=ASGITransport(app=configured_app), base_url="http://test")
    async with (
        client,
        client.stream(
            "POST",
            "/assistant/chat/stream",
            json={"message": "¿Cómo vamos?"},
            headers={"Authorization": f"Bearer {token}"},
        ) as resp,
    ):
        assert resp.status_code == 200
        event = ""
        async for line in resp.aiter_lines():
            if line.startswith("event: "):
                event = line.removeprefix("event: ").strip()
            elif line.startswith("data: "):
                frames.append((event, json.loads(line.removeprefix("data: "))))

    kinds = [k for k, _ in frames]
    assert "answer" in kinds, frames
    answer = next(data for kind, data in frames if kind == "answer")
    assert answer["answer"] == "Tienes 1 proyecto activo."
    assert len(model.checked_out) >= 2, model.checked_out
    assert model.checked_out == [0] * len(model.checked_out), (
        f"el stream retiene una conexión del pool durante el turno LLM: {model.checked_out}"
    )

    # Y el turno quedó persistido (el stream commitea en su propia sesión corta).
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        stored = await conn.fetchval(
            "SELECT count(*) FROM assistant_turns WHERE conversation_id = $1",
            UUID(answer["conversation_id"]),
        )
    finally:
        await conn.close()
    assert stored == 2, stored


# ===========================================================================
# La consecuencia: con UNA sola conexión en el pool, dos chats simultáneos
# tienen que salir los dos.
# ===========================================================================
@pytest.fixture()
def app_with_a_one_connection_pool(
    alembic_config: Any,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    """La misma app del arnés, con el pool de la aplicación reducido a UNA
    conexión y sin overflow. Es la lupa del hallazgo db-2: lo que en producción
    pasa con 15 chats a la vez, aquí pasa con dos.

    El ``pool_timeout`` baja a 3 s para que el caso rojo falle rápido en vez de
    quedarse esperando los 10 s del default."""
    import asyncio as _asyncio

    from alembic import command

    from tests.integration.conftest import _flush_redis, _grant_app_user_existing_tables

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    _asyncio.run(_grant_app_user_existing_tables())
    _asyncio.run(_flush_redis(test_redis_url))

    monkeypatch.setenv("API_SERVER_DATABASE_URL", app_database_url)
    monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")
    monkeypatch.setenv("API_SERVER_DB_POOL_SIZE", "1")
    monkeypatch.setenv("API_SERVER_DB_MAX_OVERFLOW", "0")
    monkeypatch.setenv("API_SERVER_DB_POOL_TIMEOUT", "3")

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
        app.dependency_overrides.clear()
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


class _RendezvousModel(_PoolWatchingModel):
    """Como el anterior, pero el PRIMER ``decide`` de cada request espera a que
    el otro request haya llegado también. Así los dos turnos LLM se solapan de
    verdad: es la única forma de que la contención por el pool se manifieste.

    La espera lleva timeout: si el otro request nunca llega —porque se quedó
    atascado pidiendo la única conexión— el test tiene que fallar con su
    aserción, no colgarse."""

    def __init__(self, turns: list[Any], parties: int = 2) -> None:
        super().__init__(turns)
        self._barrier = asyncio.Barrier(parties)
        self._arrived: set[int] = set()
        self.rendezvous_reached = 0

    async def decide(self, state: Any) -> Any:
        key = id(state.chat_history)
        if key not in self._arrived:
            self._arrived.add(key)
            try:
                await asyncio.wait_for(self._barrier.wait(), timeout=15)
                self.rendezvous_reached += 1
            except (TimeoutError, asyncio.BrokenBarrierError):
                pass
        return await super().decide(state)


@pytest.mark.asyncio
async def test_two_chats_share_a_single_pooled_connection(
    app_with_a_one_connection_pool: Any, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    model = _RendezvousModel(_two_round_script())
    _install_model(app_with_a_one_connection_pool, model)
    token = await _mint_token(seeded["admin"], seeded["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=app_with_a_one_connection_pool),
        base_url="http://test",
    ) as client:
        first, second = await asyncio.gather(
            client.post("/assistant/chat", json={"message": "uno"}, headers=headers),
            client.post("/assistant/chat", json={"message": "dos"}, headers=headers),
            return_exceptions=True,
        )

    for label, resp in (("primero", first), ("segundo", second)):
        assert not isinstance(resp, BaseException), f"el chat {label} reventó: {resp!r}"
        assert resp.status_code == 200, f"el chat {label} devolvió {resp.status_code}: {resp.text}"
    # Y se solaparon de verdad: los dos estuvieron dentro del turno a la vez.
    assert model.rendezvous_reached == 2, (
        "los dos chats no llegaron a solaparse, así que el test no midió la "
        f"contención por el pool (llegaron {model.rendezvous_reached})"
    )


# ===========================================================================
# «…ni embeds»: GET /knowledge-bases/{id}/search llama a Ollama en medio
# ===========================================================================
class _PoolWatchingEmbedder:
    """El embedder de la query, anotando el pool en el instante del embed.

    Es el mismo hallazgo con otra red: embeber una query es una llamada HTTP a
    Ollama, y dentro de la transacción del request convierte cualquier latencia
    del modelo en conexiones retenidas."""

    def __init__(self) -> None:
        from api_server.ingestion.embeddings import HashEmbedder

        self._inner = HashEmbedder()
        self.checked_out: list[int] = []

    @property
    def model_id(self) -> str:
        return self._inner.model_id

    @property
    def dim(self) -> int:
        return self._inner.dim

    async def embed(self, texts: Any) -> list[list[float]]:
        from api_server.db.session import get_engine

        self.checked_out.append(get_engine().pool.checkedout())
        return await self._inner.embed(texts)

    async def aclose(self) -> None:
        await self._inner.aclose()


async def _seed_kb_with_chunks(dsn: str, *, tenant_id: UUID) -> UUID:
    kb_id, document_id = uuid4(), uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO knowledge_bases (id, tenant_id, name, embedding_model_id)"
            " VALUES ($1, $2, 'KB NoTx', 'nomic-embed-text-v1.5')",
            kb_id,
            tenant_id,
        )
        await conn.execute(
            "INSERT INTO documents"
            " (id, tenant_id, kb_id, title, source_filename, source_mime_type,"
            "  source_storage_key, source_size_bytes, status)"
            " VALUES ($1, $2, $3, 'Doc', 'doc.pdf', 'application/pdf', $4, 10, 'indexed')",
            document_id,
            tenant_id,
            kb_id,
            f"kb/{tenant_id}/{kb_id}/{document_id}/doc.pdf",
        )
        await conn.execute(
            "INSERT INTO chunks (id, tenant_id, document_id, ordinal, content)"
            " VALUES ($1, $2, $3, 0, 'asyncpg necesita un pool de conexiones sano')",
            uuid4(),
            tenant_id,
            document_id,
        )
    finally:
        await conn.close()
    return kb_id


@pytest.mark.asyncio
async def test_kb_search_embeds_the_query_outside_any_transaction(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    kb_id = await _seed_kb_with_chunks(migrations_pg_dsn, tenant_id=seeded["tenant"])

    from api_server.routers.docs_viewer import get_query_embedder

    embedder = _PoolWatchingEmbedder()

    async def _override() -> Any:
        yield embedder

    configured_app.dependency_overrides[get_query_embedder] = _override
    token = await _mint_token(seeded["admin"], seeded["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(
            f"/knowledge-bases/{kb_id}/search",
            params={"q": "asyncpg"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200, resp.text
    # La guarda contra el verde vacío: el embed ocurrió (si degradase a BM25-only
    # no habría observación ninguna, y cero observaciones no es cero conexiones).
    assert embedder.checked_out == [0], embedder.checked_out
    # Y la búsqueda sigue devolviendo el chunk: los tres tramos siguen siendo un
    # search que funciona, no una refactorización que se llevó el resultado.
    assert [h["content"] for h in resp.json()] == ["asyncpg necesita un pool de conexiones sano"]
