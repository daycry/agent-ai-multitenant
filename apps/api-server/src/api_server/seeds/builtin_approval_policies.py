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
from typing import Any
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

#: Clave HERMANA de ``categories`` en el JSONB de una política: qué se hace con
#: una categoría que la política no lista (ADR 0153). Va fuera del mapa a
#: propósito — dentro sería una "categoría" más, y el contrato de
#: ``tests/unit/test_seeded_approval_policies_contract.py`` exige que ese mapa
#: contenga las 13 canónicas y NADA más.
UNLISTED_CATEGORY_KEY = "unlisted_category"


def _policy_id(slug: str) -> UUID:
    return uuid5(POLICY_SEED_NAMESPACE, f"policy:{slug}")


@dataclass(frozen=True)
class BuiltinPolicy:
    slug: str
    name: str
    description: str
    decisions: dict[str, str]  # category -> "auto" | "human_required"
    #: Qué decide este preset ante una categoría no listada (ADR 0153). Sin
    #: default a propósito: es la decisión que el ADR vino a hacer explícita,
    #: y un default aquí la volvería a dejar en manos del código.
    unlisted_category: str

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
            "Sin supervisión. El agente hace cualquier cosa sin preguntar. "
            "Solo para entornos aislados o demos internas. No describe tu "
            "infraestructura: describe cuánto se supervisa al agente."
        ),
        decisions=_all("auto"),
        # Un playground aislado no gana nada parando por una categoría futura.
        unlisted_category="auto",
    ),
    BuiltinPolicy(
        slug="development",
        name="Desarrollo",
        description=(
            "Supervisión ligera. El agente programa y usa sus integraciones sin "
            "preguntar; para antes de lo que SALE del proyecto (notificar a "
            "personas, mover datos a otra base de conocimiento). No describe tu "
            "infraestructura: describe cuánto se supervisa al agente."
        ),
        decisions={
            **_all("human_required"),
            "code_changes": "auto",
            "git_commit": "auto",
            "external_http_get": "auto",
            # `external_http_post` en AUTO, y no es un descuido — decisión del
            # operador el 2026-08-02, al revisar el ADR 0153.
            #
            # Esta categoría no cubre «una llamada HTTP»: cubre **todas las tools
            # MCP** del proyecto (`spec_approval_category` mapea `mcp_tool` y
            # `http_endpoint` aquí), y `import_mcp_tools` las da de alta con
            # `security_level="sandboxed"` por defecto. Gatearla en desarrollo
            # significaría que CADA integración del proyecto —Jira, GitHub, lo
            # que haya— pide aprobación desde el primer día. Eso no es
            # supervisión, es un muro; y un muro se rodea aprobando sin leer.
            #
            # La palanca correcta para apretar aquí NO es esta categoría: es
            # marcar `security_level="safe"` las tools MCP en las que se confía
            # (editable en la pantalla de Tools) y dejar gateadas las demás. Eso
            # es precisión por herramienta en vez de un interruptor de todo o
            # nada. Quien quiera el interruptor tiene el preset `production`.
            "external_http_post": "auto",
        },
        # ADR 0153: en desarrollo, parar por lo no listado llenaría una cola de
        # aprobaciones que nadie atiende, y eso enseña a aprobar sin leer — un
        # hábito que luego se lleva al proyecto donde sí importaba.
        unlisted_category="auto",
    ),
    BuiltinPolicy(
        slug="production",
        name="Producción",
        description=(
            "Supervisión estrecha. El agente para antes de casi todo, incluidos "
            "commits y lecturas HTTP. Para proyectos donde un error sale caro. "
            "No describe tu infraestructura: describe cuánto se supervisa al "
            "agente — un repo vacío puede llevar este preset y uno con clientes "
            "encima puede no llevarlo."
        ),
        decisions={
            **_all("human_required"),
            # Solo lecturas internas siguen siendo auto.
            # (Nada en esta lista por defecto.)
        },
        # Fail-CLOSED: bajo un preset estricto, lo que la política no nombra se
        # para. Es el punto entero del ADR 0153.
        unlisted_category="human_required",
    ),
    BuiltinPolicy(
        slug="customer-external",
        name="Cliente Externo",
        description=(
            "Supervisión total. Ninguna acción del agente ocurre sin que una "
            "persona la apruebe antes, incluida la comunicación. Para donde un "
            "agente podría escribirle a tu cliente. No describe tu "
            "infraestructura: describe cuánto se supervisa al agente."
        ),
        decisions=_all("human_required"),
        unlisted_category="human_required",
    ),
)


# The preset applied to a project that has NO explicit ``human_approval_policy``
# (A8b). Was fail-open (policy None → gate never instantiated → everything auto);
# now a project without a policy inherits this preset's decisions. Overridable at
# runtime via the ``default_approval_policy_preset`` platform setting.
DEFAULT_APPROVAL_POLICY_PRESET = "development"

_POLICIES_BY_SLUG: dict[str, BuiltinPolicy] = {p.slug: p for p in BUILTIN_POLICIES}


def _preset(slug: str) -> BuiltinPolicy:
    """El preset built-in por slug; un slug desconocido cae al default seguro."""
    return _POLICIES_BY_SLUG.get(slug) or _POLICIES_BY_SLUG[DEFAULT_APPROVAL_POLICY_PRESET]


def preset_decisions(slug: str) -> dict[str, str]:
    """The category→decision map of a built-in preset by slug (A8b).

    Every preset's ``decisions`` covers ALL canonical categories (built on
    ``_all(...)``), so the result is fully specified — no unlisted-category gap. An
    unknown slug falls back to the safe default preset (never fail-open to auto)."""
    return dict(_preset(slug).decisions)


def preset_unlisted_category(slug: str) -> str:
    """Qué decide este preset ante una categoría que no lista (ADR 0153)."""
    return _preset(slug).unlisted_category


def preset_policy(slug: str) -> dict[str, Any]:
    """La política COMPLETA de un preset, en la forma que vive en la BD.

    Es decir ``{"preset", "categories", "unlisted_category"}``: exactamente lo
    que debe acabar en ``projects.human_approval_policy``. Existe para que nadie
    vuelva a escribir un mapa de categorías a mano —así nació la clave fantasma
    ``external_http``, que no gateaba nada porque no es canónica— y para que la
    migración de datos del ADR 0153 y los esqueletos de las plantillas de
    proyecto compartan una sola fuente.
    """
    policy = _preset(slug)
    return {
        "preset": policy.slug,
        "categories": dict(policy.decisions),
        UNLISTED_CATEGORY_KEY: policy.unlisted_category,
    }


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------
_UPSERT_SQL = text("""
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
    """)


async def seed_builtin_approval_policies(session: AsyncSession) -> int:
    for policy in BUILTIN_POLICIES:
        await session.execute(
            _UPSERT_SQL,
            {
                "id": str(policy.id),
                "tenant_id": str(PLATFORM_TENANT_ID),
                "name": policy.name,
                "description": policy.description,
                "categories": json.dumps(
                    {
                        "categories": policy.decisions,
                        UNLISTED_CATEGORY_KEY: policy.unlisted_category,
                    }
                ),
            },
        )
    return len(BUILTIN_POLICIES)
