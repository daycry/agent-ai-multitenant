"""Backpressure y deadline de envío del pump de WebSockets (`task_audit14_07`).

Hallazgo **AUD14-05** de la auditoría integral del 2026-07-14: `_pump` hacía
``await ws.send_json(event)`` **sin deadline**. Un navegador que deja de drenar
—pestaña en segundo plano con la CPU saturada, una red que se cae sin cerrar el
TCP, un móvil que se duerme— llena la ventana TCP y ese `await` no vuelve NUNCA.
Consecuencias, en orden de gravedad:

1. la corrutina del pump se queda colgada indefinidamente, con su tarea de
   `ws.receive()` y su `xread` vivos detrás;
2. el `xread` se re-arma contra Redis en cada iteración, así que el socket
   lento retiene además una conexión del pool;
3. como el bucle no vuelve al principio, **deja de re-validar la credencial**:
   la garantía de `task_prod09_13` (logout cierra los sockets abiertos) se cae
   justo para el cliente que peor se comporta.

Y la otra mitad del hallazgo, la que no se ve: `_pump` cancelaba `reader` y
`xread` con `.cancel()` **sin esperarlos**. `cancel()` sólo *pide* la
cancelación; hasta que alguien hace `await`, la corrutina sigue viva con su
frame y, en el caso del `xread`, con el cierre de conexión de redis-py a medias.
Los dobles de este fichero modelan eso a propósito (`_TEARDOWN_SECONDS`): un
`XREAD` real no muere en el instante del `cancel()`, y sin modelarlo el test no
distinguiría `cancel()` de `cancel()` + `await` — que es justo la diferencia que
la casilla pide.

Los tests conducen el `_pump` **de producción** con dobles en sus dos únicos
bordes de E/S (el WebSocket y Redis), igual que
`tests/integration/test_ws_session_revalidation.py`. Aquí son unitarios porque
ninguno de los dos bordes necesita el stack.

Los dos sentidos se afirman: un cliente que no drena **debe** cerrarse, y uno
normal **no** debe cerrarse (sin ese contrapeso, un pump que cerrara siempre
pasaría el primer test y rompería el producto).
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest
from api_server.auth.deps import AuthPrincipal
from api_server.routers import ws as ws_mod

pytestmark = pytest.mark.unit

#: RFC 6455 / registro IANA de códigos de cierre: 1013 «Try Again Later». Es el
#: que corresponde a un consumidor lento: ni es una violación de política (1008)
#: ni un fallo del servidor (1011); el cliente puede volver a conectar.
_CLOSE_SLOW_CONSUMER = 1013

#: Lo que tarda un borde de E/S real en morirse tras un `cancel()`. Con 0 el test
#: no podría distinguir `cancel()` de `cancel()` + `await`.
_TEARDOWN_SECONDS = 0.05


class _FakeWebSocket:
    """WebSocket doble. ``send_hangs`` simula el cliente que no drena.

    ``receive()`` no vuelve hasta que el cliente se desconecta, que es
    exactamente lo que hace un socket vivo, y al cancelarlo tarda un poco en
    morir (ver ``_TEARDOWN_SECONDS``).
    """

    def __init__(self, *, send_hangs: bool = False) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed_with: tuple[int, str | None] | None = None
        self._disconnected = asyncio.Event()
        self._send_hangs = send_hangs

    async def receive(self) -> dict[str, Any]:
        try:
            await self._disconnected.wait()
        except asyncio.CancelledError:
            await asyncio.sleep(_TEARDOWN_SECONDS)
            raise
        return {"type": "websocket.disconnect"}

    async def send_json(self, payload: dict[str, Any]) -> None:
        if self._send_hangs:
            await asyncio.sleep(3600)  # ventana TCP llena: el envío no vuelve
        self.sent.append(payload)

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed_with = (code, reason)
        self._disconnected.set()  # el cierre desbloquea al lector

    def disconnect(self) -> None:
        """El cliente cierra la pestaña."""
        self._disconnected.set()


class _FakeRedis:
    """Stream que entrega ``entries`` una vez y luego bloquea como un XREAD real.

    Al cancelarlo NO muere en el acto: modela el cierre de conexión de redis-py,
    que es lo que hace observable la diferencia entre cancelar y esperar.
    """

    def __init__(self, entries: list[tuple[str, dict[str, str]]] | None = None) -> None:
        self._entries = list(entries or [])
        self.xread_calls = 0

    async def xread(
        self, streams: dict[str, str], count: int = 0, block: int = 0
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        self.xread_calls += 1
        if self._entries:
            pending, self._entries = self._entries, []
            return [(next(iter(streams)), pending)]
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await asyncio.sleep(_TEARDOWN_SECONDS)
            raise
        return []  # pragma: no cover - inalcanzable

    async def time(self) -> tuple[int, int]:
        return (1_700_000_000, 0)


class _Sessions:
    """Doble del SessionStore. Con la re-validación desactivada no se consulta."""

    async def get(self, _sid: object) -> dict[str, Any] | None:  # pragma: no cover
        return {"user_id": str(uuid4())}


def _principal() -> AuthPrincipal:
    return AuthPrincipal(
        user_id=uuid4(), session_id=uuid4(), tenant_id=uuid4(), is_system_admin=False
    )


def _settings(
    monkeypatch: pytest.MonkeyPatch, *, send_timeout: float, revalidate: float = 0.0
) -> None:
    """Apunta el pump a un deadline de envío diminuto.

    Se parchea el ACCESOR de settings, no una constante del módulo: así el test
    ejercita el mismo asa configurable que el operador (un 10 s a fuego sería
    intestable, que es justo por lo que es un setting).
    """

    class _S:
        ws_session_revalidate_seconds = revalidate
        ws_send_timeout_seconds = send_timeout

    monkeypatch.setattr(ws_mod, "get_settings", _S)


def _pump_kwargs(redis: _FakeRedis) -> dict[str, Any]:
    return {
        "redis": redis,
        "stream": "events:test",
        "project_filter": None,
        "sessions": _Sessions(),
        "principal": _principal(),
        "token": None,
    }


async def _run_pump(ws: _FakeWebSocket, redis: _FakeRedis) -> None:
    """Corre el pump con un tope duro: un cuelgue debe fallar, no parar la suite."""
    await asyncio.wait_for(ws_mod._pump(ws, **_pump_kwargs(redis)), timeout=5.0)  # type: ignore[arg-type]


async def _until(predicate: Any, *, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("la condición no se cumplió a tiempo")
        await asyncio.sleep(0.005)


def _leaked(before: set[asyncio.Task[Any]], *ours: asyncio.Task[Any]) -> list[str]:
    """Tareas SIN TERMINAR nacidas durante el test.

    ``asyncio.all_tasks()`` sólo devuelve tareas vivas, así que todo lo que
    aparezca aquí es una corrutina que el pump dejó colgando.
    """
    mine = {asyncio.current_task(), *ours}
    return [repr(t) for t in asyncio.all_tasks() if t not in before and t not in mine]


# ---------------------------------------------------------------------------
# El hallazgo: un cliente que no drena bloqueaba el pump para siempre.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_un_cliente_lento_se_cierra_en_vez_de_colgar_el_pump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _settings(monkeypatch, send_timeout=0.05)
    ws = _FakeWebSocket(send_hangs=True)
    redis = _FakeRedis(entries=[("1-0", {"event": "task.moved", "payload": "{}"})])

    await _run_pump(ws, redis)

    assert ws.closed_with is not None, (
        "el pump siguió esperando a un cliente que no drena: sin deadline de "
        "envío, ese await no vuelve nunca"
    )
    code, reason = ws.closed_with
    assert code == _CLOSE_SLOW_CONSUMER, (
        f"un consumidor lento se cierra con {_CLOSE_SLOW_CONSUMER} (Try Again Later), no con {code}"
    )
    assert reason is not None and "slow" in reason.lower(), reason


@pytest.mark.asyncio
async def test_un_cliente_normal_recibe_el_evento_y_no_se_cierra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El contrapeso: sin esto, «cerrar siempre» pasaría el test de arriba."""
    _settings(monkeypatch, send_timeout=5.0)
    ws = _FakeWebSocket()
    event = ("1-0", {"event": "task.moved", "payload": '{"a": 1}'})
    redis = _FakeRedis(entries=[event])

    pump = asyncio.ensure_future(ws_mod._pump(ws, **_pump_kwargs(redis)))  # type: ignore[arg-type]
    await _until(lambda: ws.sent)
    ws.disconnect()
    await asyncio.wait_for(pump, timeout=5.0)

    assert ws.closed_with is None, f"un cliente sano no se cierra: {ws.closed_with}"
    assert ws.sent == [{"event": "task.moved", "payload": {"a": 1}, "id": "1-0"}]


# ---------------------------------------------------------------------------
# La otra mitad: `cancel()` no es `cancel()` + `await`.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_al_desconectar_el_cliente_no_queda_ninguna_corrutina_viva(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El camino más frecuente: el usuario cierra la pestaña con el XREAD en vuelo."""
    _settings(monkeypatch, send_timeout=5.0)
    ws, redis = _FakeWebSocket(), _FakeRedis()
    before = set(asyncio.all_tasks())

    pump = asyncio.ensure_future(ws_mod._pump(ws, **_pump_kwargs(redis)))  # type: ignore[arg-type]
    await _until(lambda: redis.xread_calls >= 1)
    ws.disconnect()
    await asyncio.wait_for(pump, timeout=5.0)

    leaked = _leaked(before, pump)
    assert not leaked, (
        "el pump volvió dejando corrutinas vivas detrás (el `cancel()` sin "
        f"`await` no espera a que mueran): {leaked}"
    )


@pytest.mark.asyncio
async def test_al_cerrar_un_cliente_lento_tampoco_queda_ninguna_corrutina_viva(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El camino nuevo: el pump cierra por deadline y desmonta lo suyo."""
    _settings(monkeypatch, send_timeout=0.05)
    ws = _FakeWebSocket(send_hangs=True)
    redis = _FakeRedis(entries=[("1-0", {"event": "task.moved"})])
    before = set(asyncio.all_tasks())

    pump = asyncio.ensure_future(ws_mod._pump(ws, **_pump_kwargs(redis)))  # type: ignore[arg-type]
    await asyncio.wait_for(pump, timeout=5.0)

    assert ws.closed_with is not None and ws.closed_with[0] == _CLOSE_SLOW_CONSUMER
    leaked = _leaked(before, pump)
    assert not leaked, f"el cierre por deadline dejó corrutinas vivas: {leaked}"


# ---------------------------------------------------------------------------
# El asa: el deadline es configurable por el operador, no una constante.
# ---------------------------------------------------------------------------
def test_el_deadline_de_envio_es_un_setting_con_default_razonable() -> None:
    from api_server.config import Settings

    field = Settings.model_fields.get("ws_send_timeout_seconds")
    assert field is not None, (
        "falta el setting `ws_send_timeout_seconds`: el deadline tiene que poder "
        "ajustarse por despliegue (API_SERVER_WS_SEND_TIMEOUT_SECONDS)"
    )
    assert isinstance(field.default, int | float) and field.default > 0, (
        f"el default del deadline de envío debe ser positivo: {field.default!r}"
    )
