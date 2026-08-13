"""La máquina de estados de revisión de un listing — `task_mkt2_09` (ADR 0142, D6).

`draft → pending_review → published | rejected`, más la promoción
`published → verified` (que NO es un estado de revisión sino un cambio de
`trust_level`, y por eso vive aparte).

Este fichero prueba la parte **pura**: qué transiciones existen, cuáles no, qué
se exige en cada una y qué escribe cada una en la auditoría. La sesión se
sustituye por un doble que solo recuerda lo que le añaden — no hace falta una
base de datos para afirmar que un rechazo sin motivo no se acepta, y meterla
convertiría esta guarda en algo que tarda un minuto en correr.

El flujo entero contra PostgreSQL (RLS, visibilidad del catálogo, RBAC del
system admin) está en `tests/integration/test_marketplace_review_flow.py`.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from api_server.db.marketplace import (
    ListingReviewStatus,
    MarketplaceAuditAction,
    MarketplaceAuditEntry,
    MarketplaceListing,
    MarketplaceTrustLevel,
)
from api_server.marketplace.review import (
    REVIEW_TRANSITIONS,
    ReviewTransitionError,
    approve_listing,
    can_transition,
    is_visible_in_catalog,
    promote_listing,
    reject_listing,
    submit_for_review,
)


class _SessionSpy:
    """Lo mínimo que las transiciones usan de una `AsyncSession`: `add`."""

    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    @property
    def audit(self) -> list[MarketplaceAuditEntry]:
        return [o for o in self.added if isinstance(o, MarketplaceAuditEntry)]


def _listing(
    *,
    review_status: str = ListingReviewStatus.DRAFT.value,
    trust_level: str = MarketplaceTrustLevel.COMMUNITY.value,
    tenant_id: Any = None,
) -> MarketplaceListing:
    return MarketplaceListing(
        id=uuid4(),
        source_id=uuid4(),
        tenant_id=tenant_id if tenant_id is not None else uuid4(),
        kind="tool",
        name="acme-tool",
        version="1.0.0",
        trust_level=trust_level,
        review_status=review_status,
        manifest={},
        requested_permissions=[],
    )


# ---------------------------------------------------------------------------
# El grafo de transiciones
# ---------------------------------------------------------------------------
def test_transition_graph_only_allows_the_four_documented_edges() -> None:
    """El vocabulario es cerrado: cualquier arista de más es una vía de escape."""
    assert {
        ListingReviewStatus.DRAFT.value: frozenset({ListingReviewStatus.PENDING_REVIEW.value}),
        ListingReviewStatus.PENDING_REVIEW.value: frozenset(
            {ListingReviewStatus.PUBLISHED.value, ListingReviewStatus.REJECTED.value}
        ),
        # Re-publicar algo ya publicado lo devuelve a la cola: una versión nueva
        # de algo aprobado NO hereda la aprobación de la anterior (D6).
        ListingReviewStatus.PUBLISHED.value: frozenset({ListingReviewStatus.PENDING_REVIEW.value}),
        # Un rechazo no es una condena: se corrige y se vuelve a mandar.
        ListingReviewStatus.REJECTED.value: frozenset({ListingReviewStatus.PENDING_REVIEW.value}),
    } == REVIEW_TRANSITIONS


@pytest.mark.parametrize(
    ("current", "target"),
    [
        # Publicar saltándose la cola: el agujero que D6 cierra.
        (ListingReviewStatus.DRAFT.value, ListingReviewStatus.PUBLISHED.value),
        (ListingReviewStatus.REJECTED.value, ListingReviewStatus.PUBLISHED.value),
        # Rechazar algo que nadie mandó a revisar.
        (ListingReviewStatus.DRAFT.value, ListingReviewStatus.REJECTED.value),
        (ListingReviewStatus.PUBLISHED.value, ListingReviewStatus.REJECTED.value),
        # Y el bucle sobre uno mismo, que no es una transición.
        (ListingReviewStatus.PUBLISHED.value, ListingReviewStatus.PUBLISHED.value),
    ],
)
def test_forbidden_transitions_are_forbidden(current: str, target: str) -> None:
    assert can_transition(current, target) is False


def test_unknown_status_is_not_a_free_pass() -> None:
    """Un estado que no está en el vocabulario no habilita nada."""
    assert can_transition("whatever", ListingReviewStatus.PUBLISHED.value) is False


# ---------------------------------------------------------------------------
# submit_for_review
# ---------------------------------------------------------------------------
def test_submit_moves_to_pending_and_clears_the_previous_verdict() -> None:
    """Re-enviar tras un rechazo borra el veredicto viejo.

    Si el `rejection_reason` sobreviviera, la ficha enseñaría el motivo de un
    rechazo que ya no aplica — y el revisor de turno leería una acusación
    caducada.
    """
    session = _SessionSpy()
    listing = _listing(review_status=ListingReviewStatus.REJECTED.value)
    listing.rejection_reason = "faltaba el manifiesto"
    listing.reviewed_by = uuid4()

    submit_for_review(session, listing=listing, actor="user:x")

    assert listing.review_status == ListingReviewStatus.PENDING_REVIEW.value
    assert listing.rejection_reason is None
    assert listing.reviewed_by is None
    assert listing.reviewed_at is None
    assert [e.action for e in session.audit] == [MarketplaceAuditAction.SUBMIT_REVIEW.value]


def test_submit_from_published_is_allowed_and_hides_it_again() -> None:
    session = _SessionSpy()
    listing = _listing(review_status=ListingReviewStatus.PUBLISHED.value)

    submit_for_review(session, listing=listing, actor="user:x")

    assert listing.review_status == ListingReviewStatus.PENDING_REVIEW.value


# ---------------------------------------------------------------------------
# approve_listing
# ---------------------------------------------------------------------------
def test_approve_publishes_and_stamps_the_reviewer() -> None:
    session = _SessionSpy()
    listing = _listing(review_status=ListingReviewStatus.PENDING_REVIEW.value)
    reviewer = uuid4()

    approve_listing(session, listing=listing, actor="user:admin", actor_user_id=reviewer)

    assert listing.review_status == ListingReviewStatus.PUBLISHED.value
    assert listing.reviewed_by == reviewer
    assert listing.reviewed_at is not None
    assert listing.rejection_reason is None
    # Aprobar NO promociona: la confianza es una decisión aparte.
    assert listing.trust_level == MarketplaceTrustLevel.COMMUNITY.value
    assert [e.action for e in session.audit] == [MarketplaceAuditAction.APPROVE.value]


def test_approve_can_promote_in_the_same_stroke() -> None:
    session = _SessionSpy()
    listing = _listing(review_status=ListingReviewStatus.PENDING_REVIEW.value)

    approve_listing(session, listing=listing, actor="user:admin", promote=True)

    assert listing.review_status == ListingReviewStatus.PUBLISHED.value
    assert listing.trust_level == MarketplaceTrustLevel.VERIFIED.value


def test_approve_from_draft_is_rejected() -> None:
    session = _SessionSpy()
    listing = _listing(review_status=ListingReviewStatus.DRAFT.value)

    with pytest.raises(ReviewTransitionError):
        approve_listing(session, listing=listing, actor="user:admin")

    assert listing.review_status == ListingReviewStatus.DRAFT.value
    assert session.audit == []


# ---------------------------------------------------------------------------
# reject_listing — el motivo NO es opcional
# ---------------------------------------------------------------------------
def test_reject_records_the_reason() -> None:
    session = _SessionSpy()
    listing = _listing(review_status=ListingReviewStatus.PENDING_REVIEW.value)

    reject_listing(session, listing=listing, actor="user:admin", reason="pide acceso a toda la red")

    assert listing.review_status == ListingReviewStatus.REJECTED.value
    assert listing.rejection_reason == "pide acceso a toda la red"
    assert listing.reviewed_at is not None
    entry = session.audit[0]
    assert entry.action == MarketplaceAuditAction.REJECT.value
    assert entry.detail["reason"] == "pide acceso a toda la red"


@pytest.mark.parametrize("reason", ["", "   ", "\n\t "])
def test_reject_without_a_written_reason_is_refused(reason: str) -> None:
    """Un rechazo mudo es indistinguible de un borrado y no se puede recurrir."""
    session = _SessionSpy()
    listing = _listing(review_status=ListingReviewStatus.PENDING_REVIEW.value)

    with pytest.raises(ReviewTransitionError):
        reject_listing(session, listing=listing, actor="user:admin", reason=reason)

    assert listing.review_status == ListingReviewStatus.PENDING_REVIEW.value
    assert session.audit == []


# ---------------------------------------------------------------------------
# promote_listing — la promoción a verified
# ---------------------------------------------------------------------------
def test_promote_requires_published() -> None:
    session = _SessionSpy()
    listing = _listing(review_status=ListingReviewStatus.PENDING_REVIEW.value)

    with pytest.raises(ReviewTransitionError):
        promote_listing(session, listing=listing, actor="user:admin")

    assert listing.trust_level == MarketplaceTrustLevel.COMMUNITY.value


def test_promote_sets_verified_and_audits() -> None:
    session = _SessionSpy()
    listing = _listing(review_status=ListingReviewStatus.PUBLISHED.value)

    promote_listing(session, listing=listing, actor="user:admin")

    assert listing.trust_level == MarketplaceTrustLevel.VERIFIED.value
    assert [e.action for e in session.audit] == [MarketplaceAuditAction.PROMOTE.value]


def test_promote_can_demote_back_to_community() -> None:
    """La promoción es reversible: un `verified` que se estropea vuelve a community."""
    session = _SessionSpy()
    listing = _listing(
        review_status=ListingReviewStatus.PUBLISHED.value,
        trust_level=MarketplaceTrustLevel.VERIFIED.value,
    )

    promote_listing(
        session,
        listing=listing,
        actor="user:admin",
        trust_level=MarketplaceTrustLevel.COMMUNITY,
    )

    assert listing.trust_level == MarketplaceTrustLevel.COMMUNITY.value


# ---------------------------------------------------------------------------
# Visibilidad del catálogo — el negativo que importa
# ---------------------------------------------------------------------------
def test_pending_review_is_invisible_to_everyone_but_its_author() -> None:
    author = uuid4()
    other = uuid4()
    listing = _listing(review_status=ListingReviewStatus.PENDING_REVIEW.value, tenant_id=author)

    assert is_visible_in_catalog(listing, viewer_tenant_id=author) is True
    assert is_visible_in_catalog(listing, viewer_tenant_id=other) is False
    # Y tampoco para quien no tiene tenant (una sesión de plataforma navegando
    # el catálogo público).
    assert is_visible_in_catalog(listing, viewer_tenant_id=None) is False


def test_published_is_visible_to_all() -> None:
    listing = _listing(review_status=ListingReviewStatus.PUBLISHED.value, tenant_id=uuid4())
    assert is_visible_in_catalog(listing, viewer_tenant_id=uuid4()) is True


def test_rejected_is_visible_to_its_author_so_they_can_read_the_reason() -> None:
    author = uuid4()
    listing = _listing(review_status=ListingReviewStatus.REJECTED.value, tenant_id=author)
    assert is_visible_in_catalog(listing, viewer_tenant_id=author) is True
    assert is_visible_in_catalog(listing, viewer_tenant_id=uuid4()) is False


def test_a_global_listing_still_needs_to_be_published() -> None:
    """El catálogo oficial no tiene bula: si no está `published`, no se ve.

    Un listing global (`tenant_id IS NULL`) no tiene autor-tenant que lo pueda
    ver «por ser suyo», así que su única vía de visibilidad es estar publicado.
    """
    listing = _listing(review_status=ListingReviewStatus.PENDING_REVIEW.value)
    listing.tenant_id = None
    assert is_visible_in_catalog(listing, viewer_tenant_id=uuid4()) is False

    listing.review_status = ListingReviewStatus.PUBLISHED.value
    assert is_visible_in_catalog(listing, viewer_tenant_id=uuid4()) is True
