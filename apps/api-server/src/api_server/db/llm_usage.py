"""Consumo LLM de los consumidores NO-run (ADR 0116).

El gasto de plataforma se derivaba EXCLUSIVAMENTE de
``executions.total_cost_usd``: el asistente de tenants, el córtex del owner y
el chat de planning consumían LLM sin contabilizar. Esta tabla registra ese
consumo por turno (best-effort — la contabilidad jamás rompe un chat).

``tenant_id`` es NULLABLE: los turnos del córtex son del owner de plataforma,
no de un tenant. Con RLS estándar esas filas quedan invisibles para tenants
(solo sesiones admin las leen) — exactamente lo deseado.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import CheckConstraint, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from api_server.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class LLMUsageEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Un turno de consumo LLM fuera del pipeline de runs."""

    __tablename__ = "llm_usage_events"
    __table_args__ = (
        Index("ix_llm_usage_events_tenant_created", "tenant_id", "created_at"),
        CheckConstraint(
            "source IN ('assistant', 'cortex', 'planning')",
            name="ck_llm_usage_events_source",
        ),
    )

    # NULLABLE a propósito (córtex = plataforma) — ver docstring del módulo.
    tenant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    calls: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
