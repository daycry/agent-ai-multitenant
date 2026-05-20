"""SQLAlchemy 2.x declarative base and mixins shared by every model.

Conventions:

- UUID v7 primary keys (lexically sortable by creation time, no central
  generator needed). Generated client-side via the `uuid6` library until
  PostgreSQL 17 ships native uuid_generate_v7().
- All timestamps are TIMESTAMPTZ.
- Soft-delete via `deleted_at`. Queries must filter `deleted_at IS NULL`
  unless the caller explicitly opts into deleted rows.
- Tenant-scoped tables carry `tenant_id UUID NOT NULL` and rely on
  PostgreSQL RLS (enabled in migration 0001) for cross-tenant isolation.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from uuid6 import uuid7


class Base(DeclarativeBase):
    """Root declarative base. All models inherit from this."""


def _new_uuid7() -> UUID:
    """Generate a UUID v7 (millisecond timestamp prefix + random tail)."""
    return uuid7()


class UUIDPrimaryKeyMixin:
    """Adds an `id: UUID` primary key defaulted to a freshly generated v7."""

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=_new_uuid7,
    )


class TimestampMixin:
    """`created_at` and `updated_at` both TIMESTAMPTZ, server-side defaults."""

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class SoftDeleteMixin:
    """`deleted_at IS NULL` means the row is live."""

    deleted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        default=None,
    )


class TenantScopedMixin:
    """`tenant_id` column — RLS policies use it for isolation."""

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
