"""Córtex F4: cortex_curiosity_pursuits (tenant-less, BYPASSRLS — ADR 0074/0078).

Auditoría + idempotencia de la **curiosidad autónoma**: cada vez que el bucle de
fondo elige un tema y lo investiga, deja aquí una fila con su ciclo de vida
(``selected → searching → digested`` | ``skipped`` | ``failed``), el coste/búsquedas
consumidas y la memoria ``learning`` generada. Sirve para:

  * el panel "lo que está aprendiendo" (historial de persecuciones);
  * la **idempotencia** (la memoria ``learning`` referencia el ``pursuit_id`` en su
    ``metadata_``, así una re-ejecución no duplica);
  * el dedup por tema reciente (no re-investigar lo mismo en N días).

Como el resto del córtex es **tenant-less** (ADR 0074): el aislamiento es por
``owner_user_id``, con el filtro explícito en TODO SQL como primera capa (la
prueba de mérito es el test cross-owner) y la policy
``cortex_curiosity_pursuits_owner_only`` de la migración ``0140`` como defensa
estructural (ADR 0156). Estilo espejo de :mod:`api_server.db.cortex_affect`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import TIMESTAMP as PG_TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from api_server.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

#: Estados válidos del ciclo de vida de una persecución de curiosidad.
#: ``surfaced`` (migración 0103): el tema ya se abrió en un turno (surfacing).
CURIOSITY_STATUSES: tuple[str, ...] = (
    "selected",
    "searching",
    "digested",
    "surfaced",
    "skipped",
    "failed",
)


class CortexCuriosityPursuit(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Una persecución autónoma de curiosidad del córtex (auditoría + idempotencia).

    NO :class:`TenantScopedMixin` (la RLS no cuelga del tenant).
    ``owner_user_id`` es el eje de aislamiento: filtro explícito en todo SQL +
    policy ``cortex_curiosity_pursuits_owner_only`` (migración ``0140``).
    """

    __tablename__ = "cortex_curiosity_pursuits"
    __table_args__ = (
        # La cola "pendiente" + el conteo diario por estado.
        Index(
            "ix_cortex_pursuits_owner_status",
            "owner_user_id",
            "status",
        ),
        # Dedup por tema reciente (no re-investigar lo mismo en N días).
        Index(
            "ix_cortex_pursuits_owner_topic_created",
            "owner_user_id",
            "topic",
            text("created_at DESC"),
        ),
        CheckConstraint(
            "status IN ('selected', 'searching', 'digested', 'surfaced', 'skipped', 'failed')",
            name="ck_cortex_pursuits_status",
        ),
    )

    # Eje de aislamiento (FK lógica a users.id; el system_owner singleton).
    owner_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # El tema/entity elegido (normalizado como query_entity_terms).
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    # Las entities del owner que motivaron el tema (trazabilidad).
    source_entities: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'selected'"))
    # Snapshot del drive curiosity (+ resto) al disparar — el "por qué ahora".
    drive_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # FK lógica a memory_entries.id — la memoria semantic/learning generada.
    learning_memory_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    # Coste real de la pasada (USD) y nº de búsquedas consumidas (auditoría/budget).
    cost_usd: Mapped[float] = mapped_column(
        Numeric(12, 6), nullable=False, server_default=text("0")
    )
    search_count: Mapped[int] = mapped_column(
        Numeric(10, 0), nullable=False, server_default=text("0")
    )
    # Veredicto del OWNER-APPROVAL GATE (migración 0123). TRI-ESTADO a propósito:
    #   None  → propuesto, esperando al owner (el bucle NO busca);
    #   True  → aprobado (la siguiente pasada lo investiga);
    #   False → rechazado (no se reintenta).
    # Un booleano no-nulo fundiría "pendiente" con "rechazado" y el gate del paso 7
    # del bucle no podría distinguir esperar de descartar: su condición es
    # literalmente ``approved IS NULL``.
    approved: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Extensible: razón de skip, trip del circuit-breaker, etc.
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Cuándo se abrió el tema en un turno (NULL hasta entonces).
    surfaced_at: Mapped[datetime | None] = mapped_column(PG_TIMESTAMP(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"CortexCuriosityPursuit(id={self.id!r}, owner_user_id={self.owner_user_id!r},"
            f" topic={self.topic!r}, status={self.status!r})"
        )


__all__ = ["CURIOSITY_STATUSES", "CortexCuriosityPursuit"]
