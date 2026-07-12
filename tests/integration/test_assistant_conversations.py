"""A1 (investigación asistente 2026-07-11): hilos persistentes del asistente.

El asistente era STATELESS: cada POST /assistant/chat enviaba solo el mensaje
actual, sin conversation_id ni persistencia — el criterio de aceptación
human_10_04 («mantiene contexto entre mensajes») estaba incumplido. Ahora:

  * el chat crea/reutiliza un hilo (assistant_conversations, RLS) y devuelve
    su conversation_id;
  * el HISTORIAL del hilo entra al prompt del modelo (el follow-up «¿y el
    segundo?» por fin tiene contexto);
  * los turnos persisten (assistant_turns) y se listan;
  * un hilo de OTRO usuario es 404 (pertenencia por columna, no solo RLS).
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from tests.integration.test_personal_assistant import _mint_token, _seed

pytestmark = pytest.mark.integration


class _CapturingModel:
    """Modelo que responde fijo y CAPTURA el chat_history que recibe."""

    def __init__(self) -> None:
        self.histories: list[list[dict[str, Any]]] = []

    async def decide(self, state: Any) -> Any:
        from api_server.assistant.graph import ModelTurn

        self.histories.append(list(state.chat_history))
        return ModelTurn(content=f"respuesta-{len(self.histories)}")


def _install_capturing_model(app) -> _CapturingModel:
    from api_server.routers.assistant import get_assistant_model

    model = _CapturingModel()
    app.dependency_overrides[get_assistant_model] = lambda: model
    return model


async def _chat(client: AsyncClient, headers: dict, message: str, conversation_id=None):
    body: dict[str, Any] = {"message": message}
    if conversation_id:
        body["conversation_id"] = str(conversation_id)
    resp = await client.post("/assistant/chat", json=body, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_chat_persists_thread_and_feeds_history(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    model = _install_capturing_model(configured_app)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        first = await _chat(client, headers, "¿Qué proyectos tengo?")
        conv_id = first["conversation_id"]
        assert conv_id

        second = await _chat(client, headers, "¿Y el segundo?", conversation_id=conv_id)
        assert second["conversation_id"] == conv_id

        # El SEGUNDO turno del modelo recibió el historial completo del hilo.
        assert len(model.histories) == 2
        history = model.histories[1]
        contents = [h["content"] for h in history]
        assert "¿Qué proyectos tengo?" in contents
        assert "respuesta-1" in contents
        assert contents[-1] == "¿Y el segundo?"

        # Los hilos y turnos persisten y se listan.
        convs = (await client.get("/assistant/conversations", headers=headers)).json()
        assert any(c["id"] == conv_id for c in convs)
        turns = (
            await client.get(f"/assistant/conversations/{conv_id}/turns", headers=headers)
        ).json()
        roles = [t["role"] for t in turns]
        assert roles == ["user", "assistant", "user", "assistant"]


@pytest.mark.asyncio
async def test_foreign_users_conversation_is_404(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    _install_capturing_model(configured_app)
    token_a = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers_a = {"Authorization": f"Bearer {token_a}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        first = await _chat(client, headers_a, "hola")
        conv_id = first["conversation_id"]

        # El admin del tenant B no puede ni leer ni continuar el hilo de A.
        token_b = await _mint_token(seeded["admin_b"], seeded["tenant_b"])
        headers_b = {"Authorization": f"Bearer {token_b}"}
        read = await client.get(f"/assistant/conversations/{conv_id}/turns", headers=headers_b)
        assert read.status_code == 404
        cont = await client.post(
            "/assistant/chat",
            json={"message": "sigo yo", "conversation_id": conv_id},
            headers=headers_b,
        )
        assert cont.status_code == 404


# ===========================================================================
# A4: tool search_knowledge — el asistente por fin puede consultar las KBs del
# tenant («¿qué dice nuestra documentación sobre X?»). Cross-proyecto bajo la
# sesión RLS; BM25 con es_unaccent (P0-4).
# ===========================================================================
@pytest.mark.asyncio
async def test_search_knowledge_reads_tenant_kbs(configured_app, migrations_pg_dsn: str) -> None:
    from api_server.assistant.tools import AssistantToolContext, run_assistant_tool
    from sqlalchemy import text as sa_text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tests.integration._rag_helpers import seed_rag_corpus
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await seed_rag_corpus(migrations_pg_dsn)

    import os

    app_url = os.environ["API_SERVER_DATABASE_URL"]
    engine = create_async_engine(app_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session:
            await session.execute(
                sa_text("SELECT set_config('app.tenant_id', :tid, false)"),
                {"tid": str(seeded["tenant_id"])},
            )
            ctx = AssistantToolContext(
                session=session,
                tenant_id=seeded["tenant_id"],
                user_id=seeded["tenant_id"],  # cualquier UUID: la tool no lo usa
            )
            result = await run_assistant_tool("search_knowledge", ctx, {"query": "asyncpg"})
    finally:
        await engine.dispose()

    assert result["hits"], result
    assert any("asyncpg" in h["snippet"] for h in result["hits"])


# ===========================================================================
# A2 fase 1: POST /assistant/chat/stream — progreso vivo por SSE. El usuario
# miraba «Pensando…» hasta la respuesta completa; ahora ve frames progress por
# paso del grafo y el frame answer final (que además persiste el hilo).
# Token-a-token queda para ADR 0073 F2.
# ===========================================================================
@pytest.mark.asyncio
async def test_chat_stream_emits_progress_and_final_answer(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    _install_capturing_model(configured_app)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        async with client.stream(
            "POST", "/assistant/chat/stream", json={"message": "hola"}, headers=headers
        ) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            body = ""
            async for chunk in resp.aiter_text():
                body += chunk

        assert "event: progress" in body
        assert "event: answer" in body
        assert "respuesta-1" in body

        # El frame answer trae el conversation_id y el hilo quedó persistido.
        import json as _json

        answer_line = next(
            line for line in body.splitlines() if line.startswith("data:") and "answer" in line
        )
        payload = _json.loads(answer_line.removeprefix("data:").strip())
        conv_id = payload["conversation_id"]
        turns = (
            await client.get(f"/assistant/conversations/{conv_id}/turns", headers=headers)
        ).json()
        assert [t["role"] for t in turns] == ["user", "assistant"]
