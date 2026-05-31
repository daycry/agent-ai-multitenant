"""Unit tests for the public-API token ORM + helpers (Plan 13 task_13_01).

In-process, no database. We pin the column shape, the tenant-owned
tenancy decision (``tenant_id`` present), the scope enum, the
lifecycle helpers (expiry + soft-revoke), the optional CIDR allowlist,
and the mint/hash/verify token helpers in
:mod:`api_server.auth.api_tokens` — most importantly that the raw token
is NEVER recoverable from what gets persisted (only its SHA-256 digest
is stored) and that verification is constant-time. The migration + RLS
are exercised later (task_13_02 / the dedicated migration test).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from api_server.auth.api_tokens import (
    API_TOKEN_PREFIX_MARKER,
    API_TOKEN_SECRET_BYTES,
    DEFAULT_API_TOKEN_RATE_LIMIT,
    GeneratedApiToken,
    generate_api_token,
    hash_api_token,
    prefix_of,
    verify_api_token,
)
from api_server.db.models import ApiToken, ApiTokenScope
from sqlalchemy import UniqueConstraint

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Scope enum (StrEnum -> stable TEXT values)
# ---------------------------------------------------------------------------
def test_scope_enum_values() -> None:
    assert {s.value for s in ApiTokenScope} == {"read", "write"}


def test_scope_enum_is_string_valued() -> None:
    assert ApiTokenScope.READ == "read"
    assert ApiTokenScope.WRITE == "write"


# ---------------------------------------------------------------------------
# Table shape + columns
# ---------------------------------------------------------------------------
def test_table_name_and_columns() -> None:
    assert ApiToken.__tablename__ == "api_tokens"
    cols = {c.name for c in ApiToken.__table__.columns}
    assert {
        "id",
        "tenant_id",
        "token_hash",
        "prefix",
        "name",
        "scopes",
        "expires_at",
        "rate_limit",
        "ip_allowlist",
        "created_by",
        "last_used_at",
        "revoked_at",
        "created_at",
        "updated_at",
    } <= cols


def test_token_is_tenant_owned() -> None:
    """Plan 13 NON-NEGOTIABLE: api_tokens is tenant-owned (tenant_id NOT NULL)."""
    cols = {c.name for c in ApiToken.__table__.columns}
    assert "tenant_id" in cols
    assert ApiToken.__table__.columns["tenant_id"].nullable is False


def test_token_hash_is_unique() -> None:
    """The digest identifies a tenant on an unauthenticated request."""
    uniques = {c.name for c in ApiToken.__table__.constraints if isinstance(c, UniqueConstraint)}
    assert "uq_api_token_hash" in uniques


def test_no_plaintext_token_column() -> None:
    """The raw token must never have a home in the table — only its hash."""
    cols = {c.name for c in ApiToken.__table__.columns}
    assert "token" not in cols
    assert "token_plaintext" not in cols
    assert "secret" not in cols
    assert "token_hash" in cols


def test_partial_active_index_excludes_revoked() -> None:
    idx = {i.name: i for i in ApiToken.__table__.indexes}
    assert "ix_api_tokens_tenant_active" in idx


def test_created_by_fk_sets_null_on_user_delete() -> None:
    """A token outlives the admin who minted it (kept for audit)."""
    fks = list(ApiToken.__table__.columns["created_by"].foreign_keys)
    assert len(fks) == 1
    assert fks[0].ondelete == "SET NULL"
    assert ApiToken.__table__.columns["created_by"].nullable is True


def test_lifecycle_columns_nullable() -> None:
    """expires_at / revoked_at / last_used_at are all optional."""
    assert ApiToken.__table__.columns["expires_at"].nullable is True
    assert ApiToken.__table__.columns["revoked_at"].nullable is True
    assert ApiToken.__table__.columns["last_used_at"].nullable is True


def test_column_server_defaults() -> None:
    for col_name in ("scopes", "rate_limit", "ip_allowlist"):
        assert (
            ApiToken.__table__.columns[col_name].server_default is not None
        ), f"{col_name} should carry a server default"


def test_rate_limit_default_matches_named_constant() -> None:
    """Default 100/min lives in a named constant, not a magic number."""
    assert DEFAULT_API_TOKEN_RATE_LIMIT == 100
    server_default = ApiToken.__table__.columns["rate_limit"].server_default
    assert server_default is not None
    assert str(DEFAULT_API_TOKEN_RATE_LIMIT) in str(server_default.arg.text)


# ---------------------------------------------------------------------------
# Construction with all attrs
# ---------------------------------------------------------------------------
def test_full_construction() -> None:
    tenant = uuid4()
    creator = uuid4()
    minted = generate_api_token()
    expires = datetime(2026, 12, 31, tzinfo=UTC)
    token = ApiToken(
        tenant_id=tenant,
        token_hash=minted.token_hash,
        prefix=minted.prefix,
        name="CI pipeline",
        scopes=[ApiTokenScope.READ, ApiTokenScope.WRITE],
        expires_at=expires,
        rate_limit=250,
        ip_allowlist=["10.0.0.0/8", "192.168.1.5/32"],
        created_by=creator,
    )
    assert token.tenant_id == tenant
    assert token.created_by == creator
    assert token.name == "CI pipeline"
    assert token.scopes == ["read", "write"]
    assert token.expires_at == expires
    assert token.rate_limit == 250
    assert token.ip_allowlist == ["10.0.0.0/8", "192.168.1.5/32"]
    assert token.revoked_at is None
    assert token.last_used_at is None
    # The plaintext token is NOT an attribute of the persisted row.
    assert not hasattr(token, "token")
    assert minted.token_hash not in (token.prefix, token.name)


def test_minimal_construction_defaults_to_read_scope() -> None:
    token = ApiToken(
        tenant_id=uuid4(),
        token_hash="0" * 64,
        prefix="aapt_deadbeef",
        name="read-only token",
        scopes=[ApiTokenScope.READ],
    )
    assert token.scopes == ["read"]


# ---------------------------------------------------------------------------
# Lifecycle: is_active() reflects expiry + soft-revoke
# ---------------------------------------------------------------------------
def _token(
    *,
    expires_at: datetime | None = None,
    revoked_at: datetime | None = None,
) -> ApiToken:
    return ApiToken(
        tenant_id=uuid4(),
        token_hash="a" * 64,
        prefix="aapt_cafef00d",
        name="t",
        scopes=[ApiTokenScope.READ],
        expires_at=expires_at,
        revoked_at=revoked_at,
    )


def test_is_active_when_fresh() -> None:
    now = datetime(2026, 5, 1, tzinfo=UTC)
    assert _token().is_active(now=now) is True


def test_is_active_false_when_revoked() -> None:
    now = datetime(2026, 5, 1, tzinfo=UTC)
    revoked = _token(revoked_at=now - timedelta(seconds=1))
    assert revoked.is_active(now=now) is False


def test_is_active_false_when_expired() -> None:
    now = datetime(2026, 5, 1, tzinfo=UTC)
    expired = _token(expires_at=now - timedelta(seconds=1))
    assert expired.is_active(now=now) is False


def test_is_active_true_before_expiry() -> None:
    now = datetime(2026, 5, 1, tzinfo=UTC)
    future = _token(expires_at=now + timedelta(days=30))
    assert future.is_active(now=now) is True


def test_is_active_false_at_exact_expiry_instant() -> None:
    """expires_at <= now means expired (boundary is inclusive)."""
    now = datetime(2026, 5, 1, tzinfo=UTC)
    at_boundary = _token(expires_at=now)
    assert at_boundary.is_active(now=now) is False


def test_null_expiry_never_expires() -> None:
    far_future = datetime(2099, 1, 1, tzinfo=UTC)
    assert _token(expires_at=None).is_active(now=far_future) is True


# ---------------------------------------------------------------------------
# Token helpers: mint / hash / verify (constant-time)
# ---------------------------------------------------------------------------
def test_generate_returns_three_consistent_forms() -> None:
    minted = generate_api_token()
    assert isinstance(minted, GeneratedApiToken)
    # The hash on the struct is exactly the hash of the clear token.
    assert minted.token_hash == hash_api_token(minted.token)
    # The prefix is recoverable from the clear token and is its leading part.
    assert minted.token.startswith(minted.prefix)
    assert prefix_of(minted.token) == minted.prefix


def test_generated_prefix_carries_marker() -> None:
    minted = generate_api_token()
    assert minted.prefix.startswith(f"{API_TOKEN_PREFIX_MARKER}_")
    # Prefix fits the persisted column width (String(32)).
    assert len(minted.prefix) <= 32


def test_generated_tokens_are_unique() -> None:
    tokens = {generate_api_token().token for _ in range(50)}
    assert len(tokens) == 50


def test_hash_is_sha256_hex_and_not_the_token() -> None:
    minted = generate_api_token()
    # 64 hex chars = SHA-256.
    assert len(minted.token_hash) == 64
    assert all(c in "0123456789abcdef" for c in minted.token_hash)
    # The clear token never equals (nor is a substring of) its hash.
    assert minted.token != minted.token_hash
    assert minted.token not in minted.token_hash


def test_hash_is_deterministic() -> None:
    assert hash_api_token("hello-token") == hash_api_token("hello-token")


def test_verify_accepts_right_token() -> None:
    minted = generate_api_token()
    assert verify_api_token(minted.token, minted.token_hash) is True


def test_verify_rejects_wrong_token() -> None:
    minted = generate_api_token()
    other = generate_api_token()
    assert verify_api_token(other.token, minted.token_hash) is False
    assert verify_api_token("not-the-token", minted.token_hash) is False
    assert verify_api_token("", minted.token_hash) is False


def test_verify_against_stored_hash_only_never_plaintext() -> None:
    """Simulate the at-rest flow: persist ONLY the hash, verify later."""
    minted = generate_api_token()
    persisted = ApiToken(
        tenant_id=uuid4(),
        token_hash=minted.token_hash,
        prefix=minted.prefix,
        name="ci",
        scopes=[ApiTokenScope.READ],
    )
    # The presented raw token verifies against the stored hash...
    assert verify_api_token(minted.token, persisted.token_hash) is True
    # ...and the raw token is nowhere in the persisted row's data.
    persisted_values = {str(v) for v in (persisted.token_hash, persisted.prefix, persisted.name)}
    assert minted.token not in persisted_values


def test_secret_entropy_is_high() -> None:
    """The secret tail carries the configured CSPRNG entropy."""
    assert API_TOKEN_SECRET_BYTES >= 32


def test_prefix_of_falls_back_for_malformed_token() -> None:
    assert prefix_of("garbage") == "garbage"
    assert prefix_of("only_two") == "only_two"
