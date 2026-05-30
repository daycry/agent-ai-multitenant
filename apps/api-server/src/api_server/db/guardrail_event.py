"""Tenant-scoped guardrail-event log ORM (Plan 11 Fase E, task_11_20).

A single append-only table — ``guardrail_events`` — that records one row
every time a guardrail *triggers* anywhere in the platform (the four hook
points of the Plan 11 engine: ``pre_llm`` / ``post_llm`` / ``pre_tool`` /
``post_tool``). It is the substrate behind the tenant guardrails dashboard
(counts by type / severity over time + recent events) and the configurable
alerts of task_11_21.

Tenancy decision (CLAUDE.md principle 1 — multi-tenancy from day one):
**tenant-owned** (``tenant_id NOT NULL`` via :class:`TenantScopedMixin` +
RLS, added by migration 0052). A guardrail event is a tenant's operational
data — a tenant sees ONLY its own events / dashboard / alerts — so it is a
plain tenant-isolated table like ``executions`` / ``notification_logs``,
with the canonical FOR ALL tenant-isolation RLS policy. There is **no**
NULL-tenant / platform branch: every event is attributed to the tenant
whose work tripped the guardrail.

Append-only + immutable: like ``notification_logs`` / the foundations
``audit_log``, an event row is written once and never updated or deleted
through the app path. We declare a single ``created_at`` explicitly (no
:class:`TimestampMixin`) because an immutable record has no ``updated_at``.

**Never persist the raw secret / PII that triggered the event (CLAUDE.md:
NO plaintext secrets; principle on PII masking).** The guardrails that
fire on sensitive content (``pii`` / ``secret_leakage`` / …) surface a
*redacted* detail — matched spans are reported as masked markers
(``[REDACTED:<family>]``), offsets + family only, never the raw value.
This table stores that **masked summary** in ``detail`` (a short text) and
``detail_payload`` (a JSONB of non-sensitive metadata: matched families,
counts, offsets, the resolved schema error, …). The recorder service
(``api_server.guardrails.events``) is responsible for never copying a raw
secret into either column — it builds the masked summary from the
guardrail's own redacted output and asserts no raw value leaks. The
human_11_01 / human_11_02 tests verify this end to end.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from api_server.db.base import Base, TenantScopedMixin, UUIDPrimaryKeyMixin


class GuardrailHookPoint(enum.StrEnum):
    """The point in the LLM / tool cycle the guardrail ran at.

    Mirrors ``shared_guardrails.types.HookPoint`` (the engine's hook
    literal) as a stable string so a persisted row round-trips cleanly.
    Extend by adding members; never rename — historical rows reference the
    old value.
    """

    PRE_LLM = "pre_llm"
    POST_LLM = "post_llm"
    PRE_TOOL = "pre_tool"
    POST_TOOL = "post_tool"


class GuardrailEventSeverity(enum.StrEnum):
    """How serious the triggered guardrail was.

    Mirrors ``shared_guardrails.types.Severity`` (info / low / medium /
    high / critical) so the engine's severity persists 1:1.
    """

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class GuardrailEventAction(enum.StrEnum):
    """The action the host took (or would take) for the triggered guardrail.

    Mirrors ``shared_guardrails.types.Action`` (the six actions). NULL is
    allowed on the row for a triggered guardrail whose config resolved no
    action (a pure ``warn``-less observation), but in practice every fired
    guardrail carries one.
    """

    BLOCK = "block"
    REDACT = "redact"
    WARN = "warn"
    RETRY_WITH_FEEDBACK = "retry_with_feedback"
    ESCALATE_TO_HUMAN = "escalate_to_human"
    TRANSFORM = "transform"


class GuardrailEvent(Base, UUIDPrimaryKeyMixin, TenantScopedMixin):
    """One append-only record of a triggered guardrail (task_11_20).

    Tenant-owned (``tenant_id`` NOT NULL + RLS) and immutable — written
    once by the recorder service, never updated / deleted through the app
    path. The ``detail`` / ``detail_payload`` carry a **masked** summary
    only: the raw PII / secret that tripped the guardrail is NEVER stored
    (see the module docstring).
    """

    __tablename__ = "guardrail_events"
    __table_args__ = (
        # The dashboard's primary query: a tenant's recent events,
        # newest-first.
        Index("ix_guardrail_events_tenant_created", "tenant_id", "created_at"),
        # "counts by type over time" + the type filter on the list endpoint.
        Index(
            "ix_guardrail_events_tenant_type_created", "tenant_id", "guardrail_type", "created_at"
        ),
        # "counts by severity over time" + the severity filter.
        Index(
            "ix_guardrail_events_tenant_severity_created",
            "tenant_id",
            "severity",
            "created_at",
        ),
    )

    # --- what fired ----------------------------------------------------------
    # The guardrail type that triggered (one of the 12 built-ins:
    # ``pii`` / ``secret_leakage`` / ``prompt_injection`` / …). TEXT-stored
    # so the catalogue evolves migration-free as new types register.
    guardrail_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # Which of the four hook points it ran at.
    hook_point: Mapped[str] = mapped_column(String(16), nullable=False)
    # Severity + the action the host applied (the six actions).
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # --- where it fired (refs, all nullable — context may be partial) -------
    # The project / agent / execution the guardrail fired in. UUIDs kept as
    # plain columns (no FK) so an event survives the referenced row being
    # deleted — an immutable audit record outlives the work it describes,
    # mirroring ``notification_logs``.
    project_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    agent_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    execution_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    # The agent's stable name / role when no agent_id is available (the
    # planning chat fires guardrails before an execution exists). Free-form.
    agent_label: Mapped[str | None] = mapped_column(String(160), nullable=True)

    # --- the REDACTED detail (NEVER the raw secret / PII) -------------------
    # A short human-readable masked summary ("2 secret(s) redacted:
    # AWS_ACCESS_KEY, GITHUB_TOKEN"). Bounded text — never the offending
    # value itself.
    detail: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    # Non-sensitive structured metadata: matched families, counts, offsets,
    # the resolved JSON-schema error, … . JSONB so the shape evolves
    # migration-free. MUST NOT carry a raw secret / PII (the recorder
    # asserts this).
    detail_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # --- when ----------------------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"GuardrailEvent(id={self.id!r}, tenant={self.tenant_id!r}, "
            f"type={self.guardrail_type!r}, hook={self.hook_point!r}, "
            f"severity={self.severity!r}, action={self.action!r})"
        )


__all__ = [
    "GuardrailEvent",
    "GuardrailEventAction",
    "GuardrailEventSeverity",
    "GuardrailHookPoint",
]
