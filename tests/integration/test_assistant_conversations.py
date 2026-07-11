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
