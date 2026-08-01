"""prod-13 · task_prod13_02 (lo que sí cabe en el api-server) — plazo de las
sondas remotas de backup.

`to_thread` ya estaba: las llamadas de red de los adaptadores (boto3, paramiko,
rclone) salen del bucle de eventos, así que un destino inalcanzable dejó de
congelar TODAS las requests y WebSockets (hallazgo api-3). Pero `to_thread` no
pone plazo a nada, y ahí quedaban dos agujeros:

  1. **La petición no termina.** `paramiko` sin `timeout` explícito hereda el del
     SO: contra una IP que se traga los paquetes (firewall que DROPea en vez de
     RSTear) son minutos. El operador ve un spinner eterno y vuelve a pulsar.
  2. **El executor es finito.** `asyncio.to_thread` usa el executor por defecto,
     con `min(32, cpu+4)` hilos. Suficientes sondas colgadas lo agotan, y a
     partir de ahí `to_thread` deja de ser una salida: se hace cola. O sea, el
     bloqueo que se quería evitar vuelve por la puerta de atrás.

Lo que este plazo SÍ arregla y lo que NO, dicho en voz alta: la petición vuelve
acotada, pero **el hilo colgado sigue colgado** — Python no puede matar un hilo.
El arreglo completo es un timeout de socket dentro de los adaptadores
(`workers/backup_destinations.py`), que es de otro carril; este plazo es el
cinturón que se puede poner desde aquí.
"""

from __future__ import annotations

import asyncio
import time

import pytest

pytestmark = pytest.mark.unit


def test_the_deadline_is_short_enough_to_be_useful() -> None:
    """Una sonda de alcanzabilidad que tarda más de unos segundos ya ha dicho lo
    que tenía que decir. El valor concreto importa: un plazo de 10 minutos sería
    tener plazo sobre el papel y no tenerlo en la práctica."""
    from api_server.routers.backup import REMOTE_PROBE_TIMEOUT_S

    assert 0 < REMOTE_PROBE_TIMEOUT_S <= 30


@pytest.mark.asyncio
async def test_a_hung_probe_returns_a_bounded_failure() -> None:
    """El caso que motiva la tarea: el adaptador no vuelve nunca."""
    from api_server.routers.backup import run_remote_probe

    async def _never() -> None:
        await asyncio.sleep(3600)

    started = time.monotonic()
    result = await run_remote_probe(_never(), timeout_s=0.05, on_timeout="probe-timeout")
    elapsed = time.monotonic() - started

    assert result == "probe-timeout"
    assert elapsed < 1.0, f"la sonda no respetó el plazo ({elapsed:.2f}s)"


@pytest.mark.asyncio
async def test_a_fast_probe_is_untouched() -> None:
    """El plazo no puede cambiar el resultado del camino feliz."""
    from api_server.routers.backup import run_remote_probe

    async def _quick() -> str:
        return "ok"

    assert await run_remote_probe(_quick(), timeout_s=5.0, on_timeout="nope") == "ok"


@pytest.mark.asyncio
async def test_the_probe_error_is_not_swallowed() -> None:
    """Un fallo real del adaptador tiene que seguir subiendo: convertirlo en el
    valor de timeout diría «se colgó» donde en realidad hubo un error con causa,
    y el operador perdería el mensaje que necesita."""
    from api_server.routers.backup import run_remote_probe

    async def _boom() -> str:
        raise RuntimeError("credenciales inválidas")

    with pytest.raises(RuntimeError, match="credenciales"):
        await run_remote_probe(_boom(), timeout_s=5.0, on_timeout="nope")
