"""El cierre de una instalación: consentimiento → estado → materialización → auditoría.

## Por qué existe este módulo

Hasta prod-13 ``task_prod13_01`` esta secuencia vivía **inline** en el handler de
``POST /marketplace/installations``. Al mover las puertas de seguridad a Celery
(la mitad de LATENCIA de esa casilla) aparece un segundo punto donde hay que
cerrar exactamente la misma instalación: el worker, cuando las puertas pasan.

Copiarla habría creado **dos políticas de instalación** que divergen al primer
cambio —la trampa que el ADR 0142 llama «NO política paralela» y que
``verificar-antes-de-implementar`` §5 documenta como el patrón dominante de esta
base—. Así que la secuencia se extrae aquí y la llaman los dos caminos:

* el síncrono (``routers/marketplace/installations.py``), que la ejecuta dentro
  del request;
* el asíncrono (``workers/marketplace_gates.py``), que la ejecuta cuando la task
  termina las puertas.

Lo que decide, en orden, y por qué el orden importa:

1. **El consentimiento manda sobre el estado.** Un listing cuyo nivel de
   confianza exige consentimiento por permiso nace ``disabled`` y con CERO
   permisos concedidos, aunque el llamante haya pedido algunos: los concede el
   flujo de consentimiento, no el de instalación (decisión (a)+(b) de
   ``task_09_07``).
2. **Sólo un ``enabled`` materializa** (ADR 0100). Materializar algo que aún no
   está permitido sería entregar la capacidad antes del permiso.
3. **El audit row va en la MISMA transacción.** Si la instalación se comitea y su
   registro no, queda una capacidad concedida sin rastro de quién la concedió.

No comitea: el llamante es dueño de su transacción (el router la cierra al
responder, el worker la cierra al terminar la task).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.marketplace import (
    InstallationStatus,
    MarketplaceAuditAction,
    MarketplaceAuditEntry,
    MarketplaceInstallation,
    MarketplaceListing,
)
from api_server.marketplace.consent import consent_required_for

_log = structlog.get_logger("api_server.marketplace.finalize")

__all__ = ["finalize_installation"]


async def finalize_installation(
    session: AsyncSession,
    *,
    installation: MarketplaceInstallation,
    listing: MarketplaceListing,
    requested_permissions: Sequence[Any],
    actor: str,
    gates: dict[str, Any] | None,
    audit_action: MarketplaceAuditAction = MarketplaceAuditAction.INSTALL,
) -> dict[str, Any]:
    """Cierra ``installation`` sobre una fila YA persistida (con ``id``).

    ``installation`` tiene que venir flusheada: la materialización necesita su
    ``id`` para colgar de ella la tool/skill nativa. ``gates`` es el informe de
    las puertas (o su skip honesto) que viaja en el detalle del audit row.

    Propaga ``MaterializeError`` sin tocar la transacción — el llamante decide si
    eso es un 422 (router) o un ``blocked`` (worker).

    Devuelve el detalle del audit row escrito, que es lo que los tests afirman y
    lo que el llamante puede loguear.
    """
    needs_consent = consent_required_for(listing.trust_level)
    if needs_consent:
        final_status = InstallationStatus.DISABLED.value
        granted: list[Any] = []
    else:
        final_status = InstallationStatus.ENABLED.value
        granted = list(requested_permissions or [])

    installation.status = final_status
    installation.granted_permissions = granted
    installation.denied_permissions = list(installation.denied_permissions or [])

    # ADR 0100 (pieza 2): una instalación que nace ENABLED materializa su
    # capacidad nativa en la misma transacción — skill o tool de red
    # (mcp_tool/http_endpoint); python/docker siguen diferidos honestos hasta el
    # sandbox out-of-process (ADR 0081 B/C). Import diferido: `materialize`
    # arrastra los repos de tools/skills y este módulo lo importa el worker.
    materialize_summary: dict[str, Any] | None = None
    if final_status == InstallationStatus.ENABLED.value:
        from api_server.marketplace.materialize import materialize_installation

        materialize_summary = (
            await materialize_installation(session, installation=installation, listing=listing)
        ).as_dict()

    detail: dict[str, Any] = {
        "version": installation.version,
        "trust_level": listing.trust_level,
        "consent_required": needs_consent,
        "status": final_status,
        "granted_permissions": granted,
        "project_id": (str(installation.project_id) if installation.project_id else None),
        # task_prod12_mkt_01: el informe del gate de análisis (o su skip honesto)
        # viaja en el mismo audit row del install.
        "gates": gates,
        # ADR 0100: qué materializó (o por qué se difirió).
        "materialization": materialize_summary,
    }
    session.add(
        MarketplaceAuditEntry(
            tenant_id=installation.tenant_id,
            actor=actor,
            action=audit_action.value,
            listing_id=listing.id,
            installation_id=installation.id,
            detail=detail,
        )
    )
    await session.flush()
    _log.info(
        "marketplace.install.finalized",
        installation_id=str(installation.id),
        listing_id=str(listing.id),
        status=final_status,
        consent_required=needs_consent,
    )
    return detail
