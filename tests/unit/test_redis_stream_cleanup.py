"""Borrar en la BD tiene que limpiar también el stream de Redis.

Cierra la Feature 3 del plan `mejoras-2026-06-chat-coste-cortex`. El código de
limpieza ya estaba escrito —`delete_document_stream` y su gemelo de
conversación, y sus dos llamantes— pero **nada lo fijaba**: un refactor podía
quitar la llamada y ningún test se enteraba. Es el mismo patrón que persigue
toda la remediación: mecanismo entregado, cero red debajo.

Qué se rompe si falla: un stream huérfano hace que una reconexión posterior del
WebSocket **reproduzca eventos de algo que ya no existe** — progreso de ingesta
de un documento borrado, mensajes de un chat vaciado.

Y el contrato importante es el negativo: la limpieza es *best-effort*. Si Redis
está caído, el borrado del usuario **tiene que completarse igual**. Perder un
stream huérfano es un incordio; perder el borrado es perder una orden explícita.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from api_server.events import (
    _EXECUTION_STREAM_TTL_S,
    conversation_stream_key,
    delete_conversation_stream,
    delete_document_stream,
    document_stream_key,
    execution_stream_key,
    publish_execution_event,
)


class _Redis:
    """Redis de mentira que anota qué claves le mandaron borrar."""

    def __init__(self, *, boom: bool = False) -> None:
        self.deleted: list[str] = []
        self._boom = boom

    async def delete(self, key: str) -> int:
        if self._boom:
            raise ConnectionError("redis caído")
        self.deleted.append(key)
        return 1


# ---------------------------------------------------------------------------
# Se borra el stream correcto
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_deleting_a_document_drops_its_ingestion_stream() -> None:
    redis = _Redis()
    await delete_document_stream(redis, "doc-42")  # type: ignore[arg-type]
    assert redis.deleted == [document_stream_key("doc-42")]


@pytest.mark.asyncio
async def test_deleting_a_conversation_drops_its_chat_stream() -> None:
    redis = _Redis()
    await delete_conversation_stream(redis, "conv-7")  # type: ignore[arg-type]
    assert redis.deleted == [conversation_stream_key("conv-7")]


@pytest.mark.asyncio
async def test_the_two_streams_do_not_share_a_namespace() -> None:
    # Un documento y una conversación con el mismo id no pueden pisarse: borrar
    # uno se llevaría por delante el stream vivo del otro.
    assert document_stream_key("x") != conversation_stream_key("x")


# ---------------------------------------------------------------------------
# El contrato negativo: la limpieza NUNCA puede tumbar el borrado
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_dead_redis_does_not_break_the_document_delete() -> None:
    await delete_document_stream(_Redis(boom=True), "doc-42")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_dead_redis_does_not_break_the_conversation_delete() -> None:
    await delete_conversation_stream(_Redis(boom=True), "conv-7")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Que los llamantes sigan llamando
# ---------------------------------------------------------------------------
def _endpoint_body(path: Path, name: str) -> str:
    """El cuerpo de la función `name` dentro de `path`."""
    source = path.read_text(encoding="utf-8")
    start = re.search(rf"^async def {name}\(", source, re.MULTILINE)
    assert start, f"no encuentro `{name}` en {path.name} — ¿se renombró?"
    rest = source[start.end() :]
    end = re.search(r"^(async def |def |@)", rest, re.MULTILINE)
    return rest[: end.start()] if end else rest


_ROOT = Path(__file__).resolve().parents[2] / "apps" / "api-server" / "src" / "api_server"


def test_the_document_delete_endpoint_still_cleans_its_stream() -> None:
    # Guarda estática y no de comportamiento a propósito: lo que hay que impedir
    # es que la LLAMADA desaparezca en un refactor, y eso se ve en la fuente sin
    # levantar base de datos.
    body = _endpoint_body(_ROOT / "routers" / "knowledge_bases.py", "delete_document")
    assert "delete_document_stream(" in body


def test_the_conversation_delete_endpoint_still_cleans_its_stream() -> None:
    body = _endpoint_body(_ROOT / "routers" / "conversations.py", "delete_conversation")
    assert "delete_conversation_stream(" in body


def test_the_conversation_delete_hard_deletes_its_messages() -> None:
    # La conversación se queda soft-deleted como marca de auditoría, pero sus
    # mensajes se borran de verdad: si no, quedan filas huérfanas colgando de
    # una conversación que ya no se lista y que nadie volverá a mirar.
    body = _endpoint_body(_ROOT / "routers" / "conversations.py", "delete_conversation")
    assert re.search(
        r"delete\(Message\)\.where", body
    ), "el DELETE de conversación ya no hard-borra sus mensajes"


def test_clearing_a_chat_also_cleans_its_stream() -> None:
    # «Vaciar chat» borra los mensajes pero conserva la conversación; si el
    # stream sobrevive, al reconectar reaparecen los mensajes recién vaciados.
    body = _endpoint_body(_ROOT / "routers" / "conversations.py", "clear_messages")
    assert "delete_conversation_stream(" in body


def _all_stream_keys() -> list[str]:
    source = (_ROOT / "events.py").read_text(encoding="utf-8")
    return re.findall(r"^def (\w+_stream_key)\(", source, re.MULTILINE)


def test_every_stream_key_family_is_accounted_for() -> None:
    """Inventario de los streams que existen, para que uno nuevo no pase inadvertido.

    Este test no exige que todo stream tenga limpieza —los hay legítimamente
    perpetuos, como la telemetría del córtex— sino que **obliga a mirar**:
    añadir una familia nueva rompe aquí y quien la añada decide, a conciencia,
    si necesita borrado.
    """
    known = {
        "conversation_stream_key",  # limpiado al borrar/vaciar el chat
        "document_stream_key",  # limpiado al borrar el documento
        "execution_stream_key",  # TTL deslizante (no hay «borrar un run»)
        "cortex_telemetry_stream_key",  # una por OWNER: acotado, no crece con el uso
    }
    found = set(_all_stream_keys())
    assert (
        found <= known
    ), f"familias de stream nuevas sin decidir su limpieza: {sorted(found - known)}"
    # No-vacuo: si el descubrimiento deja de encontrar nada, el test dejaría de
    # vigilar sin avisar.
    assert len(found) >= 3, f"la búsqueda de claves de stream se rompió (vio {found})"


# ---------------------------------------------------------------------------
# El stream de un run caduca solo — no hay «borrar una ejecución»
# ---------------------------------------------------------------------------
class _Pipe:
    def __init__(self, sink: list[tuple[str, Any]]) -> None:
        self._sink = sink

    def xadd(self, key: str, *_a: Any, **_kw: Any) -> None:
        self._sink.append(("xadd", key))

    def expire(self, key: str, ttl: int) -> None:
        self._sink.append(("expire", (key, ttl)))

    async def execute(self) -> None:
        self._sink.append(("execute", None))


class _PipelineRedis:
    def __init__(self) -> None:
        self.ops: list[tuple[str, Any]] = []

    def pipeline(self) -> _Pipe:
        return _Pipe(self.ops)


@pytest.mark.asyncio
async def test_publishing_a_run_event_refreshes_the_streams_ttl() -> None:
    """`maxlen` acota lo que PESA cada stream, no cuántos hay.

    Sin caducidad quedaba una clave `exec:{id}` en Redis por cada run y para
    siempre: crecimiento monótono con el uso. No hay una operación «borrar
    ejecución» de la que colgar la limpieza —son registros inmutables—, así que
    la vía es un TTL que se renueva en cada evento.
    """
    redis = _PipelineRedis()
    await publish_execution_event(
        redis,  # type: ignore[arg-type]
        "run-1",
        event_type="node",
        payload={"x": 1},
    )
    key = execution_stream_key("run-1")
    assert ("expire", (key, _EXECUTION_STREAM_TTL_S)) in redis.ops
    # En la MISMA ida y vuelta que el xadd: esto está en el camino caliente de
    # cada evento de cada run.
    assert [op for op, _ in redis.ops] == ["xadd", "expire", "execute"]


@pytest.mark.asyncio
async def test_the_ttl_outlives_a_long_run() -> None:
    # Deslizante y generoso: el techo de un run es de horas, y cortarle el
    # directo por debajo a una ejecución viva sería peor que la fuga.
    assert _EXECUTION_STREAM_TTL_S >= 24 * 3600


@pytest.mark.asyncio
async def test_a_dead_redis_does_not_break_a_run() -> None:
    class _Boom:
        def pipeline(self) -> Any:
            raise ConnectionError("redis caído")

    # Publicar telemetría es best-effort: que Redis no esté no puede tumbar un
    # run que está trabajando.
    await publish_execution_event(
        _Boom(),  # type: ignore[arg-type]
        "run-1",
        event_type="node",
        payload={},
    )
