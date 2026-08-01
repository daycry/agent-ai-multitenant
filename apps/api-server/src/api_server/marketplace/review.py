"""La revisión de la publicación: `draft → pending_review → published | rejected`.

ADR 0142, decisión D6 — «nada entra al catálogo sin ojos». Este módulo es la
única puerta por la que `marketplace_listings.review_status` cambia de valor, y
cada transición:

* **comprueba la arista** contra :data:`REVIEW_TRANSITIONS` (vocabulario cerrado:
  publicar desde `draft` no es «un atajo», es la vía que D6 cierra);
* **exige actor**, que es obligatorio en la firma, no un `None` por defecto;
* **escribe auditoría**: una fila `marketplace_audit_entries` para que el tenant
  autor vea el veredicto en su propio rastro, y una fila `audit_log` de
  plataforma —cuyo `tenant_id` SÍ es nullable— para que un listing GLOBAL
  (`tenant_id IS NULL`, el catálogo oficial) también deje rastro. Sin la
  segunda, revisar el catálogo curado sería la única acción sensible del sistema
  que no se audita.

Las funciones son **síncronas** a propósito: `AsyncSession.add()` lo es, no hay
`await` que dar, y una firma `async` que nunca espera nada obliga a montar un
bucle de eventos para probar una máquina de estados. El caller es dueño de la
transacción y del `flush`.

## La otra mitad: la visibilidad

Cambiar el estado no sirve de nada si el catálogo sigue enseñando lo que está en
la cola. :func:`catalog_visibility_clause` es el filtro que los `GET
/marketplace/listings*` aplican, y :func:`is_visible_in_catalog` su gemelo puro
para decidir sobre una fila ya cargada. La regla es una sola frase: **se ve lo
publicado, y además lo propio**, porque el autor necesita leer el motivo de su
rechazo. Un `pending_review` ajeno no existe.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import ColumnElement, or_

from api_server.db.marketplace import (
    ListingReviewStatus,
    MarketplaceAuditAction,
    MarketplaceAuditEntry,
    MarketplaceListing,
    MarketplaceTrustLevel,
)
from api_server.db.models import AuditLog

#: Las aristas legales del grafo. Todo lo que no esté aquí está prohibido.
#:
#: `published → pending_review` existe porque una VERSIÓN NUEVA de algo ya
#: aprobado vuelve a la cola: heredar la aprobación de la versión anterior
#: convertiría el primer listing aprobado en un pase permanente.
#: `rejected → pending_review` existe porque un rechazo se corrige y se reenvía.
REVIEW_TRANSITIONS: dict[str, frozenset[str]] = {
    ListingReviewStatus.DRAFT.value: frozenset({ListingReviewStatus.PENDING_REVIEW.value}),
    ListingReviewStatus.PENDING_REVIEW.value: frozenset(
        {ListingReviewStatus.PUBLISHED.value, ListingReviewStatus.REJECTED.value}
    ),
    ListingReviewStatus.PUBLISHED.value: frozenset({ListingReviewStatus.PENDING_REVIEW.value}),
    ListingReviewStatus.REJECTED.value: frozenset({ListingReviewStatus.PENDING_REVIEW.value}),
}

#: `action` de las filas de `audit_log` que deja este módulo.
PLATFORM_AUDIT_ACTION = "marketplace_review"
PLATFORM_AUDIT_RESOURCE = "marketplace_listing"


class ReviewTransitionError(ValueError):
    """La transición pedida no existe, o le falta lo que exige (el motivo).

    Subclasea :class:`ValueError` para caer en los `except ValueError` que ya
    mapean a 422; el router de revisión lo traduce explícitamente.
    """


class _Adder(Protocol):
    """Lo único que este módulo necesita de una sesión."""

    def add(self, obj: Any, /) -> None: ...  # pragma: no cover - protocolo


def can_transition(current: str, target: str) -> bool:
    """¿Es legal ir de `current` a `target`?

    Un `current` que no esté en el vocabulario devuelve ``False`` — un estado
    desconocido no habilita nada, que es lo contrario de lo que haría un
    ``.get(current, ALL)`` descuidado.
    """
    return target in REVIEW_TRANSITIONS.get(current, frozenset())


def assert_transition(current: str, target: str) -> None:
    if not can_transition(current, target):
        allowed = sorted(REVIEW_TRANSITIONS.get(current, frozenset()))
        raise ReviewTransitionError(
            f"no se puede pasar de {current!r} a {target!r}"
            + (f"; desde {current!r} solo se puede ir a: {', '.join(allowed)}" if allowed else "")
        )


# ---------------------------------------------------------------------------
# Auditoría
# ---------------------------------------------------------------------------
def _audit(
    session: _Adder,
    *,
    listing: MarketplaceListing,
    action: MarketplaceAuditAction,
    actor: str,
    actor_user_id: UUID | None,
    detail: dict[str, Any],
) -> None:
    """Las dos filas: la del tenant (si lo hay) y la de plataforma (siempre)."""
    if listing.tenant_id is not None:
        session.add(
            MarketplaceAuditEntry(
                tenant_id=listing.tenant_id,
                actor=actor,
                action=action.value,
                listing_id=listing.id,
                installation_id=None,
                detail=detail,
            )
        )
    session.add(
        AuditLog(
            tenant_id=listing.tenant_id,
            user_id=actor_user_id,
            action=PLATFORM_AUDIT_ACTION,
            resource_type=PLATFORM_AUDIT_RESOURCE,
            resource_id=listing.id,
            changes={"transition": action.value, "actor": actor, **detail},
        )
    )


def _base_detail(listing: MarketplaceListing, target: str) -> dict[str, Any]:
    return {
        "from": listing.review_status,
        "to": target,
        "name": listing.name,
        "version": listing.version,
        "kind": listing.kind,
    }


# ---------------------------------------------------------------------------
# Las transiciones
# ---------------------------------------------------------------------------
def submit_for_review(
    session: _Adder,
    *,
    listing: MarketplaceListing,
    actor: str,
    actor_user_id: UUID | None = None,
) -> None:
    """Manda el listing a la cola del admin y **borra el veredicto anterior**.

    Lo segundo no es cosmético: si el `rejection_reason` sobreviviera al
    reenvío, la ficha enseñaría el motivo de un rechazo ya corregido y el
    siguiente revisor leería una acusación caducada.
    """
    target = ListingReviewStatus.PENDING_REVIEW.value
    detail = _base_detail(listing, target)
    assert_transition(listing.review_status, target)

    listing.review_status = target
    listing.reviewed_by = None
    listing.reviewed_at = None
    listing.rejection_reason = None

    _audit(
        session,
        listing=listing,
        action=MarketplaceAuditAction.SUBMIT_REVIEW,
        actor=actor,
        actor_user_id=actor_user_id,
        detail=detail,
    )


def approve_listing(
    session: _Adder,
    *,
    listing: MarketplaceListing,
    actor: str,
    actor_user_id: UUID | None = None,
    promote: bool = False,
) -> None:
    """Aprueba: `pending_review → published`, con sello de quién y cuándo.

    Aprobar **no** promociona. `promote=True` es el atajo del admin que decide
    las dos cosas de una vez; sin él, un listing aprobado sigue siendo
    `community` y arrastra los guardrails que le tocan (ADR 0032).
    """
    target = ListingReviewStatus.PUBLISHED.value
    detail = _base_detail(listing, target)
    assert_transition(listing.review_status, target)

    listing.review_status = target
    listing.reviewed_by = actor_user_id
    listing.reviewed_at = datetime.now(UTC)
    listing.rejection_reason = None
    if promote:
        listing.trust_level = MarketplaceTrustLevel.VERIFIED.value
        detail["trust_level"] = listing.trust_level

    _audit(
        session,
        listing=listing,
        action=MarketplaceAuditAction.APPROVE,
        actor=actor,
        actor_user_id=actor_user_id,
        detail=detail,
    )


def reject_listing(
    session: _Adder,
    *,
    listing: MarketplaceListing,
    actor: str,
    reason: str,
    actor_user_id: UUID | None = None,
) -> None:
    """Rechaza con motivo ESCRITO. Sin motivo no hay rechazo.

    Un rechazo mudo es indistinguible de un borrado y no se puede recurrir: el
    autor no sabe qué arreglar. La guarda es de verdad (`.strip()`), no un
    `if reason is None`, porque `"   "` es exactamente el motivo que pondría
    quien no quiere escribir ninguno.
    """
    if not reason or not reason.strip():
        raise ReviewTransitionError(
            "un rechazo necesita un motivo escrito: el autor tiene que saber qué corregir"
        )
    target = ListingReviewStatus.REJECTED.value
    detail = _base_detail(listing, target)
    assert_transition(listing.review_status, target)

    listing.review_status = target
    listing.reviewed_by = actor_user_id
    listing.reviewed_at = datetime.now(UTC)
    listing.rejection_reason = reason.strip()
    detail["reason"] = listing.rejection_reason

    _audit(
        session,
        listing=listing,
        action=MarketplaceAuditAction.REJECT,
        actor=actor,
        actor_user_id=actor_user_id,
        detail=detail,
    )


def promote_listing(
    session: _Adder,
    *,
    listing: MarketplaceListing,
    actor: str,
    actor_user_id: UUID | None = None,
    trust_level: MarketplaceTrustLevel | str = MarketplaceTrustLevel.VERIFIED,
) -> None:
    """Cambia el `trust_level` de un listing YA publicado.

    Reversible en los dos sentidos (un `verified` que se estropea vuelve a
    `community`), porque la alternativa —una promoción irreversible— obliga a
    despublicar para degradar, y despublicar rompe las instalaciones vivas.

    Exige `published`: promocionar algo que sigue en la cola daría por buena una
    revisión que no ha ocurrido.
    """
    if listing.review_status != ListingReviewStatus.PUBLISHED.value:
        raise ReviewTransitionError(
            f"solo se promociona lo publicado; este listing está {listing.review_status!r}"
        )
    level = MarketplaceTrustLevel(trust_level).value
    previous = listing.trust_level
    listing.trust_level = level

    _audit(
        session,
        listing=listing,
        action=MarketplaceAuditAction.PROMOTE,
        actor=actor,
        actor_user_id=actor_user_id,
        detail={
            "from_trust_level": previous,
            "to_trust_level": level,
            "name": listing.name,
            "version": listing.version,
        },
    )


# ---------------------------------------------------------------------------
# Visibilidad del catálogo
# ---------------------------------------------------------------------------
def catalog_visibility_clause(viewer_tenant_id: UUID | None) -> ColumnElement[bool]:
    """El filtro `WHERE` del catálogo: lo publicado, más lo propio.

    Se aplica **encima** de la RLS, no en su lugar: la RLS ya decide qué filas
    existen para esta sesión (globales + propias + compartidas); esto quita de
    ahí lo que aún no ha pasado revisión.

    Con `viewer_tenant_id=None` (una sesión sin tenant) queda solo lo publicado,
    que es lo correcto: sin tenant no hay «lo propio».
    """
    published = MarketplaceListing.review_status == ListingReviewStatus.PUBLISHED.value
    if viewer_tenant_id is None:
        return published
    return or_(published, MarketplaceListing.tenant_id == viewer_tenant_id)


def is_visible_in_catalog(listing: MarketplaceListing, *, viewer_tenant_id: UUID | None) -> bool:
    """El gemelo puro de :func:`catalog_visibility_clause`, para una fila cargada.

    Los dos tienen que decir lo mismo; el test de integración del flujo lo
    comprueba contra la BD para que no se separen.
    """
    if listing.review_status == ListingReviewStatus.PUBLISHED.value:
        return True
    if viewer_tenant_id is None or listing.tenant_id is None:
        return False
    return bool(listing.tenant_id == viewer_tenant_id)


__all__ = [
    "PLATFORM_AUDIT_ACTION",
    "PLATFORM_AUDIT_RESOURCE",
    "REVIEW_TRANSITIONS",
    "ReviewTransitionError",
    "approve_listing",
    "assert_transition",
    "can_transition",
    "catalog_visibility_clause",
    "is_visible_in_catalog",
    "promote_listing",
    "reject_listing",
    "submit_for_review",
]
