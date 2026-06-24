"""Córtex F3: cortex_identity (singleton) + cortex_identity_history (BYPASSRLS — ADR 0074/0077).

La **identidad evolutiva** del System Owner:

- :class:`CortexIdentity` — un blob ``identity_state`` JSONB **singleton por owner**
  (nombre autoelegido, valores, rasgos Big-Five, narrativa, modelo del owner, baseline
  PAD). El invariante singleton lo garantiza ``uq_cortex_identity_owner`` UNIQUE.
- :class:`CortexIdentityHistory` — versionado **append-only** de cada reescritura, con
  el ``diff`` (qué cambió: ``{campo:{before,after}}``) y un ``reason`` 1-línea.

Como el resto del córtex es **tenant-less** (excepción consciente al Principio 1 —
no hay RLS, ADR 0074): el aislamiento es por un filtro ``owner_user_id`` explícito
en TODO SQL (defensa en profundidad; ver ``cortex/identity.py`` y el test
cross-owner de F3). La identidad **nunca se borra** (ADR 0077: ``kind ∈ {identity,
owner_model}`` protegido) — solo se versiona.

Estilo espejo de :mod:`api_server.db.cortex` / :mod:`api_server.db.cortex_affect`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import TIMESTAMP as PG_TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from api_server.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class CortexIdentity(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """La identidad SINGLETON del córtex del owner (un blob ``identity_state``).

    NO :class:`TenantScopedMixin` (no hay RLS), NO :class:`SoftDeleteMixin`
    (ADR 0077: la identidad nunca se borra, solo se versiona). ``owner_user_id``
    es el eje de aislamiento (filtro explícito en todo SQL) y el UNIQUE
    ``uq_cortex_identity_owner`` impone el invariante singleton por owner.
    """

    __tablename__ = "cortex_identity"
    __table_args__ = (
        # Invariante singleton: un identity por owner.
        Index("uq_cortex_identity_owner", "owner_user_id", unique=True),
    )

    # Eje de aislamiento (FK a users.id; el system_owner singleton).
    owner_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Blob: name, core_values, traits (Big-Five), narrative, relationship_model,
    # learning_goals, language, mood_baseline (PAD set-point), affect_params.
    identity_state: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Se incrementa en cada reescritura (== última version de history).
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    # onboarding | reflection | owner_override (quién escribió el estado actual).
    updated_by: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'onboarding'")
    )
    # NULL ⇒ onboarding pendiente.
    onboarded_at: Mapped[datetime | None] = mapped_column(
        PG_TIMESTAMP(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"CortexIdentity(id={self.id!r}, owner_user_id={self.owner_user_id!r},"
            f" version={self.version!r})"
        )


class CortexIdentityHistory(Base, UUIDPrimaryKeyMixin):
    """Una versión histórica (append-only) del ``identity_state`` del owner.

    Inmutable: solo ``created_at`` (sin ``updated_at`` ni ``deleted_at``). Cada
    reescritura de :class:`CortexIdentity` añade una fila aquí con el snapshot
    completo, el ``diff`` del cambio y un ``reason``. ``owner_user_id`` es el eje
    de aislamiento (filtro explícito en todo SQL).
    """

    __tablename__ = "cortex_identity_history"
    __table_args__ = (
        # Timeline de versiones del owner (más reciente primero).
        Index(
            "ix_cortex_identity_history_owner_version",
            "owner_user_id",
            text("version DESC"),
        ),
        # Una sola fila por (owner, version) — versionado sin duplicados.
        Index(
            "uq_cortex_identity_history_owner_version",
            "owner_user_id",
            "version",
            unique=True,
        ),
    )

    owner_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # La versión que esta fila CAPTURA.
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # Snapshot completo del identity_state en esa versión.
    identity_state: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # {campo: {before, after}} — auditoría del cambio.
    diff: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    updated_by: Mapped[str] = mapped_column(Text, nullable=False)
    # Resumen 1-línea del cambio (p. ej. el ciclo de reflexión que lo produjo).
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Inmutable: solo created_at.
    created_at: Mapped[datetime] = mapped_column(
        PG_TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"CortexIdentityHistory(id={self.id!r}, owner_user_id={self.owner_user_id!r},"
            f" version={self.version!r})"
        )


__all__ = ["CortexIdentity", "CortexIdentityHistory"]
