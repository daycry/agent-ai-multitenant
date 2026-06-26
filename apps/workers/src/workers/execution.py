"""The worker conducts a real execution (Plan 02 Fase G / task_02_30).

Fases A-F built the components; this is where the worker wires them
into a live run. `conduct_execution`:

  1. creates the `executions` row in `running` state — so the
     per-execution Redis stream has a stable id the UI can connect to;
  2. launches the `agent-runtime` container for the task and streams
     its stdout, republishing every JSON event onto `exec:{id}` (the
     stream `/ws/executions/{id}` tails);
  3. finalises the row with the streamed steps_log and usage roll-ups
     once the container exits.

The DB sessionmaker and Redis client are injected so the integration
tests can point them at the throwaway test stack; the Celery task
(task_02_31) builds them from `Settings`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from api_server.auth.internal_agent import mint_agent_token
from api_server.db.approval_repo import request_approval_if_needed
from api_server.db.domain import Project, Task, TaskStatus
from api_server.db.execution_repo import (
    create_running_execution,
    finalize_execution,
    get_execution,
    supersede_running_executions,
)
from api_server.events import publish_execution_event, publish_task_status_changed
from api_server.task_state_machine import transition_task_status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workers.agent_tool_schemas import build_model_tool_schemas
from workers.config import Settings
from workers.container import AgentContainerRunner, ContainerSpec
from workers.memorizer import trigger_memorize
from workers.model_resolver import (
    ModelResolutionError,
    resolve_model_spec,
    safe_spec_summary,
)

_log = structlog.get_logger("workers.execution")

# Status the agent loop reports when it parks on a sensitive action —
# mirrors agent_runtime.state.STATUS_AWAITING_APPROVAL and
# ExecutionStatus.AWAITING_HUMAN_APPROVAL.
_AWAITING_APPROVAL = "awaiting_human_approval"

# How often the run polls `cancel_requested_at` while the container runs, to
# kill it cooperatively on an operator cancel (POST /executions/{id}/cancel).
_CANCEL_POLL_INTERVAL_S = 3.0

# A zeroed usage roll-up — used when a run produces no result line
# (the container crashed or timed out before `execution.finished`).
_EMPTY_USAGE: dict[str, Any] = {
    "iterations": 0,
    "total_tokens": 0,
    "cost_usd": 0.0,
    "tool_calls": 0,
    "model_calls": 0,
}


class CrossTenantExecutionError(RuntimeError):
    """An ExecutionRequest's `task_id` does not belong to its declared
    `tenant_id`.

    The worker connects with the BYPASSRLS `migrations_user` role
    (workers/config.py) because it legitimately writes `executions` rows
    for many tenants — so RLS cannot catch a tampered or buggy Celery
    payload that pairs one tenant with another tenant's task. We validate
    the task↔tenant ownership explicitly at the worker boundary instead
    (Plan 06.14 task_06_14_02 / multi-tenancy-rls-1, multi-tenancy-rls-5).
    """


@dataclass(frozen=True)
class ExecutionRequest:
    """Everything the worker needs to conduct one execution.

    The orchestrator (task_02_31) builds this from a task event. `task`
    and `model` are the spec dicts the agent-runtime entrypoint expects
    (`AGENT_TASK_SPEC`); `task_id` / `tenant_id` are the real DB ids the
    `executions` row is keyed on.
    """

    tenant_id: str
    task_id: str
    agent_id: str | None
    task: dict[str, Any]
    model: dict[str, Any]
    budgets: dict[str, Any] | None = None
    # The active chat mode's tool whitelist (`ChatModeConfig.allowed_tools`,
    # task_06_14_07). ``None`` = no restriction (every registered tool
    # callable). A list — including an empty one — installs the allowlist;
    # the agent-runtime's ToolRegistry then rejects any tool outside it at
    # call time. We keep ``None`` distinct from ``[]`` so the "block every
    # tool" discussion mode is expressible.
    allowed_tools: list[str] | None = None
    # The project's shell-command allowlist (`projects.allowed_commands`,
    # Plan 06.16 task_06_16_02). The orchestrator threads it from the task's
    # project; the worker forwards it into the spec so the runtime can build a
    # per-project `shell_exec` bound to exactly these program basenames
    # (deny-by-default). ``None`` = no key (shell_exec not registered, e.g. a
    # bare run); a list — including ``[]`` — registers shell_exec, with the
    # empty list meaning deny-all (every command rejected).
    allowed_commands: list[str] | None = None
    # The project's stack runtime (`projects.default_runtime_template`, Plan
    # 06.16 task_06_16_03). The orchestrator threads it from the task's project;
    # the worker forwards it so the runtime's `run_*` docker_command tools
    # (`run_pytest`/`run_lint`/`run_typecheck`/`run_build`) resolve their
    # RuntimeTemplate from the project stack — a PHP project with `php-phpunit`
    # runs `run_pytest` there, not in `python-pytest`. `None` (the default, and
    # what a project that pinned no stack carries) keeps each tool's own default
    # runtime (backward-compatible — no behaviour change for Python projects).
    default_runtime_template: str | None = None
    # The agent's assigned tools serialised as executable ToolSpec dicts
    # (`serialize_agent_tool_specs`, Plan 06.18 task_06_18_05). The orchestrator
    # builds it from the agent's `agent_tools` rows; the worker forwards it into
    # the agent spec so `__main__.run_task` registers the real executors under
    # canonical names. `None` = no key (no assignments) → the runtime keeps the
    # pre-06.18 echo/noop behaviour (06.15 backward-compat). Before forwarding,
    # the worker resolves any `docker_command` spec's `runtime_template` to a
    # concrete image (the worker owns the runtime catalog; the sandboxed
    # runtime must not import `shared_test_runtimes`).
    tool_specs: list[dict[str, Any]] | None = None
    # The project's MCP server declarations (`projects.mcp_servers`, JSONB; Plan
    # 06.18 task_06_18_12 / ADR 0052). The orchestrator threads it from the
    # task's project; the worker forwards it into the agent spec so
    # `__main__.run_task` starts an `MCPToolRunner`, connects each server (auth
    # via Vault) and registers its `<server>.<tool>` tools before the graph.
    # `None` = no key (no MCP servers declared) -> the runtime opens no MCP
    # session, the pre-06.18 behaviour (feature-safe). Each entry mirrors
    # `shared_mcp.MCPServerConfig` / `api_server.mcp.config.MCPServerConfigModel`.
    mcp_servers: list[dict[str, Any]] | None = None
    # The agent's assigned skills' `prompt_fragment` list (Plan 06.18
    # task_06_18_13 / ADR 0050). The orchestrator resolves it from the agent's
    # `agent_skills` rows; the worker forwards it into the agent spec so
    # `__main__.run_task` prepends it to the system prompt EFECTIVO. `None` = no
    # key (no skills assigned) -> the runtime keeps the current prompt untouched
    # (backward-compatible).
    skill_prompt_fragments: list[str] | None = None

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe dict — the Celery payload the orchestrator sends."""
        return {
            "tenant_id": self.tenant_id,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "task": self.task,
            "model": self.model,
            "budgets": self.budgets,
            "allowed_tools": self.allowed_tools,
            "allowed_commands": self.allowed_commands,
            "default_runtime_template": self.default_runtime_template,
            "tool_specs": self.tool_specs,
            "mcp_servers": self.mcp_servers,
            "skill_prompt_fragments": self.skill_prompt_fragments,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ExecutionRequest:
        """Rebuild a request from the Celery payload (worker side)."""
        return cls(
            tenant_id=raw["tenant_id"],
            task_id=raw["task_id"],
            agent_id=raw.get("agent_id"),
            task=raw["task"],
            model=raw["model"],
            budgets=raw.get("budgets"),
            allowed_tools=raw.get("allowed_tools"),
            allowed_commands=raw.get("allowed_commands"),
            default_runtime_template=raw.get("default_runtime_template"),
            tool_specs=raw.get("tool_specs"),
            mcp_servers=raw.get("mcp_servers"),
            skill_prompt_fragments=raw.get("skill_prompt_fragments"),
        )


@dataclass(frozen=True)
class ExecutionOutcome:
    """The result of one conducted execution."""

    execution_id: str
    status: str
    abort_code: str | None

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe summary — what the Celery result backend stores."""
        return {
            "execution_id": self.execution_id,
            "status": self.status,
            "abort_code": self.abort_code,
        }


@dataclass
class _RuntimeResult:
    """The agent run's result, in the shape `finalize_execution`
    duck-types (`ExecutionResultLike`)."""

    status: str
    abort_code: str | None
    output: str | None
    iterations: int
    steps: list[dict[str, Any]]
    usage: dict[str, Any]


def _agent_spec(
    request: ExecutionRequest,
    approval_policy: dict[str, Any] | None,
    *,
    model_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The `AGENT_TASK_SPEC` payload for the container.

    ``model_spec`` is the RESOLVED model (kind + endpoint + credential,
    ADR 0057 F1) the worker computed from ``request.model``; ``None`` keeps
    the request's spec verbatim (pure-function callers / scripted tests).
    """
    spec: dict[str, Any] = {"task": request.task, "model": model_spec or request.model}
    if request.budgets:
        spec["budgets"] = request.budgets
    # With a policy the loop gates sensitive tool calls (task_02_33).
    if approval_policy:
        spec["approval_policy"] = approval_policy
    # Forward the chat mode's tool allowlist (task_06_14_07). Only emit the
    # key when set — `None` means "no key", which the runtime reads as "no
    # restriction". An empty list IS emitted (block every tool).
    if request.allowed_tools is not None:
        spec["allowed_tools"] = request.allowed_tools
    # Forward the project's shell-command allowlist (task_06_16_02). Only emit
    # the key when set — `None` means "no key" (shell_exec not registered). An
    # empty list IS emitted: it registers a deny-all shell_exec.
    if request.allowed_commands is not None:
        spec["allowed_commands"] = request.allowed_commands
    # Forward the project's stack runtime (task_06_16_03). Only emit the key
    # when the project pinned a stack; `None` means "no key", which the runtime
    # reads as "keep each `run_*` tool's own default runtime" (python-pytest) —
    # backward-compatible for existing Python projects.
    if request.default_runtime_template is not None:
        spec["default_runtime_template"] = request.default_runtime_template
    # Forward the serialised executable ToolSpec list (task_06_18_05). Only emit
    # when the agent has assignments — `None` means "no key", which the runtime
    # reads as "register no new families" (pre-06.18 echo/noop behaviour). Before
    # forwarding we resolve each docker_command spec's `runtime_template` to a
    # concrete image (the worker owns the runtime catalog; the sandboxed runtime
    # must not import `shared_test_runtimes`).
    if request.tool_specs is not None:
        spec["tool_specs"] = _resolve_tool_spec_images(
            request.tool_specs, request.default_runtime_template
        )
    # Forward the project's MCP server declarations (task_06_18_12 / ADR 0052).
    # Only emit the key when the project declares servers -- `None` means "no
    # key", which the runtime reads as "open no MCP session" (feature-safe,
    # pre-06.18 behaviour). The runtime starts an `MCPToolRunner`, connects each
    # server and registers its `<server>.<tool>` tools before the graph.
    if request.mcp_servers is not None:
        spec["mcp_servers"] = request.mcp_servers
    # Forward the assigned skills' prompt fragments (task_06_18_13 / ADR 0050).
    # Only emit the key when the agent has skills -- `None` means "no key", which
    # the runtime reads as "keep the system prompt untouched" (backward-compat).
    if request.skill_prompt_fragments is not None:
        spec["skill_prompt_fragments"] = request.skill_prompt_fragments
    # Agentes #2: advertise the agent's tools to the LLM so it can actually call
    # them (memory_recall/rag_search/read_file/…). Without this the model never
    # sees any tool → it can neither recall memory nor work through tools, for ANY
    # provider. Schemas come from the canonical builtin catalog + custom tool_specs,
    # filtered to the effective allowlist (`allowed_tools`). Set inside `model` so
    # `build_provider_client` reads `spec["tools"]` and passes them to complete().
    # `include_system_tools=True`: the memory + orchestration families are
    # runtime CAPABILITIES (not catalog assignments), so they never reach the
    # allowlist — we advertise them here so every agent can recall/store memory
    # and move the Kanban (H0/H3). An explicit empty allowlist (discussion mode)
    # still suppresses everything inside build_model_tool_schemas.
    model_tools = build_model_tool_schemas(
        request.allowed_tools, request.tool_specs, include_system_tools=True
    )
    if model_tools:
        spec["model"] = {**spec["model"], "tools": model_tools}
    return spec


def _build_runtime_env(
    request: ExecutionRequest,
    approval_policy: dict[str, Any] | None,
    *,
    agent_internal_api_url: str,
    model_spec: dict[str, Any] | None = None,
) -> dict[str, str]:
    """El env del contenedor `agent-runtime` para una ejecución (función PURA).

    Sin docker, sin red: toma la `ExecutionRequest` (más la `approval_policy`
    resuelta del proyecto) y devuelve el dict de variables de entorno que el
    `ContainerSpec` lleva. Extraída de `conduct_execution` para poder testearla
    en aislamiento (Plan 06.17 / followup-worker-internal-token).

    Siempre incluye ``AGENT_TASK_SPEC``. Cuando la tarea tiene un agente
    asignado, mintea además ``AGENTIC_INTERNAL_TOKEN`` (firmado con el
    `jwt_secret` del api-server, vía :func:`mint_agent_token`) y publica
    ``AGENTIC_API_URL`` para que el runtime active las familias de
    conocimiento/memoria (rag-search, memory-recall/store, document-convert,
    promote-to-kb) — la costura de la API interna del agente (ADR 0012,
    Plan 04.5). El token lleva el contexto de la tarea (claim ``task`` =
    `request.task_id`) para que los endpoints internos resuelvan el project_id
    EFECTIVO de un agente global (ADR 0054).

    RIESGO operativo: esto ACTIVA llamadas HTTP internas del runtime hacia el
    api-server que antes estaban dormidas. Si la tarea NO tiene agente asignado
    NO se mintea token — sin token el runtime salta esas familias con gracia
    (backward-compat, el comportamiento actual). El runtime también degrada con
    gracia si el token expira o el api-server no responde.
    """
    env: dict[str, str] = {
        "AGENT_TASK_SPEC": json.dumps(_agent_spec(request, approval_policy, model_spec=model_spec)),
    }
    # Sin agente asignado no hay sujeto para el token: lo dejamos fuera y el
    # runtime mantiene su comportamiento sin API interna (backward-compat).
    if request.agent_id:
        env["AGENTIC_INTERNAL_TOKEN"] = mint_agent_token(
            agent_id=UUID(request.agent_id),
            tenant_id=UUID(request.tenant_id),
            task_id=UUID(request.task_id),
        )
        env["AGENTIC_API_URL"] = agent_internal_api_url
    return env


def _resolve_tool_spec_images(
    tool_specs: list[dict[str, Any]], project_default_runtime: str | None
) -> list[dict[str, Any]]:
    """Pre-resolve each ``docker_command`` ToolSpec's ``runtime_template`` to a
    concrete docker image (Plan 06.18 task_06_18_05).

    The agent-runtime is a separate container with no access to
    ``shared_test_runtimes``; only the worker can map a runtime-template id to
    an image. So we resolve here — honouring the project stack over the tool
    default (Plan 06.16 precedence) — and replace ``runtime_template`` with an
    explicit ``image`` the runtime's ``docker_command`` builder consumes
    directly. Specs that already carry an explicit ``image`` (Plan 05 custom
    tools) are left untouched. An unknown runtime id surfaces as a clear
    ``RuntimeResolutionError`` at dispatch, not a silent boot crash inside the
    container.
    """
    from workers.test_runtime import resolve_run_runtime_image

    resolved: list[dict[str, Any]] = []
    for raw in tool_specs:
        spec = dict(raw)
        if spec.get("implementation_type") == "docker_command":
            config = dict(spec.get("config") or {})
            if not config.get("image"):
                tool_runtime = config.pop("runtime_template", None)
                config["image"] = resolve_run_runtime_image(
                    project_default_runtime,
                    str(tool_runtime) if tool_runtime else None,
                )
            spec["config"] = config
        resolved.append(spec)
    return resolved


async def _load_project(session: AsyncSession, task_id: UUID) -> Project | None:
    """The task's project — its `human_approval_policy` gates the run."""
    task = await session.get(Task, task_id)
    if task is None:
        return None
    return await session.get(Project, task.project_id)


def _parse_line(line: str) -> dict[str, Any] | None:
    """Parse one stdout line into a JSON event, or None if it isn't one.

    The agent-runtime emits one JSON object per line; LangGraph (and the
    occasional library) may also print free text — those are ignored.
    """
    if not line.startswith("{"):
        return None
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _assemble_result(
    final_result: dict[str, Any] | None,
    steps: list[dict[str, Any]],
    *,
    timed_out: bool,
    exit_code: int,
    runtime_error: str | None,
) -> _RuntimeResult:
    """Fold the streamed steps + final result line into a `_RuntimeResult`.

    When the container produced an `execution.finished` line, that is
    the result. Otherwise the run failed (crash, timeout, or an
    `execution.error` line) — keep whatever steps streamed and mark it
    `failed`.
    """
    if final_result is not None:
        return _RuntimeResult(
            status=final_result.get("status", "failed"),
            abort_code=final_result.get("abort_code"),
            output=final_result.get("output"),
            iterations=int(final_result.get("iterations", 0)),
            steps=steps,
            usage=final_result.get("usage") or dict(_EMPTY_USAGE),
        )

    if runtime_error is not None:
        detail = f"agent-runtime error: {runtime_error}"
    elif timed_out:
        detail = "agent-runtime container timed out"
    else:
        detail = f"agent-runtime container exited {exit_code} with no result"
    return _RuntimeResult(
        status="failed",
        abort_code=None,
        output=detail,
        iterations=0,
        steps=steps,
        usage=dict(_EMPTY_USAGE),
    )


def _default_vault_store() -> Any:
    """El store de Vault del worker — el mismo builder cacheado del api-server.

    Lazy-import para no cargar el módulo router (FastAPI) al importar el
    worker. Devuelve ``None`` si Vault no está configurado (la resolución
    degrada a sin-credencial, suficiente para Ollama local)."""
    from api_server.routers.llm_providers import get_provider_vault_store

    return get_provider_vault_store()


async def transition_task_after_run(
    session: AsyncSession, task_id: UUID, result_status: str
) -> tuple[Task, str, str] | None:
    """Move a task off ``in_progress`` after its run reaches a terminal status.

    prod-06 task_prod06_dag_01. Until now nothing transitioned a task once its
    execution finished (only the ``awaiting_human_approval`` branch did), so a
    ``done``/``failed`` run left the task ``in_progress`` forever — inflating the
    agent's load counter and stalling the DAG. Returns ``(task, old, new)`` for
    event publication, or ``None`` when no transition applies:

      - ``done`` -> ``in_review`` if the task has a reviewer, else ``done``
        (stamping ``completed_at``).
      - any other terminal status (``failed``/``aborted``/…) -> ``blocked``; the
        motive is the linked execution row (``abort_code``/output), not a task column.
      - ``awaiting_human_approval`` is owned by the approval branch -> ``None``.

    The ``in_progress`` guard keeps it idempotent and avoids stepping on a task
    another path already moved (e.g. a cancellation that set it ``cancelled``).
    """
    if result_status == _AWAITING_APPROVAL:
        return None
    task = await session.get(Task, task_id)
    if task is None or task.status != TaskStatus.IN_PROGRESS.value:
        return None
    old_status = task.status
    if result_status == "done":
        target = (
            TaskStatus.IN_REVIEW.value
            if task.reviewer_agent_id is not None
            else TaskStatus.DONE.value
        )
    else:
        target = TaskStatus.BLOCKED.value
    transition_task_status(task, target)
    if task.status == TaskStatus.DONE.value:
        task.completed_at = datetime.now(UTC)
    if task.status == old_status:
        return None
    return (task, old_status, task.status)


async def refresh_budgets_after_run(
    sessionmaker: async_sessionmaker[AsyncSession], tenant_id: UUID
) -> None:
    """Re-derive the tenant's budget auto-pause + fire alerts after a run ends.

    prod-06 task_prod06_budget_01: the run's cost is persisted by
    ``finalize_execution``; this re-derives ``paused_by_budget`` for the tenant
    so a run that tipped a scope over 100% pauses the NEXT start immediately
    (instead of waiting for the ``workers.refresh_budgets`` beat). Best-effort —
    a budget failure must never break the finished run; the periodic beat is the
    safety net. Opens its own short transaction on the BYPASSRLS worker engine.
    """
    from api_server.budgets import sweep_tenant_budgets
    from api_server.budgets.consumption import CeleryBudgetAlertDispatcher

    try:
        async with sessionmaker() as session, session.begin():
            await sweep_tenant_budgets(
                session, tenant_id=tenant_id, dispatcher=CeleryBudgetAlertDispatcher()
            )
    except Exception as exc:  # pragma: no cover - defensive best-effort
        _log.warning("workers.budget_refresh_failed", tenant_id=str(tenant_id), error=str(exc))


async def conduct_execution(  # noqa: PLR0915, PLR0912 - tramos lineales + poll de cancelación
    request: ExecutionRequest,
    *,
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    redis: Redis,
    vault_store: Any | None = None,
    runner: AgentContainerRunner | None = None,
    cancel_poll_interval_s: float = _CANCEL_POLL_INTERVAL_S,
    celery_task_id: str | None = None,
) -> ExecutionOutcome:
    """Run one task end to end: container → Redis stream → `executions` row."""
    task_id = UUID(request.task_id)
    tenant_id = UUID(request.tenant_id)
    async with sessionmaker() as session, session.begin():
        # The worker is BYPASSRLS, so RLS cannot stop a Celery payload that
        # pairs a tenant with another tenant's task. Validate task↔tenant
        # ownership explicitly before attributing the task's data to the
        # claimed tenant (Plan 06.14 task_06_14_02 / multi-tenancy-rls-1/5).
        task = await session.get(Task, task_id)
        if task is None or task.tenant_id != tenant_id:
            _log.error(
                "workers.cross_tenant_execution_rejected",
                requested_tenant_id=str(tenant_id),
                task_id=str(task_id),
                actual_tenant_id=(str(task.tenant_id) if task is not None else None),
            )
            raise CrossTenantExecutionError(f"task {task_id} does not belong to tenant {tenant_id}")
        # Idempotency: if this task is re-delivered (acks_late + a worker
        # crash), close out the crashed run's orphan `running` row so we
        # never accumulate duplicate live executions (task_06_14_04).
        superseded = await supersede_running_executions(
            session, tenant_id=tenant_id, task_id=task_id
        )
        if superseded:
            _log.warning(
                "workers.superseded_stale_executions",
                task_id=str(task_id),
                count=superseded,
            )
        execution = await create_running_execution(
            session,
            tenant_id=tenant_id,
            task_id=task_id,
            agent_id=UUID(request.agent_id) if request.agent_id else None,
            # prod-06 cancel_01: persist the Celery job id so an operator cancel
            # can `revoke` a still-queued/running job (was NULL → revoke dead code).
            celery_task_id=celery_task_id,
        )
        execution_id = execution.id
        project = await session.get(Project, task.project_id)
        approval_policy = project.human_approval_policy if project is not None else None
        # ADR 0057 F1: resolver el model_config (clave `provider` = kind, sin
        # endpoint/credencial) a un spec EJECUTABLE (kind + base_url +
        # credencial de Vault) ANTES de lanzar el contenedor — el sandbox no
        # tiene BD/Vault. Un fallo de resolución NO degrada a scripted: la
        # ejecución se finaliza como fallida con motivo explícito.
        resolved_model: dict[str, Any] | None = None
        resolution_error: str | None = None
        try:
            resolved_model = await resolve_model_spec(
                session,
                dict(request.model or {}),
                vault=vault_store if vault_store is not None else _default_vault_store(),
            )
        except ModelResolutionError as exc:
            resolution_error = str(exc)
    exec_id = str(execution_id)
    _log.info("workers.execution_started", execution_id=exec_id, task_id=request.task_id)
    if resolved_model is not None:
        # Solo claves no sensibles (safe_spec_summary) — la credencial vive en
        # el env del contenedor efímero y nunca se loguea.
        _log.info(
            "workers.model_resolved", execution_id=exec_id, **safe_spec_summary(resolved_model)
        )

    approval: dict[str, Any] | None = None
    if resolution_error is not None:
        # Fail-fast (ADR 0057 F1): sin proveedor resoluble NO se lanza el
        # contenedor — la ejecución termina `failed` con motivo explícito en
        # vez de correr en silencio con el cliente scripted.
        _log.error("workers.model_resolution_failed", execution_id=exec_id, error=resolution_error)
        await publish_execution_event(
            redis, exec_id, event_type="execution.error", payload={"error": resolution_error}
        )
        result = _RuntimeResult(
            status="failed",
            abort_code="model_unresolved",
            output=resolution_error,
            iterations=0,
            steps=[],
            usage=dict(_EMPTY_USAGE),
        )
    else:
        # The container's stdout is pumped by a background thread; bridge
        # each line onto an asyncio queue so the live Redis publishing (and
        # event collection) happens on the running loop.
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        steps: list[dict[str, Any]] = []
        final_result: dict[str, Any] | None = None
        runtime_error: str | None = None

        def on_line(line: str) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, line)

        async def drain() -> None:
            nonlocal final_result, runtime_error
            while True:
                line = await queue.get()
                if line is None:
                    return
                event = _parse_line(line)
                if event is None or not event.get("event"):
                    continue
                kind = str(event["event"])
                if kind == "step" and isinstance(event.get("step"), dict):
                    steps.append(event["step"])
                elif kind == "execution.finished":
                    final_result = event.get("result")
                elif kind == "execution.error":
                    runtime_error = event.get("error")
                payload = {key: value for key, value in event.items() if key != "event"}
                await publish_execution_event(redis, exec_id, event_type=kind, payload=payload)

        drainer = asyncio.create_task(drain())
        container_spec = ContainerSpec(
            image=settings.agent_runtime_image,
            env=_build_runtime_env(
                request,
                approval_policy,
                agent_internal_api_url=settings.agent_internal_api_url,
                # El spec RESUELTO (kind + endpoint + credencial) — ADR 0057 F1.
                model_spec=resolved_model,
            ),
            labels={"com.agentic-platform.execution-id": exec_id},
        )
        active_runner = runner or AgentContainerRunner(settings)
        cancel_seen = False

        async def _watch_for_cancel() -> None:
            """Poll ``cancel_requested_at`` while the container runs; on an operator
            cancel, kill the container (the LLM-cost source) so ``run_streamed`` exits
            and the run finalises as ``cancelled``."""
            nonlocal cancel_seen
            while True:
                await asyncio.sleep(cancel_poll_interval_s)
                async with sessionmaker() as cancel_session:
                    ex = await get_execution(cancel_session, execution_id)
                if ex is not None and ex.cancel_requested_at is not None:
                    cancel_seen = True
                    await asyncio.to_thread(active_runner.kill_by_label, exec_id)
                    return

        watcher = asyncio.create_task(_watch_for_cancel())
        try:
            container_result = await asyncio.to_thread(
                active_runner.run_streamed, container_spec, on_line
            )
        finally:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher
        await queue.put(None)
        await drainer

        result = _assemble_result(
            final_result,
            steps,
            timed_out=container_result.timed_out,
            exit_code=container_result.exit_code,
            runtime_error=runtime_error,
        )
        if cancel_seen:
            # Operator cancel: keep the partial steps/usage for the audit trail but
            # mark the run cancelled (finalize_execution treats it as terminal).
            result = _RuntimeResult(
                status="cancelled",
                abort_code="cancelled",
                output="cancelled by operator",
                iterations=result.iterations,
                steps=result.steps,
                usage=result.usage,
            )
        approval = final_result.get("approval") if final_result else None
    task_event: tuple[Any, str, str] | None = None
    async with sessionmaker() as session, session.begin():
        await finalize_execution(session, execution_id, result=result)
        # A run parked on a sensitive action becomes a real
        # ApprovalRequest — the approval engine on the live run (task_02_33).
        # request_approval_if_needed also moves the TASK to
        # `awaiting_human_approval` and frees its agent (ADR 0020).
        if result.status == _AWAITING_APPROVAL and approval:
            execution = await get_execution(session, execution_id)
            project = await _load_project(session, task_id)
            task = await session.get(Task, task_id)
            if execution is not None and project is not None and task is not None:
                old_status = task.status
                await request_approval_if_needed(
                    session,
                    execution=execution,
                    project=project,
                    category=str(approval.get("category", "")),
                    action=dict(approval.get("action") or {}),
                )
                if task.status != old_status:
                    task_event = (task, old_status, task.status)
        else:
            # prod-06 task_prod06_dag_01: every other terminal run moves the
            # task off in_progress (done -> in_review/done, failed -> blocked).
            task_event = await transition_task_after_run(session, task_id, result.status)

    # Publish the task event AFTER the commit so the board sees a
    # consistent state. publish_* is best-effort and swallows its own errors.
    if task_event is not None:
        task_obj, old, new = task_event
        await publish_task_status_changed(redis, task_obj, old_status=old, new_status=new)

    # prod-06 task_prod06_budget_01: now that the run's cost is persisted
    # (finalize_execution above), re-derive the tenant's budget auto-pause +
    # fire any threshold alerts, so a run that tipped a scope over 100% pauses
    # the NEXT start immediately. Best-effort — never breaks the finished run.
    await refresh_budgets_after_run(sessionmaker, tenant_id)

    # Fire-and-forget Memorizer (Plan 04.5 task_04_5_02). The Celery
    # task does the LLM distillation off the executor's critical path,
    # so the executor returns immediately even if Ollama is slow.
    # `trigger_memorize` swallows broker errors — a Memorizer outage
    # must never break an agent run.
    trigger_memorize(execution_id, result.status)

    _log.info("workers.execution_finished", execution_id=exec_id, status=result.status)
    return ExecutionOutcome(
        execution_id=exec_id,
        status=result.status,
        abort_code=result.abort_code,
    )
