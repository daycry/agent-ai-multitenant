"""Persistencia del chat del asistente de tenants (A1, investigación 2026-07-11).

El asistente era STATELESS: cada ``POST /assistant/chat`` enviaba solo el
mensaje actual (sin ``conversation_id``), el frontend guardaba los turnos en
estado React y una recarga lo perdía todo — el criterio de aceptación
``human_10_04`` («mantiene contexto entre mensajes») estaba incumplido.

Espejo del patrón ``cortex_conversations``/``cortex_turns`` pero TENANT-SCOPED
(RLS + ``tenant_id``, como toda tabla de tenants — principio 1) y con
``user_id``: cada admin tiene SUS hilos (el asistente recuerda por usuario,
ADR 0054; sus conversaciones también son suyas).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from api_server.db.base import (
    Base,
    SoftDeleteMixin,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class AssistantConversation(
    Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin
):
    """Un hilo del asistente de un usuario del tenant."""

    __tablename__ = "assistant_conversations"
    __table_args__ = (
        Index("ix_assistant_conversations_user", "tenant_id", "user_id", "updated_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Título derivado del primer mensaje (recortado); editable en el futuro.
    title: Mapped[str | None] = mapped_column(String(160), nullable=True)


class AssistantTurn(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin):
    """Un turno (user/assistant) de un hilo del asistente. Append-only.

    ``user_id`` se duplica del hilo a propósito: el filtro de pertenencia es un
    predicado de columna, nunca un join (defensa en profundidad sobre RLS).
    """

    __tablename__ = "assistant_turns"
    __table_args__ = (
        Index("ix_assistant_turns_conversation", "conversation_id", "created_at"),
        CheckConstraint("role IN ('user', 'assistant')", name="ck_assistant_turns_role"),
    )

    conversation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("assistant_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tools_called: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    rounds: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
