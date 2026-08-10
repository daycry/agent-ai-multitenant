"""Cliente de Vault compartido, con renovación de token en segundo plano.

Plan prod-10 `task_prod10_07` (hallazgo secrets-4).

## La avería que estaba programada

Hasta hoy cada consumidor de Vault construía su propio ``hvac.Client`` con el
token estático de ``API_SERVER_VAULT_TOKEN`` y lo cacheaba en un singleton de
módulo (``routers/llm_providers.py``, ``routers/mcp.py``). Buscado en todo el
repositorio: **cero llamadas a ``renew_self`` o ``lookup_self``**.

Un service token de Vault tiene TTL — 32 días por defecto en la configuración
que documenta el instalador. El día que caduque, TODAS las credenciales de
proveedor LLM y toda resolución de ``auth_ref`` de MCP dejan de funcionar a la
vez, sin ningún cambio de configuración que lo explique. Es la peor clase de
avería: la que ocurre un mes después del despliegue que la causó, cuando ya
nadie relaciona una cosa con la otra.

## Qué hace este módulo

1. :func:`build_vault_client` — UNA fábrica para todos los consumidores. Devuelve
   ``None`` (no lanza) cuando Vault no está cableado o ``hvac`` no está
   instalado, que es el contrato que los routers ya esperaban: sin Vault la
   plataforma arranca y las escrituras que lo necesitan devuelven 503.
2. :class:`VaultTokenManager` — ``lookup_self`` al arrancar (deja el TTL en el
   log y en la métrica) y ``renew_self`` en un hilo de fondo antes de la mitad
   del TTL.
3. :func:`vault_token_ttl_gauge` — ``agentic_vault_token_ttl_seconds``, la serie
   sobre la que prod-08 cuelga la alerta de «al token le quedan horas».

## Por qué un hilo y no una tarea asyncio

``hvac`` va sobre ``requests``, que es SÍNCRONO. Las dependencias de FastAPI que
construyen el cliente son funciones ``def``, así que FastAPI las ejecuta en el
threadpool y **no hay bucle de eventos** al que engancharse. Y si lo hubiera,
meter una llamada bloqueante dentro del loop es el hallazgo perf-7 otra vez (un
Vault inalcanzable congelaba el api-server entero). Un hilo daemon con
``time.sleep`` es lo correcto aquí, y además muere con el proceso.

## Por qué la renovación va a la MITAD del TTL

Renovar en el 90% deja una ventana de minutos: si Vault está sellado o
reiniciándose justo entonces, el token caduca y la avería es la misma que
queríamos evitar. A la mitad hay margen para varios reintentos. El riesgo 6 del
plan lo dice explícito: un ``renew_self`` que falla en silencio reproduce
exactamente el problema — por eso el fallo se loguea a nivel error y el bucle
NO muere.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import structlog
from prometheus_client import REGISTRY, CollectorRegistry, Gauge

_logger = structlog.get_logger(__name__)

#: Nombres de las métricas. Siguen el prefijo `agentic_` del resto del exporter
#: (`api_server.metrics`) para que las reglas de Prometheus puedan seleccionarlas.
TTL_GAUGE_NAME = "agentic_vault_token_ttl_seconds"
SEALED_GAUGE_NAME = "agentic_vault_sealed"

#: Techo de espera (segundos) de CADA llamada HTTP a Vault. Mismo criterio que
#: `routers/llm_providers._VAULT_TIMEOUT_SECONDS`: Vault vive en el mismo host,
#: una lectura sana tarda milisegundos, y por encima de esto está roto.
VAULT_TIMEOUT_SECONDS = 5.0

#: Suelo y techo del intervalo de renovación. El suelo evita martillear a Vault
#: con un token de TTL absurdamente corto; el techo garantiza que una renovación
#: ocurra al menos una vez al día aunque el TTL sea de meses, para que un fallo
#: se descubra pronto y no el día de la caducidad.
MIN_RENEW_INTERVAL_S = 30.0
MAX_RENEW_INTERVAL_S = 12 * 3600.0


class VaultTokenClient(Protocol):
    """La superficie MÍNIMA que este módulo necesita del cliente de Vault.

    Se declara como Protocol para que los tests inyecten un doble sin instalar
    ``hvac`` — y para dejar escrito, en un sitio que se comprueba, que de todo
    ``hvac`` sólo dependemos de dos métodos.
    """

    def lookup_self(self) -> dict[str, Any]: ...

    def renew_self(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class TokenInfo:
    """Lo que ``lookup_self`` cuenta del token. NO lleva el token."""

    ttl_seconds: int
    renewable: bool
    policies: tuple[str, ...]


def vault_token_ttl_gauge(registry: CollectorRegistry) -> Gauge:
    """El gauge del TTL, registrado de forma IDEMPOTENTE.

    ``prometheus_client`` prohíbe registrar dos veces el mismo nombre y el
    registro del proceso es global: construir la app dos veces —cosa que hace
    cualquier suite con más de un módulo de integración— reventaría con
    ``Duplicated timeseries``. Mismo patrón que ``api_server.metrics``.
    """
    mapping = getattr(registry, "_names_to_collectors", None)
    if isinstance(mapping, dict):
        existing = mapping.get(TTL_GAUGE_NAME)
        if existing is not None:
            assert isinstance(existing, Gauge)
            return existing
    return Gauge(
        TTL_GAUGE_NAME,
        "Segundos que le quedan al token de Vault del api-server antes de caducar.",
        registry=registry,
    )


def vault_sealed_gauge(registry: CollectorRegistry) -> Gauge:
    """``agentic_vault_sealed``: 1 = sellado o sin inicializar, 0 = operativo.

    Idempotente por el mismo motivo que :func:`vault_token_ttl_gauge`.
    """
    mapping = getattr(registry, "_names_to_collectors", None)
    if isinstance(mapping, dict):
        existing = mapping.get(SEALED_GAUGE_NAME)
        if existing is not None:
            assert isinstance(existing, Gauge)
            return existing
    return Gauge(
        SEALED_GAUGE_NAME,
        "1 si Vault está sellado o sin inicializar (no sirve secretos), 0 si operativo.",
        registry=registry,
    )


@dataclass(frozen=True)
class SealProbe:
    """Resultado del sondeo de sellado.

    ``sealed`` es ``None`` cuando Vault no contestó: «no responde» no es
    «sellado», y confundirlos manda al operador a desellar un contenedor caído.
    """

    status: str  # "ok" | "degraded" | "down"
    detail: str | None
    sealed: bool | None


#: A dónde mandar al operador cuando Vault aparece sellado. El detalle viaja a
#: `/admin/system-health`, que es donde lo va a leer.
_UNSEAL_RUNBOOK = "docs/06-runbooks/restart-services.md"


async def probe_vault_seal(
    vault_url: str,
    *,
    timeout: float = VAULT_TIMEOUT_SECONDS,
    registry: CollectorRegistry | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SealProbe:
    """Pregunta a Vault si está sellado — de verdad.

    Se consulta ``/v1/sys/seal-status`` y NO ``/v1/sys/health`` porque el
    healthcheck del compose canónico pide health con
    ``sealedcode=200&uninitcode=200``: traduce «sellado» (503) y «sin
    inicializar» (501) a **200** a propósito, para que Vault no entre en bucle de
    reinicio antes de que nadie pueda desellarlo. El efecto secundario es que
    tras un reinicio del host todo el stack arranca contra un Vault inutilizable
    y nadie se entera (hallazgos secrets-5 / deploy-8). ``seal-status`` es el
    endpoint que dice la verdad, y no necesita token.

    El ``detail`` que devuelve viaja a `/admin/system-health`, así que no lleva
    nunca el texto crudo de una excepción — mismo criterio que ``_safe_detail``
    (error-obs-logging-6): eso filtra la topología interna a un dashboard.
    """
    gauge = vault_sealed_gauge(registry if registry is not None else REGISTRY)
    url = f"{vault_url.rstrip('/')}/v1/sys/seal-status"
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            response = await client.get(url)
        payload = response.json()
    except Exception as exc:
        _logger.warning(
            "vault.seal_probe.failed",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        detail = "timeout" if isinstance(exc, TimeoutError) else "connection failed"
        # El gauge se deja como estaba: ver el docstring del módulo de test.
        return SealProbe(status="down", detail=detail, sealed=None)

    if not isinstance(payload, dict):
        # Un proxy que devuelve 200 con HTML no es un Vault abierto.
        return SealProbe(status="degraded", detail="unexpected seal-status payload", sealed=None)

    initialized = payload.get("initialized")
    sealed = payload.get("sealed")

    if initialized is False:
        gauge.set(1.0)
        return SealProbe(
            status="degraded",
            detail=f"Vault is not initialised — run scripts/init-vault.sh ({_UNSEAL_RUNBOOK})",
            sealed=True,
        )
    if sealed is True:
        gauge.set(1.0)
        return SealProbe(
            status="degraded",
            detail=f"Vault is SEALED — no secret can be resolved. Unseal: {_UNSEAL_RUNBOOK}",
            sealed=True,
        )
    if sealed is False:
        gauge.set(0.0)
        return SealProbe(status="ok", detail=None, sealed=False)

    return SealProbe(status="degraded", detail="unexpected seal-status payload", sealed=None)


class VaultTokenManager:
    """Mantiene vivo un token de Vault renovable.

    No guarda el token: opera sobre un cliente ya autenticado. Así este objeto
    puede aparecer entero en un log o en un traceback sin filtrar nada.
    """

    def __init__(
        self,
        client: VaultTokenClient,
        *,
        sleep: Callable[[float], None] = time.sleep,
        registry: CollectorRegistry | None = None,
        logger: Any | None = None,
    ) -> None:
        self._client = client
        self._sleep = sleep
        self._logger = logger if logger is not None else _logger
        self._gauge = vault_token_ttl_gauge(registry if registry is not None else REGISTRY)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    # -- lectura -------------------------------------------------------------
    def lookup(self) -> TokenInfo | None:
        """TTL/renovabilidad del token, o ``None`` si Vault no contesta.

        Devolver ``None`` en vez de propagar es deliberado: la plataforma
        funciona sin Vault (las escrituras que lo requieren devuelven 503), así
        que un Vault sellado no puede impedir el arranque del api-server.
        """
        try:
            payload = self._client.lookup_self()
        except Exception as exc:
            self._logger.warning(
                "vault.token.lookup_failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return None

        data = payload.get("data") or {}
        info = TokenInfo(
            ttl_seconds=int(data.get("ttl") or 0),
            renewable=bool(data.get("renewable")),
            policies=tuple(str(p) for p in (data.get("policies") or ())),
        )
        self._gauge.set(float(info.ttl_seconds))
        self._logger.info(
            "vault.token.lookup",
            ttl_seconds=info.ttl_seconds,
            renewable=info.renewable,
            policies=list(info.policies),
        )
        return info

    # -- renovación ----------------------------------------------------------
    def renew_once(self) -> int | None:
        """Renueva y devuelve el TTL nuevo, o ``None`` si la renovación falló."""
        try:
            payload = self._client.renew_self()
        except Exception as exc:
            # ERROR, no warning: si esto se rompe y nadie lo ve, el token caduca
            # semanas después y la avería parece salida de la nada (riesgo 6).
            self._logger.error(
                "vault.token.renew_failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return None

        auth = payload.get("auth") or {}
        ttl = int(auth.get("lease_duration") or 0)
        self._gauge.set(float(ttl))
        self._logger.info("vault.token.renewed", ttl_seconds=ttl)
        return ttl

    def _interval_for(self, ttl_seconds: int) -> float:
        half = ttl_seconds / 2.0
        return max(MIN_RENEW_INTERVAL_S, min(half, MAX_RENEW_INTERVAL_S))

    def run_forever(self) -> None:
        """El bucle. Público para que el test lo ejecute con un reloj de mentira
        en vez de arrancar un hilo y esperar de verdad."""
        info = self.lookup()
        if info is None:
            self._logger.warning("vault.token.renewal_disabled", reason="lookup_failed")
            return
        if not info.renewable or info.ttl_seconds <= 0:
            # Un token de root (o cualquiera no renovable) no caduca o no se
            # puede renovar: martillear a Vault cada media hora no arregla nada.
            self._logger.info(
                "vault.token.renewal_not_needed",
                renewable=info.renewable,
                ttl_seconds=info.ttl_seconds,
            )
            return

        ttl = info.ttl_seconds
        while not self._stop.is_set():
            self._sleep(self._interval_for(ttl))
            if self._stop.is_set():
                return
            renewed = self.renew_once()
            if renewed:
                ttl = renewed
            # Si falló, se conserva el ttl anterior: el siguiente intento cae a
            # la misma distancia, que sigue dentro del margen. Rendirse aquí
            # sería exactamente el fallo silencioso que este manager evita.

    # -- ciclo de vida -------------------------------------------------------
    def start(self) -> threading.Thread:
        """Arranca el hilo de renovación. IDEMPOTENTE.

        `build_vault_client` puede llamarse desde varios routers; dos hilos
        renovando el mismo token es ruido, no redundancia.
        """
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self._thread
            thread = threading.Thread(
                target=self.run_forever,
                name="vault-token-renewal",
                daemon=True,
            )
            self._thread = thread
            thread.start()
            return thread

    def stop(self) -> None:
        self._stop.set()


# ---------------------------------------------------------------------------
# Fábrica compartida
# ---------------------------------------------------------------------------
_UNSET: object = object()


class _ClientCache:
    """Singleton de módulo (atributo de clase, no `global`, para que ruff
    PLW0603 no proteste y el hook de reset de los tests se lea claro)."""

    client: Any = _UNSET
    manager: VaultTokenManager | None = None


class HvacTokenAdapter:
    """Mapea la superficie de :class:`VaultTokenClient` sobre un ``hvac.Client``.

    Existe para que :class:`VaultTokenManager` no conozca la forma anidada
    ``client.auth.token.*`` de hvac — la misma razón por la que el instalador
    envuelve hvac en su propio Protocol.

    Público (era ``_HvacTokenAdapter``) porque ``workers.vault_client`` lo
    reutiliza: el worker tiene su propio token y su propia configuración, pero el
    mapeo sobre hvac es el mismo y duplicarlo garantizaría que uno de los dos se
    quedase atrás.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    def lookup_self(self) -> dict[str, Any]:
        result = self._client.auth.token.lookup_self()
        return dict(result) if result else {}

    def renew_self(self) -> dict[str, Any]:
        result = self._client.auth.token.renew_self()
        return dict(result) if result else {}


def build_vault_client() -> Any | None:
    """Un ``hvac.Client`` autenticado y con su token mantenido vivo, o ``None``.

    ``None`` significa «Vault no está cableado»: sin
    ``API_SERVER_VAULT_TOKEN`` o sin ``hvac`` instalado. Es el mismo contrato que
    ya tenían `routers/mcp.get_vault_resolver` y
    `routers/llm_providers.get_provider_vault_store`, así que sustituirlos por
    esta fábrica no cambia el comportamiento observable: cambia que ahora el
    token se renueva.
    """
    if _ClientCache.client is not _UNSET:
        return _ClientCache.client

    from api_server.config import get_settings

    settings = get_settings()
    if settings.vault_token is None:
        _ClientCache.client = None
        return None
    try:
        import hvac
    except ImportError:
        # hvac ausente = igual que sin token. Los llamantes degradan a 503 /
        # AUTH_ERROR en vez de reventar.
        _ClientCache.client = None
        return None

    # `hvac` va sobre `requests`, que SIN `timeout` espera indefinidamente. Un
    # Vault caído dejaba la lectura colgada dentro de un handler async y con ella
    # el bucle de eventos entero (hallazgo perf-7).
    client = hvac.Client(
        url=settings.vault_url,
        token=settings.vault_token.get_secret_value(),
        timeout=VAULT_TIMEOUT_SECONDS,
    )
    manager = VaultTokenManager(HvacTokenAdapter(client))
    manager.start()
    _ClientCache.client = client
    _ClientCache.manager = manager
    return client


def reset_vault_client_cache() -> None:
    """Hook de test: olvida el cliente cacheado y para el hilo de renovación."""
    if _ClientCache.manager is not None:
        _ClientCache.manager.stop()
    _ClientCache.manager = None
    _ClientCache.client = _UNSET
