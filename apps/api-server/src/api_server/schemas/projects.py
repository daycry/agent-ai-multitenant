"""Pydantic schemas for /projects endpoints (task_01_07).

Projects are the heaviest tenant-scoped entity: team assignment,
mcp / rag placeholders (typed loosely as JSONB until their own tables
land in Plans 04-05), repository config, human-approval policy
(structure firmed up by task_01_14), and budget controls (spec §28.7).

`paused_by_budget` is server-managed -- the budget evaluator flips it;
the API does not accept it from the request.
"""

from __future__ import annotations

import ipaddress
import json
import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from shared_test_runtimes import CATALOG

from api_server.db.domain import BudgetPeriod, HumanTaskReviewMode, Project, ProjectStatus
from api_server.mcp.config import validate_mcp_servers_payload

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

# Free-form JSONB config blobs (`worker_config`, `repository_config`,
# `human_approval_policy`, `rag_knowledge_bases`) are typed loosely until
# their own validated schemas land in Plans 02-05. We do NOT lock their
# shape here (that would pre-empt unbuilt roadmap), but we DO cap their
# serialized size so a client can't stuff megabytes of arbitrary JSON into
# a project row that later gets folded into orchestrator context
# (api-routers-validation-6). 64 KiB is generous for config.
_MAX_JSON_CONFIG_BYTES = 65536

# Plan 06.16 task_06_16_01: the per-project shell_exec allowlist. We cap
# the count + each entry's length and normalise (strip + drop blanks +
# de-dup, preserving order) so the deny-by-default allowlist stays a tidy
# list of program basenames the operator picked from the UI presets/chips
# — not a free-form dumping ground.
_MAX_ALLOWED_COMMANDS = 100
_MAX_COMMAND_LENGTH = 128


# --- allowed_domains (prod-12 task_prod12_ssrf_03) ---------------------------
# Deny-by-default FQDN allowlist for the agent HTTP tools. Server-side
# validation is the FIRST layer; the runtime's ssrf_guard re-validates every
# resolution (defence in depth). Entries are normalised (lowercase, no scheme /
# port / path) before persisting so the runtime's textual match is exact.
_MAX_ALLOWED_DOMAINS = 100
_MAX_DOMAIN_LENGTH = 253
_DOMAIN_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
# Hostnames internos del compose (y equivalentes docker) que jamás pueden ser
# destino de una tool de agente — el mensaje explícito ayuda al operador.
_COMPOSE_INTERNAL_HOSTS = frozenset(
    {
        "api-server",
        "orchestrator",
        "workers",
        "postgres",
        "redis",
        "vault",
        "minio",
        "clamav",
        "docling-serve",
        "ollama",
        "egress-proxy",
        "registry-proxy",
        "prometheus",
        "grafana",
        "loki",
        "localhost",
        "host.docker.internal",
        "gateway.docker.internal",
    }
)


def _strip_to_bare_domain(raw: str) -> str:
    """Lowercase + drop scheme/path/port (IPv6 brackets first) + trailing dot."""
    domain = raw.strip().lower()
    if "://" in domain:
        domain = domain.split("://", 1)[1]
    domain = domain.split("/", 1)[0]
    if domain.startswith("["):
        domain = domain.strip("[]").split("]", 1)[0]
    elif domain.count(":") == 1:
        domain = domain.split(":", 1)[0]
    return domain.rstrip(".")


def _validate_domain_entry(raw: str, domain: str) -> None:
    """Reject an entry that can never be a legitimate agent destination."""
    if len(domain) > _MAX_DOMAIN_LENGTH:
        raise ValueError(f"allowed_domains entry too long ({len(domain)} chars)")
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        pass
    else:
        raise ValueError(
            f"allowed_domains: literal IP addresses are not allowed ({raw!r}); "
            "use a fully-qualified domain name"
        )
    if domain in _COMPOSE_INTERNAL_HOSTS or domain.endswith(".localhost"):
        raise ValueError(
            f"allowed_domains: {raw!r} is an internal platform host and can never "
            "be an agent tool destination"
        )
    labels = domain.split(".")
    if len(labels) < 2:
        raise ValueError(
            f"allowed_domains: {raw!r} is not a fully-qualified domain name "
            "(expected e.g. 'api.example.com')"
        )
    if not all(_DOMAIN_LABEL_RE.match(label) for label in labels):
        raise ValueError(f"allowed_domains: {raw!r} is not a valid domain name")


def _normalise_allowed_domains(value: list[str]) -> list[str]:
    """Normalise + validate the project's HTTP-tools domain allowlist.

    Per entry: lowercase, strip scheme/port/path; reject literal IPs,
    ``localhost``/compose-internal hostnames and non-FQDN names (no dot) with a
    clear operator-facing message. De-dup order-preserving; caps enforced.
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in value:
        domain = _strip_to_bare_domain(raw)
        if not domain:
            continue
        _validate_domain_entry(raw, domain)
        if domain in seen:
            continue
        seen.add(domain)
        out.append(domain)
    if len(out) > _MAX_ALLOWED_DOMAINS:
        raise ValueError(f"too many allowed_domains ({len(out)}); max {_MAX_ALLOWED_DOMAINS}")
    return out


def _normalise_allowed_commands(value: list[str]) -> list[str]:
    """Strip + drop blanks + de-dup (order-preserving), enforcing caps."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in value:
        cmd = raw.strip()
        if not cmd:
            continue
        if len(cmd) > _MAX_COMMAND_LENGTH:
            raise ValueError(
                f"allowed_commands entry too long ({len(cmd)} chars); " f"max {_MAX_COMMAND_LENGTH}"
            )
        if cmd in seen:
            continue
        seen.add(cmd)
        out.append(cmd)
    if len(out) > _MAX_ALLOWED_COMMANDS:
        raise ValueError(f"too many allowed_commands ({len(out)}); max {_MAX_ALLOWED_COMMANDS}")
    return out


def _validate_runtime_template(value: str | None) -> str | None:
    """Reject a `default_runtime_template` that is not in the catalog.

    Plan 06.18 task_06_18_08 (ADR 0051): the project's default runtime must
    be one of the curated templates in `shared_test_runtimes.CATALOG`,
    reusing the same guard that already lived only in `dep_cache.py` (422 if
    the runtime is unknown). `None` means "no default runtime" (the run_*
    tools fall back to per-tool defaults) and stays accepted.
    """
    if value is None:
        return None
    if value not in CATALOG:
        known = ", ".join(sorted(CATALOG))
        raise ValueError(f"unknown default_runtime_template {value!r}; known: {known}")
    return value


def _validate_execution_budgets(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Rechaza un presupuesto de ejecución que se iba a descartar en silencio.

    `resolve_execution_budgets` (budgets/envelope.py) tira sin decir nada las
    claves desconocidas y los valores no numéricos o ≤ 0. Como la API tampoco
    los validaba, un `max_wall_clock` sin la `_s`, o un `"mucho"` donde iba un
    número, devolvía 200 y el run seguía corriendo con el presupuesto de
    plataforma. El operador cree que ha capado el gasto de un proyecto y no ha
    capado nada, sin ninguna señal.

    Que un valor por encima del techo se recorte NO es un error: está
    documentado y es intencionado. Lo que aquí se rechaza es solo lo que el
    resolver iba a DESCARTAR — que es indistinguible de no haber escrito nada.
    """
    if value is None:
        return None
    from api_server.budgets.envelope import EXECUTION_BUDGET_CEILING

    problems: list[str] = []
    for key, raw in value.items():
        if key not in EXECUTION_BUDGET_CEILING:
            problems.append(f"{key!r} no es un presupuesto conocido")
        elif isinstance(raw, bool) or not isinstance(raw, int | float):
            # bool es subclase de int: `True` no puede colarse como cantidad.
            problems.append(f"{key!r} tiene que ser un número, no {type(raw).__name__}")
        elif raw <= 0:
            problems.append(f"{key!r} tiene que ser mayor que cero")
    if problems:
        known = ", ".join(sorted(EXECUTION_BUDGET_CEILING))
        raise ValueError(f"{'; '.join(problems)}. Presupuestos válidos: {known}")
    return value


def _validate_guardrails_config(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Rechaza una config de guardrails que el runtime no sabría leer.

    La capa de proyecto se fusiona con la de plataforma en
    `_resolve_guardrails_config` (workers/execution.py), y ahí un
    `GuardrailConfigError` se captura y degrada a `None` — el run cae al
    baseline del runtime. Es un degradado SEGURO: los runs siguen tamizados. Lo
    que no es aceptable es que sea MUDO: el operador configura sus guardrails,
    la API responde 200 y todos los runs de ese proyecto ignoran la config sin
    que nada se lo diga (solo un warning en el log del worker).

    Se valida con el MISMO parser que usa el worker, así que la API no puede
    aceptar nada que el worker vaya a rechazar después.
    """
    if value is None:
        return None
    from shared_guardrails.layers import LayerConfig

    try:
        LayerConfig.from_dict("project", value)
    except Exception as exc:  # GuardrailConfigError y cualquier fallo de forma
        raise ValueError(f"config de guardrails inválida: {exc}") from exc
    return value


def _check_json_config_size(value: Any, field_name: str) -> None:
    """Reject a free-form JSON config blob over `_MAX_JSON_CONFIG_BYTES`.

    `default=str` keeps it from blowing up on stray non-serializable
    values — we only care about an order-of-magnitude size guard here.
    """
    if value is None:
        return
    size = len(json.dumps(value, default=str).encode("utf-8"))
    if size > _MAX_JSON_CONFIG_BYTES:
        raise ValueError(
            f"{field_name} is too large ({size} bytes); " f"max {_MAX_JSON_CONFIG_BYTES} bytes"
        )


def _validate_budget_invariants(self: BaseModel) -> BaseModel:
    """`custom` budget period must carry start_day + length_days; any
    other period must NOT. Currency required if amount is set."""
    period = getattr(self, "budget_period", None)
    amount = getattr(self, "budget_amount", None)
    start_day = getattr(self, "budget_period_start_day", None)
    length = getattr(self, "budget_period_length_days", None)
    currency = getattr(self, "budget_currency", None)

    if period == BudgetPeriod.CUSTOM:
        if start_day is None or length is None:
            raise ValueError(
                "budget_period='custom' requires "
                "budget_period_start_day and budget_period_length_days"
            )
    elif period is not None and (start_day is not None or length is not None):
        raise ValueError(
            "budget_period_start_day / budget_period_length_days only "
            "apply when budget_period='custom'"
        )

    if amount is not None and currency is None:
        raise ValueError("budget_currency is required when budget_amount is set")
    return self


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
class ProjectCreateRequest(BaseModel):
    model_config = _BASE_CONFIG

    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    status: ProjectStatus = ProjectStatus.ACTIVE
    team_id: UUID | None = None

    # Plan 06.13 task_06_13_03: optional project template to adopt. When
    # set, the new project is pre-granted the template's
    # `default_kb_grants` (built-in KB slugs → kb_projects rows). Absent
    # = a plain project with no auto-grants (backward-compatible).
    template_id: UUID | None = None

    # Plan 06.17 task_06_17_14: the wizard sends this flag alongside
    # `template_id` to decide whether the template's `default_kb_grants`
    # are actually applied. `True` (default, backward-compatible) grants
    # the KBs; `False` adopts the template's shape (team/config inherited
    # by the wizard front-end) WITHOUT auto-granting any KB. Ignored when
    # `template_id` is absent ("proyecto en blanco" grants nothing anyway).
    apply_template_kb_grants: bool = True

    # Ola C / ADR 0068: si `True` Y hay `team_id`, al crear el proyecto se FORKEA
    # ese equipo (copia editable del tenant, agentes `project_local`) y el
    # proyecto apunta al fork en vez de al equipo (built-in) original. `False`
    # (default, retro-compatible) = referencia el equipo tal cual (linked).
    fork_team: bool = False

    mcp_servers: list[dict[str, Any]] = Field(default_factory=list)
    rag_knowledge_bases: list[dict[str, Any]] = Field(default_factory=list)
    worker_config: dict[str, Any] = Field(default_factory=dict)
    repository_config: dict[str, Any] | None = None
    human_approval_policy: dict[str, Any] | None = None
    # `secrets_vault_id` NO se acepta (task_wf_35): la columna está DEPRECATED
    # desde P1-04 y no tiene ni un lector en todo el sistema. Aceptarlo era el
    # mismo no-op mudo que se acaba de tapar en `execution_budgets` — el
    # operador rellena un campo y no pasa nada. Sigue en la RESPUESTA (siempre
    # `null`) porque retirarlo de ahí rompería los SDK generados sin darle nada
    # a nadie; el día que se borre la columna se retira también de la respuesta.

    # Plan 06.16 task_06_16_01: shell_exec allowlist (deny-by-default —
    # empty list runs nothing) + the stack's default runtime template.
    allowed_commands: list[str] = Field(default_factory=list)
    default_runtime_template: str | None = Field(default=None, min_length=1, max_length=64)

    # prod-12 Fase B: FQDN allowlist de las tools HTTP del agente
    # (deny-by-default — lista vacía = sin red). Validada server-side
    # (task_prod12_ssrf_03) además del ssrf_guard por-resolución del runtime.
    allowed_domains: list[str] = Field(default_factory=list)

    # ADR 0128 fase 2: política OPCIONAL rol→tool de las MCP del proyecto. Mapea
    # nombre de tool MCP (`<server>.<tool>`) → roles de agente autorizados. `{}`
    # (default) = sin política: todo agente del proyecto ve toda tool MCP del
    # proyecto. Un tool con entrada se restringe a esos roles.
    mcp_tool_roles: dict[str, list[str]] = Field(default_factory=dict)

    # Plan 16 task_16_11: how a human task's deliverable is reviewed once
    # submitted. Default auto_approve (submit -> done, no extra review step).
    human_task_review_mode: HumanTaskReviewMode = HumanTaskReviewMode.AUTO_APPROVE

    budget_amount: Decimal | None = Field(default=None, ge=0)
    budget_currency: str | None = Field(default=None, min_length=3, max_length=3)
    budget_period: BudgetPeriod | None = None
    budget_period_start_day: int | None = Field(default=None, ge=1, le=31)
    budget_period_length_days: int | None = Field(default=None, ge=1, le=366)

    @field_validator("mcp_servers", mode="after")
    @classmethod
    def _validate_mcp_servers(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return validate_mcp_servers_payload(value)

    @field_validator("allowed_commands", mode="after")
    @classmethod
    def _validate_allowed_commands(cls, value: list[str]) -> list[str]:
        return _normalise_allowed_commands(value)

    @field_validator("allowed_domains", mode="after")
    @classmethod
    def _validate_allowed_domains(cls, value: list[str]) -> list[str]:
        return _normalise_allowed_domains(value)

    @field_validator("default_runtime_template", mode="after")
    @classmethod
    def _validate_runtime_template(cls, value: str | None) -> str | None:
        return _validate_runtime_template(value)

    @field_validator(
        "rag_knowledge_bases",
        "worker_config",
        "repository_config",
        "human_approval_policy",
        mode="after",
    )
    @classmethod
    def _cap_json_config(cls, value: Any, info: Any) -> Any:
        _check_json_config_size(value, info.field_name)
        return value

    @model_validator(mode="after")
    def _budget_invariants(self) -> ProjectCreateRequest:
        return _validate_budget_invariants(self)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------
class ProjectUpdateRequest(BaseModel):
    model_config = _BASE_CONFIG

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    status: ProjectStatus | None = None
    team_id: UUID | None = None

    mcp_servers: list[dict[str, Any]] | None = None
    # ADR 0128 fase 2: política rol→tool de las MCP del proyecto. None = sin
    # cambio (PATCH); `{}` la borra (vuelve a "todos los agentes, todas las MCP").
    mcp_tool_roles: dict[str, list[str]] | None = None
    # DEPRECATED (P1-04): sin lectores (lo real es `kb_projects`).
    rag_knowledge_bases: list[dict[str, Any]] | None = None
    worker_config: dict[str, Any] | None = None
    repository_config: dict[str, Any] | None = None
    human_approval_policy: dict[str, Any] | None = None
    # P1-03: presupuestos de ejecución (clamp en dispatch) y guardrails del
    # proyecto (merge en el worker) — el PUT exige tenant_admin.
    execution_budgets: dict[str, Any] | None = None
    guardrails_config: dict[str, Any] | None = None

    # Plan 06.16 task_06_16_01. None = unchanged (PATCH-style partial
    # update — `apply_partial_update` uses `exclude_unset`). An explicit
    # `[]` clears the allowlist back to deny-all; `default_runtime_template:
    # null` clears the runtime back to per-tool defaults.
    allowed_commands: list[str] | None = None
    # prod-12 Fase B: mismo contrato PATCH — `[]` explícito = deny-all.
    allowed_domains: list[str] | None = None
    default_runtime_template: str | None = Field(default=None, min_length=1, max_length=64)

    # Plan 16 task_16_11. None = unchanged (PATCH-style partial update).
    human_task_review_mode: HumanTaskReviewMode | None = None

    budget_amount: Decimal | None = Field(default=None, ge=0)
    budget_currency: str | None = Field(default=None, min_length=3, max_length=3)
    budget_period: BudgetPeriod | None = None
    budget_period_start_day: int | None = Field(default=None, ge=1, le=31)
    budget_period_length_days: int | None = Field(default=None, ge=1, le=366)

    # Modelo por defecto del proyecto (Ola A / ADR 0065). Alias JSON `model_config`
    # (igual que en Agent/Team). `{}` = no fija modelo (hereda). `None` = no tocar.
    llm_config: dict[str, Any] | None = Field(default=None, alias="model_config")
    # Modelo del CHAT del proyecto, separado del de ejecución. Alias JSON
    # `chat_model_config`. `{}` = el chat hereda el modelo de ejecución. `None` = no tocar.
    chat_llm_config: dict[str, Any] | None = Field(default=None, alias="chat_model_config")

    @model_validator(mode="after")
    def _validate_model(self) -> ProjectUpdateRequest:
        from api_server.db.platform_settings import (
            InvalidModelConfigError,
            validate_chat_model_config,
            validate_model_config,
        )

        try:
            if self.llm_config:
                validate_model_config(self.llm_config)
            if self.chat_llm_config:  # may pin a concrete provider_id (Feature B)
                validate_chat_model_config(self.chat_llm_config)
        except InvalidModelConfigError as exc:
            raise ValueError(str(exc)) from exc
        return self

    @field_validator("mcp_servers", mode="after")
    @classmethod
    def _validate_mcp_servers(
        cls, value: list[dict[str, Any]] | None
    ) -> list[dict[str, Any]] | None:
        if value is None:
            return None
        return validate_mcp_servers_payload(value)

    @field_validator("allowed_commands", mode="after")
    @classmethod
    def _validate_allowed_commands(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return _normalise_allowed_commands(value)

    @field_validator("allowed_domains", mode="after")
    @classmethod
    def _validate_allowed_domains(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return _normalise_allowed_domains(value)

    @field_validator("default_runtime_template", mode="after")
    @classmethod
    def _validate_runtime_template(cls, value: str | None) -> str | None:
        return _validate_runtime_template(value)

    @field_validator(
        "rag_knowledge_bases",
        "worker_config",
        "repository_config",
        "human_approval_policy",
        "execution_budgets",
        "guardrails_config",
        mode="after",
    )
    @classmethod
    def _cap_json_config(cls, value: Any, info: Any) -> Any:
        _check_json_config_size(value, info.field_name)
        return value

    @field_validator("execution_budgets", mode="after")
    @classmethod
    def _check_execution_budgets(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return _validate_execution_budgets(value)

    @field_validator("guardrails_config", mode="after")
    @classmethod
    def _check_guardrails_config(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return _validate_guardrails_config(value)

    @model_validator(mode="after")
    def _budget_invariants(self) -> ProjectUpdateRequest:
        # Only enforce invariants when the caller is *changing* the
        # budget shape -- a no-op update with all None fields skips.
        if any(
            getattr(self, f) is not None
            for f in (
                "budget_amount",
                "budget_currency",
                "budget_period",
                "budget_period_start_day",
                "budget_period_length_days",
            )
        ):
            return _validate_budget_invariants(self)  # type: ignore[return-value]
        return self


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------
class ProjectResponse(BaseModel):
    model_config = _BASE_CONFIG

    id: UUID
    tenant_id: UUID
    name: str
    description: str | None
    status: str
    team_id: UUID | None

    mcp_servers: list[dict[str, Any]]
    # DEPRECATED (P1-04): nadie la lee — lo real es `kb_projects`. Se mantiene
    # en la respuesta por compatibilidad; no configurar nada aquí.
    rag_knowledge_bases: list[dict[str, Any]]
    worker_config: dict[str, Any]
    # Modelo por defecto del proyecto (Ola A / ADR 0065). Alias JSON `model_config`.
    llm_config: dict[str, Any] = Field(alias="model_config")
    # Modelo del CHAT del proyecto (separado del de ejecución). `{}` = hereda.
    chat_llm_config: dict[str, Any] = Field(alias="chat_model_config")
    repository_config: dict[str, Any] | None
    # Config git del proyecto (ADR 0072): {provider, remote_url, default_branch,
    # auth_mode}. Sin secreto (vive en Vault). NULL = sin remoto.
    git_config: dict[str, Any] | None
    human_approval_policy: dict[str, Any] | None
    # DEPRECATED (P1-04): sin lectores; las credenciales van por Vault paths.
    secrets_vault_id: UUID | None
    # P1-03: settings APLICADOS (clamp de presupuestos en dispatch, merge de
    # guardrails en el worker) que eran inconfigurables por API.
    execution_budgets: dict[str, Any] | None
    guardrails_config: dict[str, Any] | None

    # Plan 06.16 task_06_16_01.
    allowed_commands: list[str]
    default_runtime_template: str | None
    # prod-12 Fase B: FQDN allowlist de las tools HTTP del agente.
    allowed_domains: list[str]
    # ADR 0128 fase 2: política rol→tool de las MCP del proyecto (`{}` = sin política).
    mcp_tool_roles: dict[str, list[str]]

    # Plan 16 task_16_11.
    human_task_review_mode: str

    budget_amount: Decimal | None
    budget_currency: str | None
    budget_period: str | None
    budget_period_start_day: int | None
    budget_period_length_days: int | None
    paused_by_budget: bool
    is_template: bool

    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


def to_project_response(p: Project) -> ProjectResponse:
    # Vía `model_validate` con la clave ALIAS `model_config`: el plugin mypy de
    # Pydantic no expone el kwarg field-name (`llm_config`) cuando hay alias
    # (mismo patrón que to_agent_response / to_team_response).
    payload: dict[str, Any] = {
        "id": p.id,
        "tenant_id": p.tenant_id,
        "name": p.name,
        "description": p.description,
        "status": p.status,
        "team_id": p.team_id,
        "mcp_servers": p.mcp_servers,
        "rag_knowledge_bases": p.rag_knowledge_bases,
        "worker_config": p.worker_config,
        "model_config": dict(p.model_config or {}),
        "chat_model_config": dict(p.chat_model_config or {}),
        "repository_config": p.repository_config,
        "git_config": p.git_config,
        "human_approval_policy": p.human_approval_policy,
        "secrets_vault_id": p.secrets_vault_id,
        "execution_budgets": p.execution_budgets,
        "guardrails_config": p.guardrails_config,
        "allowed_commands": p.allowed_commands,
        "default_runtime_template": p.default_runtime_template,
        "allowed_domains": p.allowed_domains,
        "mcp_tool_roles": p.mcp_tool_roles,
        "human_task_review_mode": p.human_task_review_mode,
        "budget_amount": p.budget_amount,
        "budget_currency": p.budget_currency,
        "budget_period": p.budget_period,
        "budget_period_start_day": p.budget_period_start_day,
        "budget_period_length_days": p.budget_period_length_days,
        "paused_by_budget": p.paused_by_budget,
        "is_template": p.is_template,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
        "deleted_at": p.deleted_at,
    }
    return ProjectResponse.model_validate(payload)


# ---------------------------------------------------------------------------
# Git config (ADR 0072) — PUT /projects/{id}/git
# ---------------------------------------------------------------------------
GitProvider = Literal["github", "gitlab", "azure_devops", "generic"]
GitAuthMode = Literal["none", "pat", "ssh"]
# Políticas del flujo git del plan (ADR 0072 fase 2).
GitBranchPushMode = Literal["incremental", "final_only"]
GitPlanValidationMode = Literal["human_required", "auto_approve"]
GitPushPolicy = Literal["forbidden", "branch_only_pr_required", "direct_to_default_allowed"]


class GitConfigUpdateRequest(BaseModel):
    """Fija el remoto + credencial del proyecto. El secreto (token/ssh_key) es de
    SOLO ESCRITURA: se guarda en Vault y NUNCA se devuelve."""

    model_config = _BASE_CONFIG

    provider: GitProvider = "generic"
    remote_url: str = Field(min_length=1, max_length=2048)
    default_branch: str = Field(default="main", min_length=1, max_length=255)
    auth_mode: GitAuthMode = "none"
    # Credencial (write-only). PAT: username opcional + token. SSH: ssh_key.
    username: str | None = Field(default=None, max_length=255)
    token: str | None = Field(default=None, max_length=8192)
    ssh_key: str | None = Field(default=None, max_length=16384)
    # Políticas del flujo git del plan (ADR 0072). Defaults razonables: la rama se
    # ve desde la 1ª tarea, el humano valida al cerrar, y se abre PR (no merge).
    branch_push_mode: GitBranchPushMode = "incremental"
    plan_validation_mode: GitPlanValidationMode = "human_required"
    push_policy: GitPushPolicy = "branch_only_pr_required"

    def config_dict(self) -> dict[str, str]:
        """Solo los campos NO secretos que se persisten en projects.git_config."""
        return {
            "provider": self.provider,
            "remote_url": self.remote_url,
            "default_branch": self.default_branch,
            "auth_mode": self.auth_mode,
        }

    def git_policies(self) -> dict[str, str]:
        """Políticas que se persisten en projects.worker_config.git_policies."""
        return {
            "branch_push_mode": self.branch_push_mode,
            "plan_validation_mode": self.plan_validation_mode,
            "push_policy": self.push_policy,
        }


class GitConfigResponse(BaseModel):
    """Config git efectiva (sin secreto) + si hay credencial guardada en Vault."""

    model_config = _BASE_CONFIG

    provider: str
    remote_url: str
    default_branch: str
    auth_mode: str
    has_credential: bool
    # Políticas del flujo git del plan (defaults si el proyecto no las fijó).
    branch_push_mode: str = "incremental"
    plan_validation_mode: str = "human_required"
    push_policy: str = "branch_only_pr_required"
