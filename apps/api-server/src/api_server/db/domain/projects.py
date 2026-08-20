"""Agregado de proyecto: el contenedor de trabajo.

`Project` es la tabla ancha del dominio --35 columnas: presupuesto, repositorio,
politica de aprobacion humana, guardrails, servidores MCP, KB de RAG y
configuracion de worker--, y por eso vive sola en su modulo.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
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


# =============================================================================
# Project
# =============================================================================
class Project(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "projects"
    __table_args__ = (
        Index(
            "ix_projects_tenant_status",
            "tenant_id",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_projects_is_template",
            "is_template",
            postgresql_where=text("is_template = true"),
        ),
        # Índice de soporte de la FK `team_id -> teams.id ON DELETE SET NULL`
        # (migración 0031). Sin él, borrar un team obliga a escanear `projects`
        # entera buscando hijos. Parcial sobre las filas vivas y con team: los
        # proyectos sin team no aportan nada al índice.
        Index(
            "ix_projects_team_id",
            "team_id",
            postgresql_where=text("team_id IS NOT NULL AND deleted_at IS NULL"),
        ),
        # Backstop del layout de bare repos `/repos/{tenant}/{project_slug}`
        # (migración 0114): dos proyectos VIVOS del mismo tenant con el mismo
        # slug operarían sobre el MISMO repo git. El router ya deduplica al
        # crear; este único parcial cubre las carreras y las escrituras que no
        # pasan por el router. Parcial porque un slug soft-borrado sí se reusa.
        Index(
            "uq_projects_tenant_slug_live",
            "tenant_id",
            "slug",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint(
            "budget_amount IS NULL OR budget_amount >= 0",
            name="ck_projects_budget_non_negative",
        ),
        # human_task_review_mode value set (Plan 16 task_16_11). Mirrors the
        # HumanTaskReviewMode StrEnum; DB-enforced by migration 0073, same
        # shape as ck_agents_agent_type.
        CheckConstraint(
            "human_task_review_mode IN ('auto_approve', 'peer_human_reviewer')",
            name="ck_projects_human_task_review_mode",
        ),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # prod-18 / ADR 0085: stable kebab slug for the project's git worktree path
    # (`BareRepoLayout`). Generated once at creation (api_server.slug.slugify), never
    # changes when `name` does, so the worktree/bare repo is not orphaned on rename.
    # Nullable: backfilled by migration 0099; a NULL slug falls back to ephemeral
    # tmpfs in execution (no worktree) — safe degradation.
    slug: Mapped[str | None] = mapped_column(String(80), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'active'"))

    # Team assignment is optional at creation -- some projects are bootstrapped
    # before a team is decided. Nullable FK; ON DELETE SET NULL keeps the
    # project alive if the team is dissolved.
    team_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="SET NULL"),
        nullable=True,
    )

    # JSONB placeholders for things that get their own tables in later
    # plans -- MCP server registry, RAG KBs, repo config, approval policy.
    mcp_servers: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    rag_knowledge_bases: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    # Plan 06.9 task_06_9_07: KBs (by slug) that the wizard should
    # auto-grant when a tenant creates a project from this template.
    # Only meaningful on `is_template=true` rows; ignored otherwise.
    # `ARRAY(Text)`, no `ARRAY(String)`: la migración 0027 la creó como
    # `sa.ARRAY(sa.Text())` y en PostgreSQL `text[]` y `varchar[]` son tipos
    # DISTINTOS aunque se comporten igual. Declararla con `String` dejaba a
    # `alembic check` proponiendo un `ALTER COLUMN ... TYPE varchar[]` sobre la
    # tabla de proyectos cada vez que alguien autogenerase una migración.
    default_kb_grants: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    worker_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Default de modelo del PROYECTO (Ola A / ADR 0055): nivel de la cadena de
    # herencia plataforma → proyecto → equipo → agente. JSONB ``{}`` = no fija
    # modelo. Distinto de ``worker_config`` (assignment_policy, etc.).
    model_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Modelo del CHAT del proyecto, separado del de ejecución (`model_config`). El
    # chat de planificación es interactivo: conviene un modelo más rápido/ligero que
    # el que los agentes usan para ejecutar tareas. JSONB ``{}`` = el chat hereda el
    # `model_config` de ejecución (cadena ADR 0065). Gana sobre el de equipo.
    chat_model_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # prod-06 task_prod06_budget_02 (workers-10): per-run budget OVERRIDE for this
    # project's executions. A subset of the agent-runtime ``Budgets`` keys
    # (max_iterations / max_tokens / max_cost_usd / max_wall_clock_s /
    # max_tool_calls). NULL = no override → the platform default applies. The
    # dispatcher clamps both project + platform values to the runtime ceiling
    # (``EXECUTION_BUDGET_CEILING``), so an override can tighten but never loosen.
    execution_budgets: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    repository_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Config git tipada del proyecto (ADR 0072): {provider, remote_url,
    # default_branch, auth_mode}. NULL = sin remoto (solo bare local). El SECRETO
    # (PAT/clave SSH) NO va aquí — vive en Vault (projects/{id}/git).
    git_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    human_approval_policy: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # ADR 0102 D3: capa PROYECTO de los guardrails declarativos. El worker la
    # fusiona con la capa plataforma (resolve_config, locked gana) y transporta
    # el resultado al runtime en spec["guardrails"]. NULL = sin capa proyecto.
    guardrails_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Plan 06.16 task_06_16_01: polyglot tool catalog. `allowed_commands`
    # is the per-project deny-by-default allowlist of program *basenames*
    # (`php`, `composer`, `vendor/bin/phpunit`, `pest`, `npm`, …) the
    # `shell_exec` builtin may run; empty `[]` = nothing runs (deny-all).
    # TEXT[] (not JSONB) — membership-only semantics, same shape as
    # `default_kb_grants`. `default_runtime_template` names the stack's
    # runtime template id (`php-phpunit`, `node-jest`, …) the `run_*`
    # tools resolve against; NULL = keep each tool's current default
    # (backward-compatible).
    # `ARRAY(Text)` + `Text`: así las creó la migración 0072 (`sa.ARRAY(sa.Text())`
    # y `sa.Text()`), y es lo que dice el comentario de arriba. El cap de 64
    # caracteres de `default_runtime_template` NUNCA existió en la BD; lo aplica
    # el borde HTTP (`schemas/projects.py`, `max_length=64` + validador contra el
    # catálogo de runtime templates), que es donde se puede devolver un 422.
    # Declarar `String(64)` aquí hacía que autogenerate propusiera estrechar una
    # columna de producción a 64 caracteres.
    allowed_commands: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    default_runtime_template: Mapped[str | None] = mapped_column(Text, nullable=True)

    # prod-12 Fase B (gap4-2): per-project deny-by-default allowlist of FQDNs
    # the HTTP tools (`http_request` + http_endpoint) may reach; `[]` = las
    # tools de red no alcanzan nada. Entries are validated server-side
    # (task_prod12_ssrf_03: FQDN en minusculas, sin esquema/puerto, nunca IPs
    # literales/localhost/hosts internos del compose) y el runtime aplica
    # ADEMAS el ssrf_guard por-resolucion (Fase A) — defensa en profundidad.
    allowed_domains: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'::text[]")
    )

    # ADR 0128 fase 2: política OPCIONAL rol→tool de las tools MCP del proyecto.
    # Mapea el nombre de una tool MCP (`<server>.<tool>`) → los roles de agente
    # autorizados a usarla. Un tool SIN entrada queda abierto a todos los roles
    # (default). `{}` = sin política (todo agente del proyecto ve toda tool MCP del
    # proyecto). No afecta a builtins/tools de rol (siguen por-agente).
    mcp_tool_roles: Mapped[dict[str, list[str]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # Soft-FK to the Vault entry that holds the project's secrets. Vault
    # is an external system so no DB-level FK.
    secrets_vault_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    # --- Budget (see spec §28.7 for tenant-vs-project budget interaction) ---
    # Numeric(14,2) maps to Decimal in Python so the schema's Decimal
    # type stays consistent end-to-end (no float rounding on currency).
    budget_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=14, scale=2), nullable=True
    )
    budget_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    budget_period: Mapped[str | None] = mapped_column(String(16), nullable=True)
    budget_period_start_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    budget_period_length_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    paused_by_budget: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))

    # --- Human-task review mode (Plan 16 task_16_11) ----------------------
    # How a human task's deliverable is reviewed once submitted (task_16_09).
    # Stored as the :class:`HumanTaskReviewMode` value (TEXT) — same
    # string-backed-enum convention as `status`. DEFAULT 'auto_approve' so
    # existing projects keep the MVP behaviour (submit -> in_review -> done,
    # no extra review step). DB-constrained by ck_projects_human_task_review_mode.
    # `Text`, como la creó la migración 0073 y como dice el comentario de arriba.
    # El value set NO lo guarda la longitud sino
    # `ck_projects_human_task_review_mode` (arriba), que es la restricción de
    # verdad; `String(32)` sólo servía para que autogenerate propusiera un
    # `ALTER COLUMN` inútil.
    human_task_review_mode: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'auto_approve'")
    )

    # --- Budget: fold human cost in? (Plan 16 task_16_12) -----------------
    # Human cost (rate * hours from human_work_sessions) is ALWAYS imputed to
    # the plan/project + segmented in the 13.7 dashboard. This flag decides
    # whether it ALSO counts toward this project's BUDGET (consumption +
    # threshold alerts + auto-pause). DEFAULT false = current behaviour (only
    # the canonical-USD AI cost counts); true folds the project's human cost
    # (converted to USD) into the consumption the evaluator compares vs the
    # cap. DB column added by migration 0074.
    budget_includes_human_cost: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )

    # Catalog marker -- when true the row is a template blueprint owned
    # by the platform tenant, visible cross-tenant via RLS but never the
    # target of writes from a tenant session.
    is_template: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
