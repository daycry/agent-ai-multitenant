"""Las puertas de seguridad del marketplace, EJECUTADAS EN EL WORKER.

prod-13 ``task_prod13_01`` (hallazgo perf-1), la mitad de **latencia** de esa
casilla. La de event loop ya estaba: bandit/semgrep y el SDK de Docker corren bajo
``asyncio.to_thread`` y ``tests/unit/test_no_blocking_calls_in_event_loop.py`` lo
mide por hilo. Lo que eso no arregla es que el request siga durando lo que dura el
análisis —hasta 2 x 120 s de escáner más la prueba de humo—, que es lo que corta un
proxy y lo que hace reintentar a un cliente sobre una instalación cuyo estado
desconoce.

## Lo que este módulo NO hace, y por qué

No decide la política de instalación. La secuencia consentimiento → estado →
materialización → auditoría vive en ``api_server.marketplace.finalize`` y la
comparte con el camino síncrono del router. Duplicarla aquí habría creado dos
políticas de instalación que divergen al primer cambio: exactamente el «NO
política paralela» del ADR 0142.

Tampoco reimplementa las puertas: llama a
``InstallOrchestrator.run_gates_for_installation``, el MISMO pipeline del install
y del update. Importar ``api_server`` desde aquí no cruza una frontera de
despliegue — la imagen de workers se construye SOBRE la de api-server
(``ARG BASE_IMAGE``, ADR 0141) y este paquete ya lo importa en ~50 sitios.

## El artefacto: la trampa que este módulo evita a propósito

Un fetch fallido es ambiguo. Puede ser «este listing no tiene artefacto en disco»
—el skip honesto que documenta el ADR 0081, y que NO debe bloquear una
instalación, porque bloquearla cerraría en falso todo el catálogo pre-registry (la
regresión H4)— o puede ser «el artefacto existe, pero desde el worker no se
alcanza». Y lo segundo es hoy el caso probable: medido el 2026-08-19 en el stack
vivo, ``docker inspect api-server`` devuelve ``Mounts: []`` y
``/data/agent-platform/marketplace/artifacts`` **no existe en ninguno de los dos
contenedores**, así que el root de artefactos es almacenamiento local del
contenedor que lo escribió.

Tragarse los dos casos como uno convertiría el traslado de la puerta al worker en
un apagado silencioso de la puerta: verde en los tests, ``skipped`` en producción,
y nadie enterándose. Así que el productor observa el artefacto donde acepta la
request (``InstallOrchestrator.artifact_expected``), lo escribe en el audit row de
``gates_queued``, y aquí se distingue:

* ``artifact_expected=False`` → la ausencia es el skip honesto y la instalación
  sigue;
* ``artifact_expected=True`` y el fetch falla → **``blocked``** con motivo
  ``artifact_unreachable_from_worker``. Un fallo ruidoso y diagnosticable.

Cerrar ese hueco de verdad —un root de artefactos compartido y durable— es la Fase
B/C del ADR 0081 (registry + sandbox out-of-process), no esta casilla.

## Multi-tenancy

El worker corre con rol BYPASSRLS, así que **cada query lleva su ``tenant_id``
explícito**, y el ``tenant_id`` es el que viajó en el mensaje. Una instalación de
otro tenant no se encuentra: no hay 403 que confirme que existe, hay «no está».
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import desc, select

from workers.celery_app import app
from workers.config import get_settings
from workers.db import worker_session

_log = structlog.get_logger("workers.marketplace_gates")

#: Públicas: el productor (``api_server.celery_client``) declara las suyas y un
#: test compara las dos parejas. Un nombre distinto en cada lado deja el mensaje
#: en el broker para siempre mientras el endpoint devuelve 202 igualmente.
TASK_NAME = "workers.marketplace_run_install_gates"
QUEUE = "marketplace"

__all__ = ["QUEUE", "TASK_NAME", "run_install_gates"]


@app.task(name=TASK_NAME)  # type: ignore[untyped-decorator]
def run_install_gates(*, installation_id: str, tenant_id: str) -> dict[str, Any]:
    """Entry point Celery. Nunca propaga: un fallo deja rastro y estado.

    Propagar haría que Celery reintentase el mensaje sobre una instalación que
    quizá ya quedó ``blocked``, y el segundo intento escribiría un segundo audit
    row de aborto por el mismo rechazo. El resultado se devuelve como estado.
    """
    try:
        return asyncio.run(_run_install_gates_async(UUID(installation_id), UUID(tenant_id)))
    except Exception as exc:  # defensivo: la task no puede morir sin dejar rastro
        _log.exception(
            "marketplace.gates.task_failed",
            installation_id=installation_id,
            error=str(exc),
        )
        return {"installation_id": installation_id, "status": f"error:{type(exc).__name__}"}


async def _run_install_gates_async(installation_id: UUID, tenant_id: UUID) -> dict[str, Any]:
    from api_server.db.marketplace import (
        InstallationStatus,
        MarketplaceAuditAction,
        MarketplaceInstallation,
        MarketplaceListing,
    )
    from api_server.marketplace.finalize import finalize_installation
    from api_server.marketplace.install import (
        InstallError,
        InstallOrchestrator,
        LocalArtifactFetcher,
        default_artifact_root,
    )
    from api_server.marketplace.materialize import MaterializeError

    settings = get_settings()
    async with worker_session(settings) as session:
        # Tenant explícito en las DOS lecturas: el worker es BYPASSRLS.
        installation = (
            await session.execute(
                select(MarketplaceInstallation).where(
                    MarketplaceInstallation.id == installation_id,
                    MarketplaceInstallation.tenant_id == tenant_id,
                    MarketplaceInstallation.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if installation is None:
            _log.warning(
                "marketplace.gates.installation_gone",
                installation_id=str(installation_id),
            )
            return {"installation_id": str(installation_id), "status": "gone"}

        # Idempotencia: Redis puede re-entregar un mensaje (visibility timeout) y
        # `task_acks_late` lo hace probable en una task larga. Sólo se analiza lo
        # que sigue esperando veredicto; una instalación ya resuelta se deja
        # exactamente como está en vez de re-materializarse.
        if installation.status != InstallationStatus.ANALYZING.value:
            _log.info(
                "marketplace.gates.already_resolved",
                installation_id=str(installation_id),
                status=installation.status,
            )
            return {"installation_id": str(installation_id), "status": installation.status}

        listing = (
            await session.execute(
                select(MarketplaceListing).where(
                    MarketplaceListing.id == installation.listing_id,
                    MarketplaceListing.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if listing is None:
            await _block(
                session,
                installation=installation,
                tenant_id=tenant_id,
                reason="listing_gone",
                message="el listing desapareció entre la petición y el análisis",
            )
            return {"installation_id": str(installation_id), "status": "blocked"}

        handoff = await _handoff_detail(
            session, installation_id=installation_id, tenant_id=tenant_id
        )
        actor = handoff.get("actor") or "system:marketplace_gates"
        requested_permissions = handoff.get("requested_permissions") or []
        artifact_expected = bool(handoff.get("artifact_expected", True))

        orchestrator = InstallOrchestrator(
            fetcher=LocalArtifactFetcher(root_dir=default_artifact_root()),
            public_key_pem=_signing_key(),
        )
        try:
            gates = await orchestrator.run_gates_for_installation(
                session=session,
                tenant_id=tenant_id,
                actor=actor,
                listing=listing,
                installation_id=installation_id,
                artifact_expected=artifact_expected,
            )
        except InstallError as exc:
            # `_abort` ya escribió Y COMITEÓ su audit row, enlazado a esta
            # instalación (`ctx.installation_id`), con el motivo dentro. Aquí
            # sólo queda mover el estado que el cliente consulta.
            await _block(
                session,
                installation=installation,
                tenant_id=tenant_id,
                reason=type(exc).__name__,
                message=str(exc),
                audit=False,
            )
            return {"installation_id": str(installation_id), "status": "blocked"}

        # Las puertas pasaron: cierre con la MISMA política que el router.
        try:
            await finalize_installation(
                session,
                installation=installation,
                listing=listing,
                requested_permissions=requested_permissions,
                actor=actor,
                gates=gates,
                audit_action=MarketplaceAuditAction.INSTALL,
            )
            await session.commit()
        except MaterializeError as exc:
            await session.rollback()
            # ADR 0100: un manifest que no puede materializar no puede quedar
            # `enabled`. En el router es un 422 y la fila no llega a existir;
            # aquí la fila existe, así que el equivalente honesto es `blocked`.
            await _block(
                session,
                installation=installation,
                tenant_id=tenant_id,
                reason="materialization_failed",
                message=str(exc),
            )
            return {"installation_id": str(installation_id), "status": "blocked"}

        _log.info(
            "marketplace.gates.passed",
            installation_id=str(installation_id),
            status=installation.status,
        )
        return {"installation_id": str(installation_id), "status": installation.status}

    # Sin `finally` que comitee: cada rama cierra su propia transacción con su
    # `commit`/`rollback`. Un commit de barrido aquí escribiría también los
    # estados a medias de la rama que ya abortó. Y las transacciones NO se abren
    # con `session.begin()`: la sesión ya la abrió implícitamente en el primer
    # `select`, y `begin()` sobre una transacción viva es `InvalidRequestError`.


async def _handoff_detail(
    session: Any, *, installation_id: UUID, tenant_id: UUID
) -> dict[str, Any]:
    """El detalle del audit row de `gates_queued` que dejó el productor.

    Lleva lo que el worker no puede deducir: los permisos que pidió el llamante,
    el actor original (para que la auditoría no atribuya la instalación a
    "system") y la observación del artefacto. Se lee el MÁS RECIENTE por si una
    instalación se re-encoló.
    """
    from api_server.db.marketplace import MarketplaceAuditAction, MarketplaceAuditEntry

    row = (
        await session.execute(
            select(MarketplaceAuditEntry)
            .where(
                MarketplaceAuditEntry.installation_id == installation_id,
                MarketplaceAuditEntry.tenant_id == tenant_id,
                MarketplaceAuditEntry.action == MarketplaceAuditAction.GATES_QUEUED.value,
            )
            .order_by(desc(MarketplaceAuditEntry.created_at))
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        # No se inventa nada: sin la fila de handoff no hay permisos que conceder
        # y el artefacto se asume esperado (fallar cerrado).
        _log.warning("marketplace.gates.no_handoff_row", installation_id=str(installation_id))
        return {}
    detail = dict(row.detail or {})
    detail["actor"] = row.actor
    return detail


async def _block(
    session: Any,
    *,
    installation: Any,
    tenant_id: UUID,
    reason: str,
    message: str,
    audit: bool = True,
) -> None:
    """Deja la instalación en `blocked` y (salvo que ya exista) su audit row.

    `audit=False` para el caso en que `InstallOrchestrator._abort` ya escribió el
    suyo: dos filas por un solo rechazo dejan el rastro ambiguo — el mismo motivo
    por el que el ADR 0142 D7 le dio acción propia al `refresh` en vez de emitir
    un segundo `update`.
    """
    from api_server.db.marketplace import (
        InstallationStatus,
        MarketplaceAuditAction,
        MarketplaceAuditEntry,
    )

    installation.status = InstallationStatus.BLOCKED.value
    if audit:
        session.add(
            MarketplaceAuditEntry(
                tenant_id=tenant_id,
                actor="system:marketplace_gates",
                action=MarketplaceAuditAction.INSTALL.value,
                listing_id=installation.listing_id,
                installation_id=installation.id,
                detail={
                    "aborted": True,
                    "reason": reason,
                    "message": message,
                    "status": InstallationStatus.BLOCKED.value,
                },
            )
        )
    await session.commit()
    _log.warning(
        "marketplace.gates.blocked",
        installation_id=str(installation.id),
        reason=reason,
    )


def _signing_key() -> bytes | None:
    """La clave pública de firma de la plataforma, si está configurada.

    Misma resolución que `get_install_orchestrator` en el api-server: sin clave,
    un listing `verified` falla cerrado en la puerta de firma, igual que allí.
    """
    import os

    key = os.environ.get("MARKETPLACE_SIGNING_PUBLIC_KEY")
    return key.encode("utf-8") if key else None
