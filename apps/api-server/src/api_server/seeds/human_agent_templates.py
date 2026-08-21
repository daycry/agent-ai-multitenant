"""Built-in Human-Agent templates (Plan 16 task_16_07).

Five curated global Human Agents the platform ships so a tenant can clone-and-
fork a sensible starting point instead of authoring one from scratch: a Security
Reviewer, a Brand Lead, a DBA, a Legal Reviewer and a UX Lead. Each models the
kind of human-in-the-loop step a mixed AI/human plan needs (security audit,
brand decision, DBA intervention, legal sign-off, UX review).

These are ``global_builtin`` :class:`~api_server.db.domain.Agent` rows with
``agent_type='human'`` owned by the platform tenant — visible to every tenant
via the ``agents_global_builtin_read`` SELECT policy (migration 0004). They
carry NO ``human_agent_config`` (the assignment to a concrete User is
intrinsically tenant-scoped, Plan 16 Decisiones Clave); the gallery's clone-
and-fork action mints a fresh, tenant-owned config on fork.

The template's planning hints (``acceptance_timeout_hours``, expected response /
execution times, default notification channels) ride along in ``model_config``
so the fork can seed the new config from them without a config row existing on
the global. Stable UUIDs via the same ``AGENT_SEED_NAMESPACE`` the AI built-ins
use — re-running the seed is an upsert, not a duplicate insert.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.seeds import AGENT_SEED_NAMESPACE, PLATFORM_TENANT_ID


def _agent_id(slug: str) -> UUID:
    # Same namespace + key shape as builtin_agents._agent_id so the human
    # templates share the AI built-ins' stable-id scheme without colliding
    # (slugs are distinct).
    return uuid5(AGENT_SEED_NAMESPACE, f"agent:{slug}")


@dataclass(frozen=True)
class HumanAgentTemplate:
    slug: str
    name: str
    description: str
    role: str
    # Planning hints folded into model_config; the fork seeds its config row
    # from these. None = unknown (the fork leaves the column NULL).
    acceptance_timeout_hours: int = 24
    expected_response_time_hours: int | None = None
    expected_execution_time_hours: int | None = None
    notification_channels: tuple[str, ...] = field(default_factory=lambda: ("email", "in_app"))

    @property
    def id(self) -> UUID:
        return _agent_id(self.slug)

    def to_model_config(self) -> dict[str, Any]:
        return {
            "acceptance_timeout_hours": self.acceptance_timeout_hours,
            "expected_response_time_hours": self.expected_response_time_hours,
            "expected_execution_time_hours": self.expected_execution_time_hours,
            "notification_channels": list(self.notification_channels),
        }


HUMAN_AGENT_TEMPLATES: tuple[HumanAgentTemplate, ...] = (
    HumanAgentTemplate(
        slug="human-security-reviewer-senior",
        name="Security Reviewer Senior",
        description=(
            "Revisor de seguridad humano para auditorías sensibles (auth, datos "
            "cross-tenant, supply chain). Para pasos del plan que exigen criterio "
            "humano experto, no automatizable."
        ),
        role="security",
        acceptance_timeout_hours=24,
        expected_response_time_hours=4,
        expected_execution_time_hours=8,
    ),
    HumanAgentTemplate(
        slug="human-brand-lead",
        name="Brand Lead",
        description=(
            "Responsable de marca para decisiones de identidad, tono y mensajes "
            "clave. Aprueba o rechaza propuestas con criterio de negocio."
        ),
        role="custom",
        acceptance_timeout_hours=48,
        expected_response_time_hours=8,
        expected_execution_time_hours=4,
    ),
    HumanAgentTemplate(
        slug="human-dba-senior",
        name="DBA Senior",
        description=(
            "Administrador de bases de datos para intervenciones en producción "
            "(migraciones críticas, tuning, recuperación). Paso humano obligatorio "
            "antes de operar sobre datos de producción."
        ),
        role="devops",
        acceptance_timeout_hours=12,
        expected_response_time_hours=2,
        expected_execution_time_hours=6,
    ),
    HumanAgentTemplate(
        slug="human-legal-reviewer",
        name="Legal Reviewer",
        description=(
            "Revisor legal para firma de contratos, cláusulas y cumplimiento. "
            "Tarea humana de tipo 'sign-off' dentro de planes mixtos."
        ),
        role="reviewer",
        acceptance_timeout_hours=72,
        expected_response_time_hours=24,
        expected_execution_time_hours=8,
    ),
    HumanAgentTemplate(
        slug="human-ux-lead",
        name="UX Lead",
        description=(
            "Responsable de experiencia de usuario para validar flujos, "
            "accesibilidad y consistencia de diseño antes de cerrar una feature."
        ),
        role="frontend_dev",
        acceptance_timeout_hours=48,
        expected_response_time_hours=8,
        expected_execution_time_hours=6,
    ),
)


# A human agent's system_prompt column is required (NOT NULL) but meaningless
# for a human — a short human-readable note keeps the column non-empty.
_HUMAN_SYSTEM_PROMPT = (
    "Human agent template. Represents a human (or human role) assignable to "
    "plan tasks exactly like an AI agent. The actual human is bound to the "
    "concrete User on fork (assignment is tenant-intrinsic)."
)


_UPSERT_SQL = text("""
    INSERT INTO agents (
        id, tenant_id, name, description, agent_type, role,
        system_prompt, model_config, memory_scope, review_capability,
        max_concurrent_tasks, is_template, scope, project_id
    )
    VALUES (
        :id, :tenant_id, :name, :description, 'human', :role,
        :system_prompt, CAST(:model_config AS jsonb), 'private',
        false, 1, true, 'global_builtin', NULL
    )
    ON CONFLICT (id) DO UPDATE SET
        name = EXCLUDED.name,
        description = EXCLUDED.description,
        role = EXCLUDED.role,
        system_prompt = EXCLUDED.system_prompt,
        model_config = EXCLUDED.model_config,
        updated_at = now()
    """)


async def seed_human_agent_templates(session: AsyncSession) -> int:
    """Upsert all global Human-Agent templates. Returns rows touched."""
    for tpl in HUMAN_AGENT_TEMPLATES:
        await session.execute(
            _UPSERT_SQL,
            {
                "id": str(tpl.id),
                "tenant_id": str(PLATFORM_TENANT_ID),
                "name": tpl.name,
                "description": tpl.description,
                "role": tpl.role,
                "system_prompt": _HUMAN_SYSTEM_PROMPT,
                "model_config": json.dumps(tpl.to_model_config()),
            },
        )
    return len(HUMAN_AGENT_TEMPLATES)
