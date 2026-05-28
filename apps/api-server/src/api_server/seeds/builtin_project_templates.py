"""Built-in project templates (task_01_13).

Eight pre-cooked blueprints across the project archetypes a tenant
typically reaches for. Each carries a reference to one of the 5
built-in team templates (task_01_12) plus skeleton config (mcp,
rag KB list, repo, approval policy) that the wizard in task_01_21
will use as defaults.

Project templates are NEVER linked -- when a tenant adopts one, the
fork logic (task_01_15+) deep-copies the row into the tenant. Linked
mode wouldn't make sense for projects (the resulting project has its
own lifecycle, budget, status).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.seeds import PLATFORM_TENANT_ID
from api_server.seeds.builtin_teams import _team_id as builtin_team_id

PROJECT_TEMPLATE_NAMESPACE: UUID = UUID("00000000-0000-0000-0000-000000000014")


def _project_template_id(slug: str) -> UUID:
    return uuid5(PROJECT_TEMPLATE_NAMESPACE, f"project_template:{slug}")


@dataclass(frozen=True)
class BuiltinProjectTemplate:
    slug: str
    name: str
    description: str
    team_slug: str
    worker_config: dict[str, Any] = field(default_factory=dict)
    repository_config: dict[str, Any] | None = None
    human_approval_policy: dict[str, Any] | None = None
    # Plan 06.9 task_06_9_07: KBs (por slug del catálogo built-in) que
    # se grantean automáticamente al adoptar esta plantilla. Vacío =
    # ningún grant por defecto.
    default_kb_grants: tuple[str, ...] = ()

    @property
    def id(self) -> UUID:
        return _project_template_id(self.slug)


# Default approval policy skeleton -- task_01_14 will replace these
# inline dicts with references to the proper policy templates.
_POLICY_DEV_SKELETON: dict[str, Any] = {
    "preset": "development",
    "categories": {
        "code_changes": "auto",
        "git_push": "human_required",
        "external_http": "human_required",
        "secrets_access": "human_required",
    },
}


BUILTIN_PROJECT_TEMPLATES: tuple[BuiltinProjectTemplate, ...] = (
    BuiltinProjectTemplate(
        slug="api-rest",
        name="Plantilla: API REST",
        description=(
            "Servicio REST en Python (FastAPI + SQLAlchemy async). Incluye CI, "
            "tests pytest, contenedor base y plantilla de OpenAPI."
        ),
        team_slug="backend-api",
        worker_config={
            "min_workers": 1,
            "max_workers": 4,
            "cpu_per_worker": 1.0,
            "ram_per_worker_mb": 1024,
        },
        repository_config={"language": "python", "framework": "fastapi", "license": "MIT"},
        human_approval_policy=_POLICY_DEV_SKELETON,
        default_kb_grants=(
            "python-fastapi-conventions",
            "api-rest-guidelines",
            "postgresql-best-practices",
        ),
    ),
    BuiltinProjectTemplate(
        slug="webapp",
        name="Plantilla: Webapp Full-Stack",
        description=(
            "Webapp con backend Python + frontend Next.js. Incluye autenticación "
            "básica, dashboard inicial y pipeline de despliegue."
        ),
        team_slug="full-stack-web",
        worker_config={
            "min_workers": 2,
            "max_workers": 6,
            "cpu_per_worker": 1.0,
            "ram_per_worker_mb": 1536,
        },
        repository_config={
            "language": "python+typescript",
            "frontend": "next.js",
            "backend": "fastapi",
        },
        human_approval_policy=_POLICY_DEV_SKELETON,
        default_kb_grants=(
            "python-fastapi-conventions",
            "react-nextjs-conventions",
            "api-rest-guidelines",
            "postgresql-best-practices",
        ),
    ),
    BuiltinProjectTemplate(
        slug="data-pipeline",
        name="Plantilla: Data Pipeline",
        description=(
            "Pipeline ETL/ELT con orquestación (Prefect/Airflow), almacenamiento "
            "intermedio y tests de calidad de datos."
        ),
        team_slug="data",
        worker_config={
            "min_workers": 1,
            "max_workers": 3,
            "cpu_per_worker": 2.0,
            "ram_per_worker_mb": 4096,
        },
        repository_config={"language": "python", "framework": "prefect"},
        human_approval_policy=_POLICY_DEV_SKELETON,
        default_kb_grants=("postgresql-best-practices",),
    ),
    BuiltinProjectTemplate(
        slug="legacy-migration",
        name="Plantilla: Migración Legacy",
        description=(
            "Migración progresiva de un sistema legado: análisis, strangler fig, "
            "tests de regresión y plan de switchover."
        ),
        team_slug="backend-api",
        worker_config={
            "min_workers": 1,
            "max_workers": 4,
            "cpu_per_worker": 1.0,
            "ram_per_worker_mb": 1024,
        },
        repository_config={"language": "polyglot", "notes": "legacy + target stacks"},
        human_approval_policy={
            **_POLICY_DEV_SKELETON,
            "preset": "production",
            "categories": {
                **_POLICY_DEV_SKELETON["categories"],
                "data_migration": "human_required",
                "production_deploy": "human_required",
            },
        },
        # Stack polyglot: ofrecemos las dos referencias de stack más
        # comunes en migraciones (Python destino + PHP origen) +
        # principios generales.
        default_kb_grants=(
            "api-rest-guidelines",
            "python-fastapi-conventions",
            "php-symfony-conventions",
            "postgresql-best-practices",
        ),
    ),
    BuiltinProjectTemplate(
        slug="research-spec",
        name="Plantilla: Investigación + Especificación",
        description=(
            "Proyecto de investigación técnica: revisión literatura, comparación "
            "de opciones, redacción de ADR/RFC y prototipo mínimo."
        ),
        team_slug="research-spec",
        worker_config={"min_workers": 1, "max_workers": 2},
        repository_config=None,
        human_approval_policy={"preset": "sandbox", "categories": {"all": "auto"}},
    ),
    BuiltinProjectTemplate(
        slug="devops-bootstrap",
        name="Plantilla: DevOps Bootstrap",
        description=(
            "Bootstrap de plataforma: Docker, CI/CD, observabilidad (OTEL + "
            "Prometheus + Grafana), secretos en Vault, runbooks iniciales."
        ),
        team_slug="devops-platform",
        worker_config={
            "min_workers": 1,
            "max_workers": 3,
            "cpu_per_worker": 1.0,
            "ram_per_worker_mb": 1024,
        },
        repository_config={"language": "polyglot", "iac": "terraform+ansible"},
        human_approval_policy={
            **_POLICY_DEV_SKELETON,
            "preset": "production",
            "categories": {
                **_POLICY_DEV_SKELETON["categories"],
                "infra_provision": "human_required",
                "secret_rotation": "human_required",
            },
        },
    ),
    BuiltinProjectTemplate(
        slug="e2e-test-suite",
        name="Plantilla: Suite E2E",
        description=(
            "Suite de tests E2E con Playwright/Cypress, fixtures de datos, "
            "reporting y pipeline de regresión por PR."
        ),
        team_slug="full-stack-web",
        worker_config={"min_workers": 1, "max_workers": 3},
        repository_config={"language": "typescript", "framework": "playwright"},
        human_approval_policy=_POLICY_DEV_SKELETON,
        default_kb_grants=("react-nextjs-conventions", "node-express-conventions"),
    ),
    BuiltinProjectTemplate(
        slug="doc-modernization",
        name="Plantilla: Modernización de Documentación",
        description=(
            "Reorganización y reescritura de docs: estructura canónica de 7 "
            "carpetas, ADRs, runbooks, diagramas Mermaid."
        ),
        team_slug="research-spec",
        worker_config={"min_workers": 1, "max_workers": 2},
        repository_config={"language": "markdown", "diagrams": "mermaid"},
        human_approval_policy={"preset": "sandbox", "categories": {"all": "auto"}},
    ),
)


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------
_UPSERT_SQL = text(
    """
    INSERT INTO projects (
        id, tenant_id, name, description, status, team_id,
        mcp_servers, rag_knowledge_bases, worker_config,
        repository_config, human_approval_policy,
        default_kb_grants,
        is_template
    )
    VALUES (
        :id, :tenant_id, :name, :description, 'active', :team_id,
        '[]'::jsonb, '[]'::jsonb, CAST(:worker_config AS jsonb),
        CAST(:repository_config AS jsonb),
        CAST(:human_approval_policy AS jsonb),
        :default_kb_grants,
        true
    )
    ON CONFLICT (id) DO UPDATE SET
        name = EXCLUDED.name,
        description = EXCLUDED.description,
        team_id = EXCLUDED.team_id,
        worker_config = EXCLUDED.worker_config,
        repository_config = EXCLUDED.repository_config,
        human_approval_policy = EXCLUDED.human_approval_policy,
        default_kb_grants = EXCLUDED.default_kb_grants,
        updated_at = now()
    """
)


async def seed_builtin_project_templates(session: AsyncSession) -> int:
    for tpl in BUILTIN_PROJECT_TEMPLATES:
        await session.execute(
            _UPSERT_SQL,
            {
                "id": str(tpl.id),
                "tenant_id": str(PLATFORM_TENANT_ID),
                "name": tpl.name,
                "description": tpl.description,
                "team_id": str(builtin_team_id(tpl.team_slug)),
                "worker_config": json.dumps(tpl.worker_config),
                "repository_config": (
                    json.dumps(tpl.repository_config) if tpl.repository_config is not None else None
                ),
                "human_approval_policy": (
                    json.dumps(tpl.human_approval_policy)
                    if tpl.human_approval_policy is not None
                    else None
                ),
                # PostgreSQL TEXT[] — asyncpg encodes Python list as
                # the SQL array literal natively.
                "default_kb_grants": list(tpl.default_kb_grants),
            },
        )
    return len(BUILTIN_PROJECT_TEMPLATES)
