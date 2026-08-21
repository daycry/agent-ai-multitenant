"""Córtex F2: cortex_affect_snapshots (tenant-less, eje owner — ADR 0074/0075/0156).

Serie temporal **append-only e inmutable** del estado del motor afectivo del
System Owner (PAD + mood + drives). Como el resto del córtex es **tenant-less**:
el aislamiento es por ``owner_user_id``, en dos capas que no se sustituyen entre
sí — el filtro explícito en TODO SQL (``cortex/affect_store.py``, el test
cross-owner de F2), que es la que actúa hoy porque todos los caminos conectan con
un rol BYPASSRLS; y la policy ``cortex_affect_snapshots_owner_only`` de la
migración ``0140``, que es la que responde el día que una query llegue por la
sesión ordinaria de ``app_user``.

Inmutable: NO :class:`SoftDeleteMixin` ni ``updated_at`` — los snapshots no se
editan ni se borran; un decay/evento produce SIEMPRE una fila nueva. Por eso
define ``created_at`` directamente en vez de heredar :class:`TimestampMixin`.

> Honestidad (ADR 0075 §6): el ``mood_label`` es una etiqueta **derivada SOLO
> para UI** del cuadrante PAD; la fuente de verdad es el estado continuo
> (valence/arousal/dominance). Es simulación afectiva determinista, NO emociones
> reales.

Estilo espejo de :mod:`api_server.db.cortex`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Float, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import TIMESTAMP as PG_TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from api_server.db.base import Base, UUIDPrimaryKeyMixin


class CortexAffectSnapshot(Base, UUIDPrimaryKeyMixin):
    """Una muestra inmutable del estado afectivo del córtex (motor PAD).

    NO :class:`TenantScopedMixin` (la RLS no cuelga del tenant), NO
    :class:`SoftDeleteMixin`, NO ``updated_at``: filas append-only.
    ``owner_user_id`` es el eje de aislamiento — filtro explícito en todo SQL +
    policy ``cortex_affect_snapshots_owner_only`` (migración ``0140``).
    """

    __tablename__ = "cortex_affect_snapshots"
    __table_args__ = (
        # Sirve "el último snapshot" y ``/affect/timeseries``.
        Index(
            "ix_cortex_affect_snapshots_owner_created",
            "owner_user_id",
            text("created_at DESC"),
        ),
        # Sirve ``/episodes?emotion=`` (filtra por etiqueta de mood).
        Index(
            "ix_cortex_affect_snapshots_owner_mood_label",
            "owner_user_id",
            "mood_label",
        ),
        # Idempotencia del distilador: un snapshot por turno como máximo (parcial
        # para que los snapshots de decay/mantenimiento, sin turno, no choquen).
        Index(
            "uq_cortex_affect_snapshot_per_turn",
            "source_turn_id",
            unique=True,
            postgresql_where=text("source_turn_id IS NOT NULL"),
        ),
    )

    # Eje de aislamiento (FK lógica a users.id; el system_owner singleton).
    owner_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # --- Emoción viva (capa rápida) ---
    valence: Mapped[float] = mapped_column(Float, nullable=False)  # [-1, 1]
    arousal: Mapped[float] = mapped_column(Float, nullable=False)  # [0, 1]
    dominance: Mapped[float] = mapped_column(Float, nullable=False)  # [-1, 1]
    intensity: Mapped[float] = mapped_column(Float, nullable=False)  # [0, 1]

    # --- Mood (capa lenta, EWMA) ---
    mood_valence: Mapped[float] = mapped_column(Float, nullable=False)
    mood_arousal: Mapped[float] = mapped_column(Float, nullable=False)
    mood_dominance: Mapped[float] = mapped_column(Float, nullable=False)
    # Etiqueta categórica derivada SOLO-UI (no fuente de verdad).
    mood_label: Mapped[str] = mapped_column(String(32), nullable=False)

    # --- Drives homeostáticos {curiosity,bonding,coherence,competence} ∈ [0,1] ---
    drives: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # Razón del appraisal (NULL en snapshots de decay o fail-open delta=0).
    appraisal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Back-link al turno que disparó el snapshot (NULL en decay/mantenimiento).
    source_turn_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("cortex_turns.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Inmutable: solo created_at (sin updated_at ni deleted_at).
    created_at: Mapped[datetime] = mapped_column(
        PG_TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"CortexAffectSnapshot(id={self.id!r}, owner_user_id={self.owner_user_id!r},"
            f" mood_label={self.mood_label!r})"
        )


__all__ = ["CortexAffectSnapshot"]
