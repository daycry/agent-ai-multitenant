"""Liveness y readiness, que no son lo mismo (`task_audit14_08`).

Hallazgo **AUD14-06** de la auditoría integral del 2026-07-14: la api-server sólo
tenía `/healthz`, que responde `{"status": "ok"}` en cuanto el proceso está en pie.
Eso no distingue «arrancado» de «listo», y las dos preguntas tienen consumidores
distintos:

``GET /healthz`` — **liveness**: ¿hay que reiniciar el contenedor? Contesta el
proceso y NADA MÁS. Meterle una comprobación de PostgreSQL sería el clásico bucle
de reinicios: con la BD caída, reiniciar la api-server no arregla nada y encima
tira las conexiones sanas que le quedaban. Es el `healthcheck` de Compose.

``GET /readyz`` — **readiness**: ¿puede este proceso atender tráfico AHORA? Prueba
las dependencias sin las que toda petición fallaría —PostgreSQL y Redis— y
responde 503 con el detalle de cuál falla. Es lo que debe consultar un proxy o un
`depends_on` antes de mandarle tráfico.

Qué NO se comprueba, a propósito
--------------------------------
Vault, Ollama y Docling son **opcionales**: hay despliegues que no los tienen y
funcionan. Incluirlos convertiría un auxiliar caído en un flapping de readiness
de la api-server entera (riesgo 5 del plan de remediación). La lista crítica es
corta y está en :data:`READINESS_CHECKS`.

Secretos
--------
El cuerpo del 503 lo lee un healthcheck de Docker y acaba en `docker logs` y en
pantallas de estado. Los errores de driver pueden arrastrar el DSN, así que el
detalle de cada check pasa por :func:`_scrub` (borra `usuario:contraseña@` y
recorta) antes de salir. Se prueba en `tests/unit/test_readiness_scrub.py`, NO en
la aserción end-to-end del 503: se comprobó desactivando el saneado y el test de
integración seguía verde, porque hoy ni asyncpg ni redis-py meten la credencial
en su `str(exc)`. Aquella aserción se conserva como red para el día que un driver
nuevo sí lo haga, pero la que muerde es la unitaria.

Timeout
-------
Cada check tiene deadline propio (:data:`READINESS_TIMEOUT_SECONDS`): un
PostgreSQL que acepta el TCP pero no contesta colgaría la petición de readiness
para siempre, que es peor que un 503. Se configura con la variable de entorno
``API_SERVER_READINESS_TIMEOUT_SECONDS``; el nombre es el que tendría el campo
`readiness_timeout_seconds` de `Settings` (mismo `env_prefix`), para que moverlo
allí no cambie el contrato de despliegue.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text as sa_text

from api_server.config import Settings, get_settings

router = APIRouter(tags=["health"])

#: Deadline por check. 2 s es corto a propósito: readiness se consulta a menudo y
#: una dependencia que tarda más de eso ya no está sirviendo tráfico útil.
READINESS_TIMEOUT_SECONDS = float(os.getenv("API_SERVER_READINESS_TIMEOUT_SECONDS", "2.0"))

#: Longitud máxima del detalle de un check. Un traceback de driver entero no
#: aporta y sí engorda cada línea de log del healthcheck.
_MAX_DETAIL = 200

#: `//usuario:contraseña@` en cualquier URL que se cuele en un mensaje de error.
_CREDENTIALS_IN_URL = re.compile(r"(?<=//)[^/@\s]+(?=@)")


def _scrub(message: str) -> str:
    """Quita credenciales de un mensaje de error y lo recorta."""
    clean = _CREDENTIALS_IN_URL.sub("***", message).strip()
    if len(clean) > _MAX_DETAIL:
        clean = f"{clean[:_MAX_DETAIL]}…"
    return clean


async def _ping_postgres(_settings: Settings) -> None:
    """`SELECT 1` con el MISMO engine que sirve el tráfico (rol RLS).

    Recibe `Settings` para compartir firma con los demás checks —el runner los
    llama igual a todos— aunque este no lo use: la URL ya está dentro del engine
    cacheado. Import perezoso del sessionmaker para que el engine se resuelva en
    el momento de la petición, así una reconfiguración (o una recuperación de la
    BD) se ve sin reiniciar el proceso.
    """
    from api_server.db.session import get_sessionmaker

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        await session.execute(sa_text("SELECT 1"))


async def _ping_redis(settings: Settings) -> None:
    """`PING` con un cliente efímero.

    A propósito NO se reutiliza el cliente cacheado de `auth.deps`: un check no
    debe poder dejar en mal estado el cliente que sirve las sesiones.
    """
    from redis.asyncio import Redis

    client: Redis = Redis.from_url(settings.redis_url)
    try:
        await client.ping()
    finally:
        await client.aclose()


#: Las dependencias SIN las que ninguna petición funciona. Añadir aquí un
#: servicio opcional es cómo se provoca un flapping de readiness.
READINESS_CHECKS: tuple[tuple[str, str], ...] = (
    ("postgresql", "_ping_postgres"),
    ("redis", "_ping_redis"),
)


def _probe(name: str) -> Callable[[Settings], Awaitable[None]]:
    """Resuelve el check por nombre EN EL MOMENTO de usarlo.

    Indirection deliberada: los tests sustituyen `_ping_redis` por un doble que
    se cuelga, y un `tuple` de funciones capturadas al importar el módulo se
    quedaría con la original.
    """
    probe: Callable[[Settings], Awaitable[None]] = globals()[name]
    return probe


async def _run_check(name: str, probe_name: str, settings: Settings) -> dict[str, Any]:
    started = time.monotonic()
    detail: str | None = None
    ok = True
    try:
        await asyncio.wait_for(_probe(probe_name)(settings), timeout=READINESS_TIMEOUT_SECONDS)
    except TimeoutError:
        ok = False
        detail = f"timeout tras {READINESS_TIMEOUT_SECONDS:g}s"
    except Exception as exc:  # cualquier fallo de dependencia es un no-listo
        ok = False
        detail = _scrub(f"{type(exc).__name__}: {exc}")
    outcome: dict[str, Any] = {
        "name": name,
        "ok": ok,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }
    if detail is not None:
        outcome["detail"] = detail
    return outcome


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness del PROCESO. No consulta dependencias externas — ver módulo."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz() -> JSONResponse:
    """Readiness: 200 si el proceso puede atender tráfico, 503 con el detalle si no.

    Los checks corren en paralelo (el tiempo total es el del más lento, no la
    suma) y el resultado NO se cachea: en cuanto la dependencia vuelve, readiness
    vuelve a 200 sin reiniciar nada.
    """
    settings = get_settings()
    checks = await asyncio.gather(
        *(_run_check(name, probe, settings) for name, probe in READINESS_CHECKS)
    )
    ready = all(check["ok"] for check in checks)
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready", "checks": list(checks)},
    )


__all__ = ["READINESS_CHECKS", "READINESS_TIMEOUT_SECONDS", "router"]
