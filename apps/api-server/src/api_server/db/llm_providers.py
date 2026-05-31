"""Platform-global LLM provider catalog ORM (Plan 11.2 Fase A, task_11_2_01).

A single platform-global table — ``llm_providers`` — that records the
runtime configuration of each LLM provider the platform talks to. The
four supported provider paths are the closed catalogue of ADR 0021
(Claude Agent SDK, GitHub Copilot, Azure AI Foundry via APIM, Ollama);
the runtime selection factory (task_11_2_04) reads a row's ``base_url`` +
non-secret ``config`` plus the secret resolved from Vault, falling back to
the installer/env config when no active row exists.

Tenancy decision (ADR 0028 + CLAUDE.md principle 9 / 1):
**platform-global, gestionado solo por ``system_admin``.** A provider is
configured once for the whole platform — it is NOT a tenant's data — so
the table carries **no ``tenant_id``**. Unlike the read-open catalogs
``model_prices`` (migration 0049) and ``exchange_rates`` (migration
0062), a provider row exposes *operational* configuration (endpoints,
which Vault path holds its credential) that is **not** open to every
tenant. ADR 0028 therefore makes this table **platform-global with NO RLS
policy at all**: access is solely through the ``system_admin`` endpoints
running on the BYPASSRLS admin engine (``get_admin_session``). A
NOBYPASSRLS (tenant / ``app_user``) session is denied every read and
write by table GRANTs alone — the migration grants the table only to the
migrations role, never to ``app_user`` — so there is no RLS policy to
reason about and no risk of a tenant ever seeing a provider's
configuration.

Secret handling (CLAUDE.md: no plaintext secrets in the DB, never echoed
/ logged / returned). A provider's credential — the Claude/Copilot OAuth
token, the Azure APIM API key — is written ONLY to Vault (at
``platform/llm/<provider_id>``, task_11_2_02) and the DB persists ONLY
the pointer ``secret_vault_path``. The credential value itself NEVER
lands in a column here; there is deliberately no column that could hold
one. ``base_url`` (the APIM gateway / Ollama endpoint) and ``config``
(non-secret knobs: enabled model defaults, flags, ...) are the only
operational fields, and neither is a secret.

NO migration ships in THIS module — migration 0070 creates the table +
the kind CHECK + the (no-)RLS decision. This module is the ORM shape +
the closed ``LLMProviderKind`` enum + a small repository helper so the
rest of the plan builds against a stable contract.
"""

from __future__ import annotations

import enum
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from api_server.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


# =============================================================================
# Enum (StrEnum so the value persists as a stable plain string / TEXT)
# =============================================================================
class LLMProviderKind(enum.StrEnum):
    """The four supported provider paths — closed catalogue (ADR 0021).

    Mirrors the ``agents.llm_provider`` value set and the installer's
    ``installer_backend.config.LLMProviderKind`` so a provider row, the
    agent that selects it, and the installer bootstrap all speak the same
    four strings. Extend by adding members; never rename existing ones —
    historical rows still reference the old string value. Adding a fifth
    provider requires an explicit ADR (CLAUDE.md principle 9).
    """

    CLAUDE_SDK = "claude_sdk"
    COPILOT = "copilot"
    AZURE_FOUNDRY = "azure_foundry"
    OLLAMA = "ollama"


# The CHECK / membership source of truth — the four valid ``kind`` values.
LLM_PROVIDER_KINDS: tuple[str, ...] = tuple(k.value for k in LLMProviderKind)


# =============================================================================
# llm_providers — platform-global provider configuration (no tenant_id, no RLS)
# =============================================================================
class LlmProvider(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One configured LLM provider, platform-global (ADR 0028).

    Holds the *non-secret* runtime configuration of a provider: its
    ``kind`` (one of the four ADR-0021 paths), an operator label
    (``display_name``), the optional endpoint (``base_url`` — the APIM
    gateway URL / the Ollama endpoint; NULL for the subscription-based
    Claude SDK path), the Vault pointer to its credential
    (``secret_vault_path`` — NULL until a secret is written), an
    ``is_active`` toggle, and a free-form non-secret ``config`` JSONB
    (model defaults, feature flags, ...). The credential VALUE itself is
    never stored here — only in Vault, referenced by ``secret_vault_path``.

    NOT tenant-scoped and NOT soft-deleted: the catalog is platform-global
    and managed only by ``system_admin``. There is NO RLS policy (ADR
    0028); access is solely through the BYPASSRLS admin engine the
    ``system_admin`` endpoints use.
    """

    __tablename__ = "llm_providers"
    __table_args__ = (
        # The DB enforces the closed LLMProviderKind value set (same CHECK
        # shape as ck_agents_agent_type / ck_human_task_assignments_status).
        CheckConstraint(
            "kind IN ('claude_sdk', 'copilot', 'azure_foundry', 'ollama')",
            name="ck_llm_providers_kind",
        ),
    )

    # One of the four ADR-0021 provider paths (an LLMProviderKind value).
    # TEXT + CHECK (not a PG ENUM) so adding a path later is a CHECK swap,
    # not a fragile enum ALTER. Width matches the longest value comfortably.
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # Operator-facing label ("Claude (prod)", "Ollama local", ...).
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # The provider endpoint: the APIM gateway URL (azure_foundry) or the
    # Ollama endpoint. NULL for claude_sdk (subscription, no base URL).
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Vault pointer to the provider's credential (platform/llm/<id>). NULL
    # until a secret is written (task_11_2_02). The credential VALUE is
    # NEVER stored in any column — only in Vault, referenced by this path.
    secret_vault_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Free-form NON-secret config: enabled flags, default model ids, etc.
    # A credential must never be put here — that is what Vault is for.
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # An inactive provider is configured but not used by the runtime
    # factory (it falls back to env/installer). Default true: a newly
    # created provider is active.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"LlmProvider(id={self.id!r}, kind={self.kind!r}, "
            f"display_name={self.display_name!r}, is_active={self.is_active!r})"
        )


# =============================================================================
# Repository helpers — thin async accessors over the admin (BYPASSRLS) session.
#
# Platform-global: every helper runs on the System-Admin session the
# /admin/llm-providers endpoints own (get_admin_session). There is no
# tenant filter because the table has no tenant_id (ADR 0028).
# =============================================================================
async def get_llm_provider(session: AsyncSession, provider_id: UUID) -> LlmProvider | None:
    """Fetch one provider by id, or ``None`` when it does not exist."""
    return await session.get(LlmProvider, provider_id)


async def list_llm_providers(
    session: AsyncSession, *, active_only: bool = False
) -> list[LlmProvider]:
    """List all configured providers, newest first.

    ``active_only`` restricts to ``is_active`` rows — what the runtime
    selection factory (task_11_2_04) wants when picking the row that wins
    over the env/installer fallback.
    """
    from sqlalchemy import select

    stmt = select(LlmProvider)
    if active_only:
        stmt = stmt.where(LlmProvider.is_active.is_(True))
    # UUID v7 ids are time-ordered, so ordering by id desc is "newest first".
    stmt = stmt.order_by(LlmProvider.id.desc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def list_active_llm_providers_by_kind(
    session: AsyncSession, kind: str | LLMProviderKind
) -> list[LlmProvider]:
    """Active providers of a given ``kind``, newest first.

    The runtime factory resolves the provider for an agent by its
    ``agents.llm_provider`` kind; this is the lookup it uses.
    """
    from sqlalchemy import select

    kind_value = str(kind)
    stmt = (
        select(LlmProvider)
        .where(LlmProvider.kind == kind_value, LlmProvider.is_active.is_(True))
        .order_by(LlmProvider.id.desc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


__all__ = [
    "LLM_PROVIDER_KINDS",
    "LLMProviderKind",
    "LlmProvider",
    "get_llm_provider",
    "list_active_llm_providers_by_kind",
    "list_llm_providers",
]
