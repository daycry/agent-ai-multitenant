"""Built-in approval-policy templates (task_01_14).

Four named presets covering the project lifecycle:

  sandbox      everything auto -- safe playgrounds, internal demos.
  development  default for active development; risky ops gated.
  production   strict; almost everything human_required.
  customer     for client-facing work; even reads/comms get reviewed.

Each preset evaluates all 13 categories of sensitive actions (spec
§7.7-7.8). When a tenant adopts a template, the `categories` JSON gets
copied into `projects.human_approval_policy`; modifications there are
local to the project.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID, uuid5

from shared_domain.approval_categories import APPROVAL_CATEGORIES
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.seeds import PLATFORM_TENANT_ID

POLICY_SEED_NAMESPACE: UUID = UUID("00000000-0000-0000-0000-000000000015")

# Categories of sensitive actions (spec §7.7-7.8). Single source in
# shared-domain (APPROVAL_CATEGORIES) so the sandboxed runtime approval gate
# uses the SAME vocabulary — they had diverged, opening a fail-open hole (g6).
CATEGORIES: tuple[str, ...] = APPROVAL_CATEGORIES


def _policy_id(slug: str) -> UUID:
    return uuid5(POLICY_SEED_NAMESPACE, f"policy:{slug}")


@dataclass(frozen=True)
class BuiltinPolicy:
    slug: str
    name: str
    description: str
    decisions: dict[str, str]  # category -> "auto" | "human_required"

    @property
    def id(self) -> UUID:
        return _policy_id(self.slug)


def _all(value: str) -> dict[str, str]:
    return dict.fromkeys(CATEGORIES, value)


# ---------------------------------------------------------------------------
# The four built-in presets
# ---------------------------------------------------------------------------
BUILTIN_POLICIES: tuple[BuiltinPolicy, ...] = (
    BuiltinPolicy(
        slug="sandbox",
        name="Sandbox",
        description=(
            "Permite cualquier acción sin revisión humana. "
            "Solo para entornos aislados o demos internas."
        ),
        decisions=_all("auto"),
    ),
    BuiltinPolicy(
        slug="development",
        name="Desarrollo",
        description=(
            "Default sensato para desarrollo activo: cambios de código "
            "y HTTP GET en auto; el resto pasa por humano."
        ),
        decisions={
            **_all("human_required"),
            "code_changes": "auto",
            "git_commit": "auto",
            "external_http_get": "auto",
        },
    ),
    BuiltinPolicy(
        slug="production",
        name="Producción",
        description=(
            "Estricto: incluso commits y HTTP GET pasan por humano. "
            "Diseñado para proyectos con clientes en producción."
        ),
        decisions={
            **_all("human_required"),
            # Solo lecturas internas siguen siendo auto.
            # (Nada en esta lista por defecto.)
        },
    ),
    BuiltinPolicy(
        slug="customer-external",
        name="Cliente Externo",
        description=(
            "Máxima fricción humana. Cualquier acción, incluida "
            "comunicación, queda gatekept por revisión humana."
        ),
        decisions=_all("human_required"),
    ),
)


# The preset applied to a project that has NO explicit ``human_approval_policy``
# (A8b). Was fail-open (policy None → gate never instantiated → everything auto);
# now a project without a policy inherits this preset's decisions. Overridable at
# runtime via the ``default_approval_policy_preset`` platform setting.
DEFAULT_APPROVAL_POLICY_PRESET = "development"

_POLICIES_BY_SLUG: dict[str, BuiltinPolicy] = {p.slug: p for p in BUILTIN_POLICIES}


def preset_decisions(slug: str) -> dict[str, str]:
    """The category→decision map of a built-in preset by slug (A8b).

    Every preset's ``decisions`` covers ALL canonical categories (built on
    ``_all(...)``), so the result is fully specified — no unlisted-category gap. An
    unknown slug falls back to the safe default preset (never fail-open to auto)."""
    policy = _POLICIES_BY_SLUG.get(slug) or _POLICIES_BY_SLUG[DEFAULT_APPROVAL_POLICY_PRESET]
    return dict(policy.decisions)


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------
_UPSERT_SQL = text(
    """
    INSERT INTO approval_policy_templates (
        id, tenant_id, name, description, categories, is_builtin
    )
    VALUES (
        :id, :tenant_id, :name, :description,
        CAST(:categories AS jsonb), true
    )
    ON CONFLICT (id) DO UPDATE SET
        name = EXCLUDED.name,
        description = EXCLUDED.description,
        categories = EXCLUDED.categories,
        updated_at = now()
    """
)


async def seed_builtin_approval_policies(session: AsyncSession) -> int:
    for policy in BUILTIN_POLICIES:
        await session.execute(
            _UPSERT_SQL,
            {
                "id": str(policy.id),
                "tenant_id": str(PLATFORM_TENANT_ID),
                "name": policy.name,
                "description": policy.description,
                "categories": json.dumps({"categories": policy.decisions}),
            },
        )
    return len(BUILTIN_POLICIES)
