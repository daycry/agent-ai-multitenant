"""Cliente de Vault DEL WORKER, con el token mantenido vivo.

Plan prod-10 `task_prod10_07` (hallazgo secrets-4), segunda mitad.

## Por qué hace falta un módulo aparte y no vale el del api-server

`api_server.vault_client.build_vault_client` lee ``API_SERVER_VAULT_URL`` /
``API_SERVER_VAULT_TOKEN``. El worker corre con SU propia configuración
(``WORKERS_VAULT_*``, y su token está acuñado contra la política ``workers`` que
escribe el bootstrap del instalador), así que llamar a la fábrica del api-server
desde el worker devolvía ``None`` — la misma trampa que ya costó que toda
ejecución de agente corriese con ``has_credential=False``.

Lo que SÍ se reutiliza es la pieza que importa:
:class:`api_server.vault_client.VaultTokenManager`. El calendario de renovación,
la métrica ``agentic_vault_token_ttl_seconds`` y la decisión de renovar a la
mitad del TTL son idénticos en los dos procesos; duplicarlos garantizaría que
uno de los dos se quedase atrás.

## La avería que cierra

El worker construía ``hvac.Client`` a mano en tres sitios —el job semanal de
rotación, la resolución de credencial LLM de cada ejecución y el clonado de
repos— y ninguno llamaba jamás a ``renew_self``. Un token periódico que nadie
renueva caduca al final de su período exactamente igual que uno de TTL fijo. El
apagón resultante es peor de diagnosticar que el del api-server: no sale un 503,
salen ejecuciones que corren sin credencial y fallan por su cuenta.

## Contrato

``build_worker_vault_client`` devuelve ``None`` cuando Vault no está cableado
(sin URL, sin token, o sin ``hvac`` instalado). Los tres llamantes ya sabían
degradar ante ``None`` —ciclo de rotación ``SKIPPED``, resolución sin credencial,
clonado sin secreto git—, así que sustituir sus constructores por esta fábrica no
cambia nada observable: cambia que ahora el token se renueva.
"""

from __future__ import annotations

import threading
from typing import Any

import structlog
from api_server.vault_client import (
    VAULT_TIMEOUT_SECONDS,
    HvacTokenAdapter,
    VaultTokenManager,
)

_log = structlog.get_logger("workers.vault_client")

__all__ = [
    "build_worker_vault_client",
    "reset_worker_vault_client_cache",
    "worker_vault_token_manager",
]

_UNSET: object = object()


class _ClientCache:
    """Singleton de módulo como atributo de clase (mismo criterio que en el
    api-server: sin `global`, y con un hook de reset legible para los tests)."""

    client: Any = _UNSET
    manager: VaultTokenManager | None = None
    lock = threading.Lock()


def _new_hvac_client(url: str, token: str) -> Any:
    """Costura de construcción: única línea que toca ``hvac`` de verdad.

    Aislarla permite que el test inyecte un doble sin manosear ``sys.modules``, y
    deja el `import` perezoso —``hvac`` sigue siendo opcional en los entornos que
    no hablan con Vault—.
    """
    import hvac

    # SIN `timeout`, `requests` espera indefinidamente: un Vault caído dejaba
    # colgado el hilo del worker que resolvía la credencial (mismo criterio que
    # `api_server.vault_client`, hallazgo perf-7).
    return hvac.Client(url=url, token=token, timeout=VAULT_TIMEOUT_SECONDS)


def build_worker_vault_client(settings: Any | None = None) -> Any | None:
    """Un ``hvac.Client`` del worker con su token renovándose, o ``None``.

    Cacheado: los tres consumidores viven en el mismo proceso Celery y comparten
    un único token. Tres hilos renovándolo sería ruido, no redundancia.
    """
    if _ClientCache.client is not _UNSET:
        return _ClientCache.client

    if settings is None:
        from workers.config import get_settings

        settings = get_settings()

    url = getattr(settings, "vault_url", None)
    token = getattr(settings, "vault_token", None)
    if not url or not token:
        # NO se cachea el `None`: en el worker la configuración puede llegar
        # tarde (una task que se ejecuta antes de que el env esté completo), y
        # memorizar «no hay Vault» dejaría el proceso sin Vault para siempre.
        return None

    with _ClientCache.lock:
        if _ClientCache.client is not _UNSET:
            return _ClientCache.client
        try:
            client = _new_hvac_client(str(url), str(token))
        except ImportError:  # pragma: no cover - hvac es dependencia declarada
            _log.warning("worker_vault.hvac_missing")
            return None

        manager = VaultTokenManager(HvacTokenAdapter(client), logger=_log)
        manager.start()
        _ClientCache.client = client
        _ClientCache.manager = manager
        return client


def worker_vault_token_manager() -> VaultTokenManager | None:
    """El manager vivo, o ``None``. Existe para que los tests (y un futuro probe
    de salud) puedan mirarlo sin reconstruir el cliente."""
    return _ClientCache.manager


def reset_worker_vault_client_cache() -> None:
    """Hook de test: olvida el cliente y para el hilo de renovación."""
    if _ClientCache.manager is not None:
        _ClientCache.manager.stop()
    _ClientCache.manager = None
    _ClientCache.client = _UNSET
