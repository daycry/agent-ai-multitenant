"""El re-consentimiento del delta al actualizar una instalación — ADR 0142 D7.

Actualizar no es reinstalar: la instalación ya tiene permisos concedidos, y lo
único sobre lo que hay que volver a preguntar es **lo que la versión nueva pide
de más**. Ese delta lo calcula ``listing_versions.permission_diff``; lo que vive
aquí es qué se hace con él.

Por qué está fuera del router: ``routers/marketplace.py`` pasa de 1.700 líneas y
el plan `marketplace-v2-despliegue` lo dice con todas las letras («todo endpoint
nuevo va a `routers/marketplace_deployments.py`, no ahí dentro»). Al meter esta
lógica en el endpoint, la función llegó a 17 ramas y 61 sentencias y `ruff` la
paró — que es exactamente para lo que está el límite.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.marketplace import (
    InstallationStatus,
    MarketplaceAuditAction,
    MarketplaceAuditEntry,
    MarketplaceListing,
)
from api_server.marketplace.consent import ConsentError, apply_decisions


def pending_consent_types(delta_payload: dict[str, Any]) -> list[str]:
    """Los tipos de permiso sobre los que hay que preguntar.

    Solo `added` y `changed`: **retirar** un permiso no amplía nada, así que no
    necesita una decisión humana. Preguntar por ello enseñaría a la gente a
    aceptar sin leer, que es la forma barata de perder el mecanismo entero.
    """
    return sorted(
        {
            *(p["type"] for p in delta_payload["added"]),
            *(c["type"] for c in delta_payload["changed"]),
        }
    )


def apply_update_consent(
    session: AsyncSession,
    *,
    installation: Any,
    target_listing: MarketplaceListing,
    delta_payload: dict[str, Any],
    decisions: dict[str, Any],
    tenant_id: UUID,
    actor: str,
    from_version: str,
    to_version: str,
) -> None:
    """Exige y aplica el consentimiento del delta. Muta ``installation``.

    Levanta 409 si falta alguna decisión, 422 si las que llegan son inválidas.
    No hace commit: el llamante sigue dentro de su transacción.
    """
    pending = pending_consent_types(delta_payload)
    if pending and not set(pending) <= set(decisions):
        # 409 con el delta en el cuerpo: la UI lo pinta y vuelve a llamar con
        # las decisiones. No es un 422 porque la petición está bien formada —
        # lo que falta es el consentimiento, que es estado, no sintaxis.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "consent_required",
                "message": (
                    "esta versión pide permisos que no están concedidos; decide sobre "
                    "ellos y vuelve a llamar con `consent`"
                ),
                "permission_delta": delta_payload,
                "pending": pending,
            },
        )

    if not decisions:
        return

    try:
        outcome = apply_decisions(
            trust_level=target_listing.trust_level,
            requested_permissions=list(target_listing.requested_permissions or []),
            existing_granted=list(installation.granted_permissions or []),
            existing_denied=list(installation.denied_permissions or []),
            decisions=decisions,
        )
    except (ConsentError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    installation.granted_permissions = outcome.granted
    installation.denied_permissions = outcome.denied
    if not outcome.enable or outcome.any_denied:
        # Un permiso nuevo DENEGADO deja la instalación deshabilitada.
        #
        # El `or outcome.any_denied` es una desviación deliberada del flujo de
        # consentimiento del alta, y la razón importa: allí un listing
        # `verified` no requiere consentimiento, así que `outcome.enable` es
        # SIEMPRE `True` y una denegación no cambiaría nada. Pero aquí sí se ha
        # preguntado —D7 pregunta por el delta con independencia del nivel de
        # confianza— y el operador ha dicho que no. Ignorar esa respuesta
        # convertiría la pregunta en teatro, que es peor que la incoherencia de
        # tratar distinto a un `verified` según por dónde entre.
        installation.status = InstallationStatus.DISABLED.value

    session.add(
        MarketplaceAuditEntry(
            tenant_id=tenant_id,
            actor=actor,
            action=MarketplaceAuditAction.CONSENT.value,
            listing_id=target_listing.id,
            installation_id=installation.id,
            detail={
                "event": "update_delta_consent",
                "from_version": from_version,
                "to_version": to_version,
                "decisions": decisions,
                "permission_delta": delta_payload,
            },
        )
    )
