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
    # PROJ-01/P1-05 (auditoría 2026-07-17): toolchain del stack. Sin esto,
    # adoptar la plantilla producía proyectos con stack_exec deny-all (el
    # agente no podía correr ni su test runner) y sin runtime por defecto.
    allowed_commands: tuple[str, ...] = ()
    default_runtime_template: str | None = None
    allowed_domains: tuple[str, ...] = ()

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
        worker_config={"assignment_policy": "skill_match"},
        repository_config={"language": "python", "framework": "fastapi", "license": "MIT"},
        human_approval_policy=_POLICY_DEV_SKELETON,
        default_kb_grants=(
            "python-fastapi-conventions",
            "api-rest-guidelines",
            "postgresql-best-practices",
        ),
        allowed_commands=("python", "pip", "pytest", "ruff", "black", "alembic"),
        default_runtime_template="python-pytest",
        allowed_domains=("pypi.org", "files.pythonhosted.org"),
    ),
    BuiltinProjectTemplate(
        slug="webapp",
        name="Plantilla: Webapp Full-Stack",
        description=(
            "Webapp con backend Python + frontend Next.js. Incluye autenticación "
            "básica, dashboard inicial y pipeline de despliegue."
        ),
        team_slug="full-stack-web",
        worker_config={"assignment_policy": "skill_match"},
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
        allowed_commands=("python", "pip", "pytest", "node", "npm", "npx"),
        default_runtime_template="python-pytest",
        allowed_domains=("pypi.org", "files.pythonhosted.org", "registry.npmjs.org"),
    ),
    BuiltinProjectTemplate(
        slug="data-pipeline",
        name="Plantilla: Data Pipeline",
        description=(
            "Pipeline ETL/ELT con orquestación (Prefect/Airflow), almacenamiento "
            "intermedio y tests de calidad de datos."
        ),
        team_slug="data",
        worker_config={"assignment_policy": "skill_match"},
        repository_config={"language": "python", "framework": "prefect"},
        human_approval_policy=_POLICY_DEV_SKELETON,
        default_kb_grants=("postgresql-best-practices",),
        allowed_commands=("python", "pip", "pytest"),
        default_runtime_template="python-pytest",
        allowed_domains=("pypi.org", "files.pythonhosted.org"),
    ),
    BuiltinProjectTemplate(
        slug="legacy-migration",
        name="Plantilla: Migración Legacy",
        description=(
            "Migración progresiva de un sistema legado: análisis, strangler fig, "
            "tests de regresión y plan de switchover."
        ),
        team_slug="backend-api",
        worker_config={"assignment_policy": "skill_match"},
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
        allowed_commands=("python", "pip", "pytest", "php", "composer", "phpunit"),
        default_runtime_template="python-pytest",
        allowed_domains=("pypi.org", "files.pythonhosted.org", "packagist.org"),
    ),
    BuiltinProjectTemplate(
        slug="research-spec",
        name="Plantilla: Investigación + Especificación",
        description=(
            "Proyecto de investigación técnica: revisión literatura, comparación "
            "de opciones, redacción de ADR/RFC y prototipo mínimo."
        ),
        team_slug="research-spec",
        worker_config={"assignment_policy": "skill_match"},
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
        worker_config={"assignment_policy": "skill_match"},
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
        allowed_commands=("python", "pip", "pytest", "docker", "terraform", "ansible"),
        default_runtime_template="python-pytest",
        allowed_domains=("pypi.org", "files.pythonhosted.org"),
    ),
    BuiltinProjectTemplate(
        slug="e2e-test-suite",
        name="Plantilla: Suite E2E",
        description=(
            "Suite de tests E2E con Playwright/Cypress, fixtures de datos, "
            "reporting y pipeline de regresión por PR."
        ),
        team_slug="full-stack-web",
        worker_config={"assignment_policy": "skill_match"},
        repository_config={"language": "typescript", "framework": "playwright"},
        human_approval_policy=_POLICY_DEV_SKELETON,
        default_kb_grants=("react-nextjs-conventions", "node-express-conventions"),
        allowed_commands=("node", "npm", "npx", "playwright"),
        default_runtime_template="node-playwright",
        allowed_domains=("registry.npmjs.org", "playwright.azureedge.net"),
    ),
    BuiltinProjectTemplate(
        slug="doc-modernization",
        name="Plantilla: Modernización de Documentación",
        description=(
            "Reorganización y reescritura de docs: estructura canónica de 7 "
            "carpetas, ADRs, runbooks, diagramas Mermaid."
        ),
        team_slug="research-spec",
        worker_config={"assignment_policy": "skill_match"},
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
        allowed_commands, default_runtime_template, allowed_domains,
        is_template
    )
    VALUES (
        :id, :tenant_id, :name, :description, 'active', :team_id,
        '[]'::jsonb, '[]'::jsonb, CAST(:worker_config AS jsonb),
        CAST(:repository_config AS jsonb),
        CAST(:human_approval_policy AS jsonb),
        :default_kb_grants,
        :allowed_commands, :default_runtime_template, :allowed_domains,
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
        allowed_commands = EXCLUDED.allowed_commands,
        default_runtime_template = EXCLUDED.default_runtime_template,
        allowed_domains = EXCLUDED.allowed_domains,
        updated_at = now()
    """
)


async def upsert_project_template(session: AsyncSession, tpl: BuiltinProjectTemplate) -> None:
    """Upsert one :class:`BuiltinProjectTemplate`. Idempotent.

    Extracted so other built-in seeders (e.g. the CodeIgniter 4 app
    template in ``ci4_team.py``) can reuse the exact same SQL without
    duplicating it, and without adding their template to
    ``BUILTIN_PROJECT_TEMPLATES`` (whose count the seed test pins). The
    referenced team must already be seeded (FK on ``projects.team_id``).
    """
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
            "allowed_commands": list(tpl.allowed_commands),
            "default_runtime_template": tpl.default_runtime_template,
            "allowed_domains": list(tpl.allowed_domains),
        },
    )


async def seed_builtin_project_templates(session: AsyncSession) -> int:
    for tpl in BUILTIN_PROJECT_TEMPLATES:
        await upsert_project_template(session, tpl)
    return len(BUILTIN_PROJECT_TEMPLATES)
