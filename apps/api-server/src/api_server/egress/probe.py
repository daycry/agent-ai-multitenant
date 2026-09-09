"""Preguntarle al proxy si un host está permitido (`task_mk_02`, ADR 0165 D7.3).

Ni el ajuste `egress.mcp_allowed_hosts` ni el `filter.txt` pueden responder
«¿está permitido?»: el ajuste es la INTENCIÓN de un System Admin y el fichero
que manda es el horneado en la imagen del proxy en marcha, que el api-server no
ve. La única afirmación verdadera por construcción es abrir un CONNECT **a
través del proxy** y leer qué contesta. Eso es este módulo.

Dos límites que hay que saber antes de fiarse, y que el runbook repite:

- **Sondear no enumera.** Una allowlist de regex no se recorre desde fuera: se
  puede afirmar «todo lo que el ajuste pide está permitido», nunca «el proxy no
  permite nada más». Por eso el resultado es por host, no un booleano global.
- **Tiene coste de seguridad.** Convierte al api-server en un cliente que abre
  conexiones a destinos externos elegidos por el operador. Los dos frenos son de
  diseño: sale POR el proxy (no alcanza más que lo que el filtro ya permite) y no
  se dispara solo — sólo bajo petición explícita de un System Admin, nunca en un
  barrido periódico.

La lectura de cada desenlace sale de la tabla MEDIDA del runbook §4.2, no de la
documentación de tinyproxy.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Literal

import httpx

from api_server.egress.mcp_allowlist import InvalidMcpHostError, normalise_host

__all__ = [
    "DEFAULT_CONCURRENCY",
    "DEFAULT_TIMEOUT_S",
    "ProbeOpener",
    "ProbeResult",
    "Verdict",
    "classify_probe_outcome",
    "probe_hosts",
]

Verdict = Literal["permitido", "bloqueado", "error"]

#: Un CONNECT por host es barato, pero el proxy es el mismo que usan los sandboxes
#: y hasta 100 hosts (tope del ajuste) no deben salir de golpe.
DEFAULT_CONCURRENCY = 8
#: Suficiente para un túnel TLS; un origen que no contesta en esto ya nos ha
#: dicho lo que queríamos saber (que el proxy dejó pasar).
DEFAULT_TIMEOUT_S = 10.0

#: `(host, proxy_url, timeout_s) -> respuesta | excepción`. Inyectable para que el
#: sondeo se pruebe sin red; el real es :func:`_open_through_proxy`.
ProbeOpener = Callable[[str, str, float], Awaitable[httpx.Response | BaseException]]


@dataclass(frozen=True)
class ProbeResult:
    host: str
    verdict: Verdict
    detail: str


def classify_probe_outcome(host: str, outcome: httpx.Response | BaseException) -> ProbeResult:
    """Qué significa lo que devolvió el intento, según la tabla del runbook §4.2.

    - Una respuesta HTTP del ORIGEN, sea cual sea su status: el proxy abrió el
      túnel. Lo que pase después ya no es asunto suyo.
    - `ProxyError` con `Filtered`: el destino no está en la allowlist aplicada.
    - Otro `ProxyError` (`Access denied`): el proxy rechazó al CLIENTE, no al
      destino — es la ACL `Allow` (D4), otra avería.
    - No poder conectar con el proxy: error, y no dice nada de la allowlist.
    - Timeout de LECTURA: el túnel ya estaba abierto (la conexión se estableció),
      el origen no contestó. El proxy dejó pasar.
    """
    if isinstance(outcome, httpx.Response):
        return ProbeResult(
            host, "permitido", f"el proxy abrió el túnel; el origen respondió {outcome.status_code}"
        )
    if isinstance(outcome, httpx.ProxyError):
        if "filtered" in str(outcome).lower():
            return ProbeResult(
                host,
                "bloqueado",
                f"el proxy rechazó el CONNECT ({outcome}): el host no está en el filter.txt "
                "aplicado. Si acabas de añadirlo al ajuste, falta reconstruir y recrear el proxy",
            )
        return ProbeResult(
            host,
            "error",
            f"el proxy rechazó al api-server como cliente ({outcome}): no es la allowlist de "
            "hosts, es la ACL `Allow` de tinyproxy (runbook §5)",
        )
    if isinstance(outcome, httpx.ConnectError | httpx.ConnectTimeout):
        return ProbeResult(
            host,
            "error",
            f"no se pudo conectar con el egress-proxy ({outcome}); el veredicto sobre este host "
            "no se puede emitir",
        )
    if isinstance(outcome, httpx.TimeoutException):
        return ProbeResult(
            host,
            "permitido",
            "el proxy abrió el túnel y el origen no contestó a tiempo: el fallo está más allá "
            "del proxy",
        )
    return ProbeResult(host, "error", f"{type(outcome).__name__}: {outcome}")


async def _open_through_proxy(
    host: str, proxy_url: str, timeout_s: float
) -> httpx.Response | BaseException:
    """El intento real: un HEAD a `https://{host}/` con el proxy montado.

    Devuelve la excepción en vez de levantarla para que el clasificador reciba
    los dos desenlaces por el mismo camino. `follow_redirects=False`: no queremos
    seguir a un `Location` que apunte a otro host — el veredicto es de ESTE.
    """
    try:
        async with httpx.AsyncClient(
            proxy=proxy_url, timeout=timeout_s, follow_redirects=False
        ) as client:
            return await client.head(f"https://{host}/")
    except Exception as exc:
        return exc


async def probe_hosts(
    hosts: Iterable[str],
    *,
    proxy_url: str | None,
    opener: ProbeOpener = _open_through_proxy,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> list[ProbeResult]:
    """Sondea cada host POR el proxy y devuelve un veredicto por host, en el orden
    de entrada.

    Sin ``proxy_url`` no se sondea nada: sondear directo diría «permitido» de
    todo, que es el falso verde en su forma más pura. Un host que no pasa el
    validador del ajuste tampoco se sondea: sale como error con el motivo.
    """
    entradas = list(hosts)
    if not proxy_url or not proxy_url.strip():
        return [
            ProbeResult(
                h,
                "error",
                "el api-server no tiene egress-proxy configurado (API_SERVER_EGRESS_PROXY_URL): "
                "sin proxy no hay a quién preguntar",
            )
            for h in entradas
        ]

    semaforo = asyncio.Semaphore(max(1, concurrency))

    async def _uno(raw: str) -> ProbeResult:
        try:
            host = normalise_host(raw)
        except InvalidMcpHostError as exc:
            return ProbeResult(raw, "error", f"no se sondea: {exc.reason}")
        async with semaforo:
            try:
                outcome = await opener(host, proxy_url, timeout_s)
            except Exception as exc:
                outcome = exc
        return classify_probe_outcome(host, outcome)

    return list(await asyncio.gather(*(_uno(h) for h in entradas)))
