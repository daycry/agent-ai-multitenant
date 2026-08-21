"""El 202 de la instalación: la fila nace, las puertas se encolan (prod-13).

## Qué problema resuelve, que NO es el del event loop

`task_prod13_01` tenía dos mitades y sólo una estaba hecha. La de **event loop**
—que bandit/semgrep y el SDK de Docker no congelen el proceso— se resolvió con
`asyncio.to_thread` y la mide por hilo
`tests/unit/test_no_blocking_calls_in_event_loop.py`. Esta es la otra: con
`to_thread` el request **sigue durando lo que dura el análisis**, hasta 2 x 120 s de
escáner (`DEFAULT_SCAN_TIMEOUT_S`) más la prueba de humo del sandbox. Cuatro
minutos no caben en un HTTP aunque no bloqueen a nadie — los corta el timeout del
proxy, y entonces el cliente reintenta sobre una instalación cuyo estado no
conoce.

## El recurso de estado es la INSTALACIÓN, no el despliegue

El plan prod-13 apuntaba a la entidad de despliegue de marketplace-v2
(`marketplace_deployments`, ADR 0142) como «el recurso de estado que el 202
necesita». No sirve, y conviene que quede escrito por qué: un despliegue se crea
**después** de que la instalación exista, es por proyecto, y su `status` es
`active`/`disabled`/`retired` — el ciclo de vida de algo vivo, no el progreso de
un trabajo. Cuando el 202 responde no hay ningún despliegue al que apuntar.

El hermano correcto de esa entidad, un nivel más arriba, sí sirve y ya existe:
`marketplace_installations`. Está acotado por tenant con RLS, ya tiene columna
`status`, y es exactamente lo que la instalación crea. Así que el 202 devuelve la
instalación en un estado transitorio (`analyzing`) y el cliente la consulta por
`GET /marketplace/installations/{id}`. **Ni tabla nueva ni segunda máquina de
estados** — la regla de «NO política paralela» del ADR 0142.

Y no hace falta migración: `status` es `varchar(16)` sin CHECK (migración 0041),
o sea que el conjunto de valores se aplica en Python y en ningún sitio del DDL.

## Por qué el encolado va con `schedule_after_commit` y no aquí mismo

La task tiene que salir **después** de que la fila sea durable: el worker abre su
propia sesión y busca la instalación por `id`, así que encolar antes del commit
abre la carrera clásica —el consumidor llega primero y no encuentra nada— con el
agravante de que el mensaje ya no vuelve y la instalación se queda en `analyzing`
para siempre. Es literalmente el fallo que documenta el docstring de
:func:`api_server.db.after_commit.schedule_after_commit` («publishing inline lets a fast
consumer read the not-yet-committed row and silently skip it»).

La primera versión de este módulo lo resolvía con un `await session.commit()` aquí
dentro, y estaba **mal por una razón que no se ve leyendo el código**:
`open_tenant_session` acota RLS con `set_config(..., is_local := true)`, o sea
GUC de TRANSACCIÓN. Un commit a media request cierra esa transacción y con ella
`app.tenant_id` y `app.user_id`: cualquier consulta posterior del handler correría
**sin contexto de tenant**. En este camino no había ninguna después, así que
funcionaba y los tests pasaban — un campo de minas para el siguiente que añada una
línea. Se usa el mecanismo de la casa, que además ya trae el «best-effort» del
publish (un fallo se loguea y no rompe una request ya comiteada).

**Y lo que ese best-effort NO arregla, que conviene saber:** si el broker está
caído cuando toca publicar, la instalación se queda en `analyzing` sin nadie que
la mueva. No hay barrido de beat que las rescate (el equivalente de
`sweep_pending_documents` para la ingesta) — la recuperación hoy es manual y
visible: la instalación se lee por su URL, se revoca con `DELETE` y se reinstala.
Un barrido sería la respuesta completa y es otra tarea.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.after_commit import schedule_after_commit
from api_server.db.marketplace import (
    InstallationStatus,
    MarketplaceAuditAction,
    MarketplaceAuditEntry,
    MarketplaceInstallation,
    MarketplaceListing,
)

_log = structlog.get_logger("api_server.marketplace.async_gates")

#: Firma del productor. Se inyecta para que el test pueda medir que se llamó
#: (y con qué) sin tocar un broker.
EnqueueFn = Callable[..., Awaitable[bool]]

#: Firma del registrador post-commit. Inyectable por el mismo motivo: el test
#: unitario usa una sesión de mentira que no pasa por `open_tenant_session`.
ScheduleFn = Callable[[Any, Callable[[], Awaitable[None]]], None]

__all__ = ["queue_install_gates"]


async def queue_install_gates(
    session: AsyncSession,
    *,
    installation: MarketplaceInstallation,
    listing: MarketplaceListing,
    actor: str,
    requested_permissions: Sequence[Any],
    artifact_expected: bool = True,
    enqueue: EnqueueFn | None = None,
    schedule: ScheduleFn | None = None,
) -> dict[str, Any]:
    """Deja ``installation`` en ``analyzing`` y encola sus puertas.

    ``installation`` tiene que venir ya flusheada (necesita ``id``: es lo que el
    202 devuelve y lo que la task va a buscar).

    ``requested_permissions`` se guarda en el detalle del audit row porque el
    cierre de la instalación ocurre en OTRO proceso: sin esto, el worker no
    sabría qué permisos pidió el llamante y un listing `verified` acabaría
    instalado con la lista vacía — la capacidad concedida a medias que
    `finalize_installation` existe para evitar.

    Devuelve el detalle escrito en la auditoría (lo que afirman los tests).
    """
    installation.status = InstallationStatus.ANALYZING.value

    detail: dict[str, Any] = {
        "version": installation.version,
        "trust_level": listing.trust_level,
        "status": InstallationStatus.ANALYZING.value,
        "project_id": (str(installation.project_id) if installation.project_id else None),
        # Lo que el worker necesita para cerrar la instalación con la MISMA
        # política que el camino síncrono.
        "requested_permissions": list(requested_permissions or []),
        # La observación del productor sobre el artefacto: distingue «este
        # listing no tiene artefacto» de «existe y desde el worker no se
        # alcanza». Ver `InstallOrchestrator.artifact_expected`.
        "artifact_expected": bool(artifact_expected),
        # `after_commit` y no `published: true`: cuando esta fila se escribe, el
        # mensaje todavía NO ha salido. Decir que sí sería la mentira barata.
        "gates": {"queued": True, "queue": "marketplace", "publish": "after_commit"},
    }
    session.add(
        MarketplaceAuditEntry(
            tenant_id=installation.tenant_id,
            actor=actor,
            action=MarketplaceAuditAction.GATES_QUEUED.value,
            listing_id=listing.id,
            installation_id=installation.id,
            detail=detail,
        )
    )
    await session.flush()

    publisher = enqueue if enqueue is not None else _default_enqueue
    register = schedule if schedule is not None else schedule_after_commit
    installation_id = installation.id
    tenant_id = installation.tenant_id
    listing_id = listing.id

    async def _publish() -> None:
        published = await publisher(installation_id=installation_id, tenant_id=tenant_id)
        if not published:
            _log.warning(
                "marketplace.gates.enqueue_failed",
                installation_id=str(installation_id),
                listing_id=str(listing_id),
            )

    # Los ids se capturan ARRIBA, fuera del closure sobre `installation`: el
    # callback corre cuando la sesión ya comiteó, y leer un atributo de una
    # instancia expirada dispararía un refresh sobre una transacción cerrada.
    register(session, _publish)
    return detail


async def _default_enqueue(*, installation_id: UUID, tenant_id: UUID) -> bool:
    """El productor real. Import diferido para no arrastrar Celery en los tests."""
    from api_server.celery_client import enqueue_marketplace_install_gates

    return await enqueue_marketplace_install_gates(
        installation_id=installation_id, tenant_id=tenant_id
    )
