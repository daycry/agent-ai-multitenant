"""Tenant-level assistant identity config (Plan 10 task_10_14).

The assistant's *identity* is customizable per tenant: name, avatar,
tone, language (es|en only — CLAUDE.md §12), an optional system_prompt
override and the list of read tools the tenant has enabled. Rather than
add a column per field to ``organizations``, we store the whole identity
as a single JSONB blob in the existing ``tenant_settings`` table
(category ``assistant``, key ``identity``). This keeps the shape evolving
without migrations and matches the "generic key/value config" pattern
established in Plan 06.7.

The *toggle* that gates the feature on/off (``personal_assistant_enabled``)
is a first-class boolean column on ``organizations`` (migration 0047),
because it is read on the hot path of every assistant request and must be
cheap + indexable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.models import TenantSetting

# Settings coordinates for the identity blob.
ASSISTANT_SETTINGS_CATEGORY = "assistant"
ASSISTANT_IDENTITY_KEY = "identity"

# Closed set of supported languages (CLAUDE.md §12: ES + EN only).
SUPPORTED_LANGUAGES: frozenset[str] = frozenset({"es", "en"})

# The full catalogue of cross-project read tools the assistant exposes.
# A tenant's ``enabled_tools`` is intersected with this set, so an unknown
# tool name in the stored config can never widen the surface. The default
# (no config saved) enables every tool.
DEFAULT_ENABLED_TOOLS: tuple[str, ...] = (
    "tenant_projects_status",
    "tenant_plans_summary",
    "tenant_recent_activity",
    "tenant_budget_status",
    "tenant_human_workload",
    "tenant_human_assignments_pending",
    # Write tool: let the assistant remember durable facts about the user
    # (private per-user memory — ADR 0054).
    "remember_about_me",
)

_DEFAULT_NAME = "Asistente"
_DEFAULT_TONE = "profesional y conciso"
_DEFAULT_LANGUAGE = "es"


@dataclass(frozen=True)
class AssistantIdentity:
    """The per-tenant customizable identity of the assistant.

    All fields have safe defaults so a tenant that never configured the
    assistant still gets a usable persona.
    """

    name: str = _DEFAULT_NAME
    avatar_url: str | None = None
    tone: str = _DEFAULT_TONE
    language: str = _DEFAULT_LANGUAGE
    system_prompt_override: str | None = None
    enabled_tools: tuple[str, ...] = DEFAULT_ENABLED_TOOLS

    def effective_tools(self) -> tuple[str, ...]:
        """Return the enabled tools intersected with the known catalogue,
        preserving the catalogue order. Guards against a stored config
        that lists a renamed/removed tool."""
        enabled = set(self.enabled_tools)
        return tuple(t for t in DEFAULT_ENABLED_TOOLS if t in enabled)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "avatar_url": self.avatar_url,
            "tone": self.tone,
            "language": self.language,
            "system_prompt_override": self.system_prompt_override,
            "enabled_tools": list(self.enabled_tools),
        }

    def system_prompt(self) -> str:
        """Build the system prompt the LLM seam receives. A tenant
        override fully replaces the default body; the persona header is
        always prepended so the assistant keeps its identity."""
        lang_label = "español" if self.language == "es" else "English"
        header = (
            f"Eres {self.name}, el asistente personal del tenant. "
            f"Tono: {self.tone}. Responde en {lang_label}."
        )
        if self.system_prompt_override:
            return f"{header}\n\n{self.system_prompt_override}"
        return (
            f"{header}\n\n"
            "Respondes preguntas del Tenant Admin sobre el estado global "
            "cross-proyecto del tenant (proyectos, planes, actividad "
            "reciente, presupuesto). Usa las herramientas de solo lectura "
            "disponibles para consultar datos reales; nunca inventes cifras."
        )

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AssistantIdentity:
        """Build an identity from a stored/UI dict, applying validation
        and defaults. Unknown languages fall back to the default; tool
        names are intersected with the catalogue."""
        language = str(raw.get("language") or _DEFAULT_LANGUAGE)
        if language not in SUPPORTED_LANGUAGES:
            language = _DEFAULT_LANGUAGE
        raw_tools = raw.get("enabled_tools")
        if raw_tools is None:
            tools: tuple[str, ...] = DEFAULT_ENABLED_TOOLS
        else:
            wanted = {str(t) for t in raw_tools}
            tools = tuple(t for t in DEFAULT_ENABLED_TOOLS if t in wanted)
        name = str(raw.get("name") or _DEFAULT_NAME).strip() or _DEFAULT_NAME
        tone = str(raw.get("tone") or _DEFAULT_TONE).strip() or _DEFAULT_TONE
        override = raw.get("system_prompt_override")
        avatar = raw.get("avatar_url")
        return cls(
            name=name,
            avatar_url=str(avatar) if avatar else None,
            tone=tone,
            language=language,
            system_prompt_override=(str(override) if override else None),
            enabled_tools=tools,
        )


async def get_assistant_identity(session: AsyncSession, tenant_id: UUID) -> AssistantIdentity:
    """Read the tenant's assistant identity, or the defaults if unset.

    Runs through the caller's (RLS-bound) session, so a tenant only ever
    reads its own row.
    """
    stmt = select(TenantSetting.value).where(
        TenantSetting.tenant_id == tenant_id,
        TenantSetting.category == ASSISTANT_SETTINGS_CATEGORY,
        TenantSetting.key == ASSISTANT_IDENTITY_KEY,
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if not isinstance(row, dict):
        return AssistantIdentity()
    return AssistantIdentity.from_dict(row)


async def set_assistant_identity(
    session: AsyncSession,
    tenant_id: UUID,
    identity: AssistantIdentity,
    *,
    updated_by_user_id: UUID | None = None,
) -> AssistantIdentity:
    """Upsert the tenant's assistant identity. Returns the stored value
    (normalised through ``from_dict`` so callers get the coerced shape)."""
    normalised = AssistantIdentity.from_dict(identity.to_dict())
    value = normalised.to_dict()
    stmt = (
        pg_insert(TenantSetting)
        .values(
            tenant_id=tenant_id,
            category=ASSISTANT_SETTINGS_CATEGORY,
            key=ASSISTANT_IDENTITY_KEY,
            value=value,
            updated_by_user_id=updated_by_user_id,
        )
        .on_conflict_do_update(
            index_elements=[
                TenantSetting.tenant_id,
                TenantSetting.category,
                TenantSetting.key,
            ],
            set_={"value": value, "updated_by_user_id": updated_by_user_id},
        )
    )
    await session.execute(stmt)
    return normalised


__all__ = [
    "ASSISTANT_IDENTITY_KEY",
    "ASSISTANT_SETTINGS_CATEGORY",
    "DEFAULT_ENABLED_TOOLS",
    "SUPPORTED_LANGUAGES",
    "AssistantIdentity",
    "get_assistant_identity",
    "set_assistant_identity",
]
