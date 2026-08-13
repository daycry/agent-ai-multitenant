"""El bug «Event loop is closed» del runtime (prod-07 task_prod07_02, llm-3).

El modo de fallo, exacto
------------------------
El lazo del agente es SÍNCRONO y los providers son async, así que el runtime
puentea con ``asyncio.run`` **por llamada** (``providers._run``). Cada
``asyncio.run`` crea un event loop nuevo y lo CIERRA al salir. Si un provider
guarda un ``httpx.AsyncClient`` en su constructor, ese cliente queda atado al
loop de la PRIMERA llamada; en la segunda —loop distinto, el anterior ya
cerrado— httpx intenta reusar la conexión del pool y estalla con
``RuntimeError: Event loop is closed``. Un run de 30 iteraciones moría en la
iteración 2.

Por qué este test no existía y los tres que se citaban no valían
---------------------------------------------------------------
Los tests de "cliente fresco por llamada" de los tres providers
(``test_ollama_provider.test_owned_client_is_fresh_per_call`` y sus gemelos)
llaman al helper privado ``_acquire()`` DOS VECES DENTRO DEL MISMO loop de
pytest-asyncio. Ahí no hay dos loops, así que el fallo real —cruzar el puente
``asyncio.run`` dos veces— no se ejercita: el test pasaría igual con el patrón
roto. Este fichero cruza el puente de verdad, por el camino del runtime.

Lo que se fija:

  1. dos ``decide()`` consecutivos sobre el MISMO cliente del runtime funcionan,
     cada uno en su propio ``asyncio.run``, sin ``Event loop is closed``;
  2. el httpx real (no MockTransport precargado) sirve las dos llamadas contra
     un servidor keep-alive de verdad;
  3. ``close()`` del cliente invoca ``aclose()`` del provider al cerrar el run —
     y también cruza el puente sin romperse;
  4. la guarda de regresión: un provider que SÍ cachea su cliente entre loops
     revienta, de modo que este test no puede pasar vacíamente.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import pytest
from agent_runtime.providers import OllamaModelClient, _ProviderModelClient, _run
from shared_llm import Message, OllamaProvider
from shared_llm.types import CompletionResponse, Usage

_BODY = {
    "model": "llama3.1",
    "choices": [{"message": {"role": "assistant", "content": "sigo"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 3, "completion_tokens": 4},
}


class _Handler(BaseHTTPRequestHandler):
    """`/v1/chat/completions` mínimo, con keep-alive REAL (HTTP/1.1)."""

    protocol_version = "HTTP/1.1"
    # Sin esto, en Windows el ACK diferido + Nagle mete ~2s por petición: la
    # respuesta sale en dos segmentos TCP y el cliente espera el segundo.
    disable_nagle_algorithm = True

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        payload = json.dumps(_BODY).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args: Any) -> None:
        return  # sin ruido en la salida de pytest


@pytest.fixture
def keepalive_server() -> Any:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _state(title: str) -> dict[str, Any]:
    return {"task": {"title": title}, "context": []}


def test_two_decides_reuse_no_closed_loop(keepalive_server: str) -> None:
    """El test que el plan exige: dos ``decide()`` seguidos, HTTP real."""
    client = OllamaModelClient(model="llama3.1", base_url=keepalive_server)
    first = client.decide(_state("paso 1"))
    second = client.decide(_state("paso 2"))  # <- aquí moría el run
    assert first is not None
    assert second is not None


def test_close_after_two_calls_also_crosses_the_bridge(keepalive_server: str) -> None:
    """``close()`` corre en su PROPIO ``asyncio.run``: el tercer loop del run."""
    provider = OllamaProvider(base_url=keepalive_server, default_model="llama3.1")
    client = _ProviderModelClient(provider=provider, model="llama3.1")
    client.decide(_state("paso 1"))
    client.decide(_state("paso 2"))
    client.close()  # no debe lanzar
    # Idempotente: el teardown del runtime puede llamarlo dos veces.
    client.close()


def test_provider_aclose_is_invoked_on_close() -> None:
    """``close()`` del adaptador tiene que LLAMAR a ``aclose()`` del provider.

    Sin esto, el `finally` de ``__main__`` cerraría el run dejando vivos el
    cliente httpx (y, con claude_sdk, un CLI + su loop de fondo)."""
    closed: list[int] = []

    class _Provider:
        name = "fake"

        async def complete(
            self, _messages: Sequence[Message], **_kwargs: Any
        ) -> CompletionResponse:
            return CompletionResponse(
                content="x", model="m", provider="fake", usage=Usage(1, 1, 0.0)
            )

        async def aclose(self) -> None:
            closed.append(1)

    client = _ProviderModelClient(provider=_Provider(), model="m")
    client.close()
    assert closed == [1]


def test_a_client_cached_across_loops_would_break(keepalive_server: str) -> None:
    """Guarda de regresión (§4 de verificar-antes-de-implementar): si el patrón
    roto NO fallara, los tests de arriba pasarían vacíamente.

    Reproduce el patrón que el plan mandaba eliminar —un ``AsyncClient`` creado
    UNA vez y reusado desde varios ``asyncio.run``— y comprueba que rompe de
    verdad. El día que httpx deje de romper aquí, este test avisa de que las
    aserciones de arriba dejaron de significar algo."""
    provider = OllamaProvider(
        base_url=keepalive_server,
        default_model="llama3.1",
        # Inyectado ⇒ el provider NO crea uno por llamada: exactamente el
        # patrón roto que tenía el runtime.
        http_client=httpx.AsyncClient(timeout=10.0),
    )
    messages = [Message(role="user", content="hola")]

    _run(provider.complete(messages))  # 1.er loop: llena el pool keep-alive
    with pytest.raises((RuntimeError, httpx.HTTPError, OSError)) as info:
        _run(provider.complete(messages))  # 2.º loop: el pool es del loop muerto
    assert "closed" in str(info.value).lower() or isinstance(info.value, httpx.HTTPError)


def test_owned_provider_creates_a_client_per_loop(keepalive_server: str) -> None:
    """Y el contraste: SIN cliente inyectado, el mismo provider aguanta N loops."""
    provider = OllamaProvider(base_url=keepalive_server, default_model="llama3.1")
    messages = [Message(role="user", content="hola")]
    for _ in range(3):
        resp = _run(provider.complete(messages))
        assert resp.content == "sigo"
    _run(provider.aclose())


def test_asyncio_run_really_closes_its_loop() -> None:
    """La premisa del test entero, comprobada en vez de asumida: cada
    ``asyncio.run`` usa un loop DISTINTO y lo deja cerrado."""
    loops: list[asyncio.AbstractEventLoop] = []

    async def _grab() -> None:
        loops.append(asyncio.get_running_loop())

    _run(_grab())
    _run(_grab())
    assert loops[0] is not loops[1]
    assert loops[0].is_closed() and loops[1].is_closed()
