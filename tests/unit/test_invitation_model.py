"""Unit tests for the invitation ORM + token helpers (ADR 0134, opción C).

In-process, no database. Pin the column shape, the tenancy decision
(``tenant_id`` present — la invitación otorga acceso a UN tenant), the
single-use + expiry lifecycle, and the mint/hash/verify helpers in
:mod:`api_server.auth.invitations`.

Lo que de verdad importa aquí y por qué: el token de invitación es una
credencial de alta que llega en una petición **no autenticada**, igual que
``X-API-Token`` y el bearer de SCIM. Se le exige por tanto lo mismo (CLAUDE.md:
ningún secreto en claro en la BD): que el valor crudo NO sea recuperable de lo
que se persiste (solo su SHA-256), y que la comparación sea de tiempo constante.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from api_server.auth.invitations import (
    DEFAULT_INVITATION_TTL_HOURS,
    INVITATION_TOKEN_PREFIX_MARKER,
    INVITATION_TOKEN_SECRET_BYTES,
    GeneratedInvitationToken,
    generate_invitation_token,
    hash_invitation_token,
    invitation_prefix_of,
    verify_invitation_token,
)
from api_server.db.invitation import UserInvitation
from sqlalchemy import UniqueConstraint

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Table shape + columns
# ---------------------------------------------------------------------------
def test_table_name_and_columns() -> None:
    assert UserInvitation.__tablename__ == "user_invitations"
    cols = {c.name for c in UserInvitation.__table__.columns}
    assert {
        "id",
        "tenant_id",
        "email",
        "token_hash",
        "token_prefix",
        "role",
        "expires_at",
        "redeemed_at",
        "redeemed_by_user_id",
        "revoked_at",
        "created_by",
        "created_at",
        "updated_at",
    } <= cols


def test_invitation_is_tenant_owned() -> None:
    """CLAUDE.md principio 1: la invitación vive en un tenant (RLS la aísla).

    Y no es solo higiene de multi-tenancy: el tenant es lo que el canje
    convierte en la membresía que da acceso. Sin él, el invitado aterrizaría en
    `no_access` y la invitación no serviría para nada.
    """
    assert UserInvitation.__table__.columns["tenant_id"].nullable is False


def test_token_hash_is_unique() -> None:
    """El digest identifica la invitación en una petición NO autenticada."""
    uniques = {
        c.name for c in UserInvitation.__table__.constraints if isinstance(c, UniqueConstraint)
    }
    assert "uq_user_invitation_token_hash" in uniques


def test_no_plaintext_token_column() -> None:
    """El token crudo no tiene casa en la tabla — solo su hash."""
    cols = {c.name for c in UserInvitation.__table__.columns}
    assert "token" not in cols
    assert "token_plaintext" not in cols
    assert "secret" not in cols
    assert "token_hash" in cols


def test_required_columns_are_not_nullable() -> None:
    for col_name in ("email", "token_hash", "token_prefix", "role", "expires_at"):
        column = UserInvitation.__table__.columns[col_name]
        assert column.nullable is False, f"{col_name} debería ser NOT NULL"


def test_lifecycle_columns_nullable() -> None:
    for col_name in ("redeemed_at", "redeemed_by_user_id", "revoked_at", "created_by"):
        assert UserInvitation.__table__.columns[col_name].nullable is True


def test_pending_partial_index_exists() -> None:
    idx = {i.name for i in UserInvitation.__table__.indexes}
    assert "ix_user_invitations_tenant_pending" in idx


def test_user_fks_set_null_on_user_delete() -> None:
    """La invitación sobrevive al admin que la emitió (rastro de auditoría)."""
    for col_name in ("created_by", "redeemed_by_user_id"):
        fks = list(UserInvitation.__table__.columns[col_name].foreign_keys)
        assert len(fks) == 1, col_name
        assert fks[0].ondelete == "SET NULL", col_name


# ---------------------------------------------------------------------------
# Lifecycle: is_redeemable() = un solo uso + caducidad + revocación
# ---------------------------------------------------------------------------
_NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _invitation(
    *,
    expires_at: datetime | None = None,
    redeemed_at: datetime | None = None,
    revoked_at: datetime | None = None,
) -> UserInvitation:
    return UserInvitation(
        tenant_id=uuid4(),
        email="invitee@example.com",
        token_hash="a" * 64,
        token_prefix="aainv_cafef00d",
        role="member",
        expires_at=expires_at if expires_at is not None else _NOW + timedelta(days=7),
        redeemed_at=redeemed_at,
        revoked_at=revoked_at,
    )


def test_is_redeemable_when_fresh() -> None:
    assert _invitation().is_redeemable(now=_NOW) is True


def test_is_redeemable_false_once_redeemed() -> None:
    """UN SOLO USO: canjeada = inservible (ADR 0134)."""
    used = _invitation(redeemed_at=_NOW - timedelta(seconds=1))
    assert used.is_redeemable(now=_NOW) is False


def test_is_redeemable_false_when_revoked() -> None:
    revoked = _invitation(revoked_at=_NOW - timedelta(seconds=1))
    assert revoked.is_redeemable(now=_NOW) is False


def test_is_redeemable_false_when_expired() -> None:
    expired = _invitation(expires_at=_NOW - timedelta(seconds=1))
    assert expired.is_redeemable(now=_NOW) is False


def test_is_redeemable_false_at_exact_expiry_instant() -> None:
    """expires_at <= now es caducada (frontera inclusiva, como ApiToken)."""
    assert _invitation(expires_at=_NOW).is_redeemable(now=_NOW) is False


def test_is_redeemable_true_just_before_expiry() -> None:
    assert _invitation(expires_at=_NOW + timedelta(seconds=1)).is_redeemable(now=_NOW) is True


# ---------------------------------------------------------------------------
# Token helpers: mint / hash / verify (tiempo constante)
# ---------------------------------------------------------------------------
def test_generate_returns_three_consistent_forms() -> None:
    minted = generate_invitation_token()
    assert isinstance(minted, GeneratedInvitationToken)
    assert minted.token_hash == hash_invitation_token(minted.token)
    assert minted.token.startswith(minted.prefix)
    assert invitation_prefix_of(minted.token) == minted.prefix


def test_generated_prefix_carries_marker_and_fits_the_column() -> None:
    minted = generate_invitation_token()
    assert minted.prefix.startswith(f"{INVITATION_TOKEN_PREFIX_MARKER}_")
    width = UserInvitation.__table__.columns["token_prefix"].type.length
    assert len(minted.prefix) <= width


def test_generated_tokens_are_unique() -> None:
    tokens = {generate_invitation_token().token for _ in range(50)}
    assert len(tokens) == 50


def test_hash_is_sha256_hex_and_not_the_token() -> None:
    minted = generate_invitation_token()
    assert len(minted.token_hash) == 64
    assert all(c in "0123456789abcdef" for c in minted.token_hash)
    assert minted.token != minted.token_hash
    assert minted.token not in minted.token_hash
    # …y cabe en la columna que lo persiste.
    assert len(minted.token_hash) <= UserInvitation.__table__.columns["token_hash"].type.length


def test_hash_is_deterministic() -> None:
    assert hash_invitation_token("hello-invite") == hash_invitation_token("hello-invite")


def test_verify_accepts_right_token() -> None:
    minted = generate_invitation_token()
    assert verify_invitation_token(minted.token, minted.token_hash) is True


def test_verify_rejects_wrong_token() -> None:
    minted = generate_invitation_token()
    other = generate_invitation_token()
    assert verify_invitation_token(other.token, minted.token_hash) is False
    assert verify_invitation_token("not-the-token", minted.token_hash) is False
    assert verify_invitation_token("", minted.token_hash) is False


def test_raw_token_is_not_recoverable_from_the_persisted_row() -> None:
    """El flujo at-rest: se persiste SOLO el hash; el crudo se enseña una vez."""
    minted = generate_invitation_token()
    persisted = UserInvitation(
        tenant_id=uuid4(),
        email="invitee@example.com",
        token_hash=minted.token_hash,
        token_prefix=minted.prefix,
        role="member",
        expires_at=_NOW + timedelta(days=7),
    )
    assert verify_invitation_token(minted.token, persisted.token_hash) is True
    persisted_values = {
        str(persisted.token_hash),
        str(persisted.token_prefix),
        str(persisted.email),
    }
    assert minted.token not in persisted_values
    assert not hasattr(persisted, "token")


def test_secret_entropy_is_high() -> None:
    assert INVITATION_TOKEN_SECRET_BYTES >= 32


def test_default_ttl_is_bounded_and_finite() -> None:
    """Una invitación SIEMPRE caduca (ADR 0134). Un default infinito o de meses
    convertiría el token en una credencial permanente por descuido."""
    assert 0 < DEFAULT_INVITATION_TTL_HOURS <= 24 * 30


def test_invitation_prefix_of_falls_back_for_malformed_token() -> None:
    assert invitation_prefix_of("garbage") == "garbage"
    assert invitation_prefix_of("only_two") == "only_two"


# ---------------------------------------------------------------------------
# Schema de emisión: el rol acaba siendo una membresía real
# ---------------------------------------------------------------------------
def test_create_request_rejects_an_unknown_role() -> None:
    """Un rol inventado solo daría la cara AL CANJEAR, creando una membresía con
    un rol que el RBAC no conoce: un usuario dentro del tenant, sin permisos y
    sin explicación. Se corta en la emisión."""
    from api_server.schemas.invitations import InvitationCreateRequest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        InvitationCreateRequest(email="a@example.com", tenant_id=uuid4(), role="wizard-supremo")


def test_create_request_accepts_every_membership_role() -> None:
    from api_server.db.models import UserRole
    from api_server.schemas.invitations import InvitationCreateRequest

    seen = 0
    for role in UserRole:
        payload = InvitationCreateRequest(email="a@example.com", tenant_id=uuid4(), role=role.value)
        assert payload.role == role.value
        seen += 1
    # La guarda no puede pasar en vacío si mañana el enum se queda sin miembros.
    assert seen >= 4, f"el enum de roles se ha quedado en {seen} miembros"


def test_create_request_defaults_are_bounded() -> None:
    """Sin `expires_in_hours` la invitación toma el default del backend, y el
    schema acota el máximo: un admin no puede pedir una vigencia infinita."""
    from api_server.schemas.invitations import InvitationCreateRequest
    from pydantic import ValidationError

    default = InvitationCreateRequest(email="a@example.com", tenant_id=uuid4())
    assert default.expires_in_hours == DEFAULT_INVITATION_TTL_HOURS

    with pytest.raises(ValidationError):
        InvitationCreateRequest(email="a@example.com", tenant_id=uuid4(), expires_in_hours=10_000)
    with pytest.raises(ValidationError):
        InvitationCreateRequest(email="a@example.com", tenant_id=uuid4(), expires_in_hours=0)


# ---------------------------------------------------------------------------
# Estado derivado que ve el admin
# ---------------------------------------------------------------------------
def test_status_is_derived_and_redeemed_wins_over_the_rest() -> None:
    """`status` se calcula, no se guarda: una columna habría que mantenerla al
    día con el paso del tiempo (nadie escribe una fila cuando caduca) y acabaría
    mintiendo. El orden describe lo que de HECHO le pasó a la invitación."""
    from api_server.routers.invitations import _status_of

    assert _status_of(_invitation(), now=_NOW) == "pending"
    assert _status_of(_invitation(expires_at=_NOW - timedelta(seconds=1)), now=_NOW) == "expired"
    assert _status_of(_invitation(revoked_at=_NOW), now=_NOW) == "revoked"
    assert _status_of(_invitation(redeemed_at=_NOW), now=_NOW) == "redeemed"
    # Canjeada Y caducada sigue siendo «canjeada»: es lo que pasó de verdad.
    both = _invitation(redeemed_at=_NOW - timedelta(days=1), expires_at=_NOW - timedelta(hours=1))
    assert _status_of(both, now=_NOW) == "redeemed"
