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
from api_server.db.domain import Plan, Project, Task, TaskStatus
from api_server.db.execution_repo import (
    create_running_execution,
    finalize_execution,
    get_execution,
    supersede_running_executions,
)
from api_server.db.models import Organization
from api_server.events import publish_execution_event, publish_task_status_changed
from api_server.task_state_machine import transition_task_status
from redis.asyncio import Redis
from sqlalchemy import func, select
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
    # prod-17 (bucle del AI reviewer): when True, this run is a REVIEW of the task
    # by its reviewer agent. On finish the worker applies the parsed verdict
    # (parse_reviewer_output → apply_reviewer_verdict) instead of the normal
    # done/failed task transition (dag_01). `review_context` carries the review
    # input (acceptance criteria + the implementer's prior output) the runtime
    # injects into the reviewer's prompt. Default False/None = a normal run.
    review: bool = False
    review_context: dict[str, Any] | None = None
    # Inter-run reviewer feedback (A2): the AI reviewer's prior rejection payloads
    # for THIS task, threaded by the orchestrator when the task was rejected on an
    # earlier pass and re-dispatched to the implementer (in_review → backlog →
    # ready). Each entry is `{failed_criterion, what_to_fix, testreport_evidence}`.
    # The runtime folds them into a corrective preamble so the IMPLEMENTER knows
    # what to fix. `None` = no key (no prior rejection) → identical to the current
    # behaviour for a first dispatch (backward-compat). Distinct from `review` /
    # `review_context`, which drive the REVIEWER run, not the implementer.
    prior_review_feedback: list[dict[str, Any]] | None = None
    # Feature C: human comments on this task/plan (added in the Kanban/plan UI),
    # threaded by the orchestrator. Each entry `{scope, content}`. The runtime folds
    # them into a contextual preamble so the agent takes them into account. `None` =
    # no key (no comments) → backward-compat.
    task_comments: list[dict[str, Any]] | None = None

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
            "review": self.review,
            "review_context": self.review_context,
            "prior_review_feedback": self.prior_review_feedback,
            "task_comments": self.task_comments,
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
            review=bool(raw.get("review", False)),
            review_context=raw.get("review_context"),
            prior_review_feedback=raw.get("prior_review_feedback"),
            task_comments=raw.get("task_comments"),
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


# Eligibility (R5): the task status the orchestrator sets right before enqueueing
# a run of each kind — the ONLY status a run of that kind may launch from.
_LAUNCHABLE_STATUS_BY_KIND: dict[bool, str] = {
    False: TaskStatus.IN_PROGRESS.value,  # implementer run
    True: TaskStatus.IN_REVIEW.value,  # reviewer run
}


def _task_is_launchable(status: str, *, is_review: bool) -> bool:
    """Whether a task in ``status`` may start a run of this kind.

    A re-delivered Celery message (``acks_late``) can re-fire ``run_execution``
    for a task the operator moved to ``blocked``/``cancelled`` in the meantime
    (e.g. after the worker restart that recovers an R1 hang). Only the in-flight
    status the orchestrator set right before enqueueing is launchable; anything
    else means the task moved on and the run must be a no-op (R5).
    """
    return status == _LAUNCHABLE_STATUS_BY_KIND[is_review]


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
    # ADR 0087: the agent's structured finish status (success|failed|partial) or
    # None — carried from the runtime's execution.finished result.
    finish_status: str | None = None


# Track 1 / ADR 0021 addendum: a base shell allowlist for the natively-agentic
# Claude Agent SDK. UNIONed with the project's allowlist for `claude_sdk` runs ONLY,
# so the SDK can reconcile the worktree with file ops instead of being straitjacketed
# by an empty allowlist. Safe inside the sandbox (cap-drop ALL, read-only rootfs except
# /workspace+/tmp, internal network/no egress, no docker socket — ADR 0012/0019/0040):
# every command is confined to the container and the task worktree.
# NOTE (Feature D): `git` is deliberately NOT here. The agent never commits/pushes
# (the worker owns git — principle 2, no credentials in the sandbox), and git is
# BROKEN here anyway: the worktree's `.git` points to the bare repo's worktree
# metadata, which is NOT mounted in the sandbox → every `git` exits 128. Exposing it
# only made the agent waste turns on cryptic failures; the prompt tells it the
# platform persists changes automatically.
_SDK_BASE_SHELL_COMMANDS: frozenset[str] = frozenset(
    {
        "rm",
        "mv",
        "cp",
        "mkdir",
        "rmdir",
        "ls",
        "cat",
        "find",
        "grep",
        "touch",
        "head",
        "tail",
        "wc",
        "diff",
    }
)


def _agent_spec(  # noqa: PLR0912 - secuencia lineal de claves opcionales del spec
    request: ExecutionRequest,
    approval_policy: dict[str, Any] | None,
    *,
    model_spec: dict[str, Any] | None = None,
    acceptance_criteria: list[Any] | None = None,
    wall_clock_budget_s: float | None = None,
    max_iterations_budget: int | None = None,
    max_tokens_budget: int | None = None,
) -> dict[str, Any]:
    """The `AGENT_TASK_SPEC` payload for the container.

    ``model_spec`` is the RESOLVED model (kind + endpoint + credential,
    ADR 0057 F1) the worker computed from ``request.model``; ``None`` keeps
    the request's spec verbatim (pure-function callers / scripted tests).

    ``acceptance_criteria`` (the task's definition of "done") is merged into
    ``spec["task"]`` so the agent's decision prompt can show what completing the
    task means — letting the TASK drive read/write/test behaviour instead of a
    blanket rule. ``None``/empty keeps ``task`` as the request sent it.
    """
    task_payload = (
        {**request.task, "acceptance_criteria": acceptance_criteria}
        if acceptance_criteria
        else request.task
    )
    spec: dict[str, Any] = {"task": task_payload, "model": model_spec or request.model}
    # Agent-loop safeguard budgets. Align the internal wall-clock with the
    # per-provider container budget so a slow claude_sdk run isn't aborted early
    # by the 600s default (max_wall_clock_exceeded). An operator-supplied value in
    # request.budgets always wins (setdefault).
    budgets = dict(request.budgets or {})
    if wall_clock_budget_s is not None:
        budgets.setdefault("max_wall_clock_s", float(wall_clock_budget_s))
    if max_iterations_budget is not None:
        budgets.setdefault("max_iterations", int(max_iterations_budget))
    if max_tokens_budget is not None:
        # Auditoría 2026-07-02: con la contabilidad de usage arreglada (F1.4),
        # el default de 100k del runtime corta runs sanos de claude_sdk a ~23
        # iteraciones — presupuesto por-kind realista, como max_iterations.
        budgets.setdefault("max_tokens", int(max_tokens_budget))
    if budgets:
        spec["budgets"] = budgets
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
    # empty list IS emitted: it registers a deny-all shell_exec. For a natively-
    # agentic `claude_sdk` run, UNION the base VCS/file allowlist so the SDK can
    # reconcile the worktree (Track 1 / ADR 0021) — this also forces shell_exec to
    # register even when the project pinned nothing. Thin providers are unchanged.
    kind = (model_spec or request.model or {}).get("kind")
    allowed_commands = request.allowed_commands
    if kind == "claude_sdk":
        allowed_commands = sorted(_SDK_BASE_SHELL_COMMANDS.union(allowed_commands or []))
    if allowed_commands is not None:
        spec["allowed_commands"] = allowed_commands
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
    # Audit cluster C1 (F51): a REVIEW run MUST carry its review context to the
    # container. The orchestrator builds `review_context` (acceptance criteria +
    # the implementer's prior output + the <test-report>) but until now the worker
    # never forwarded it, so the reviewer ran blind on title+description, produced
    # no <verdict>, and the worker defensively rejected every reviewed task
    # (in_review→backlog→blocked). The runtime reads `review` to build the
    # reviewer's verdict-instruction preamble (`build_review_preamble`).
    if request.review:
        spec["review"] = True
        if request.review_context is not None:
            spec["review_context"] = request.review_context
    # Inter-run reviewer feedback (A2): a re-dispatched IMPLEMENTER run carries the
    # AI reviewer's prior rejection payloads so the runtime can fold them into a
    # corrective preamble (`build_prior_feedback_preamble`). Only emit when present
    # (`None`/absent = no prior rejection) — "no key" is the unchanged behaviour for
    # a first dispatch (backward-compat). Independent of the REVIEW keys above: this
    # is the implementer being told what to fix, not the reviewer judging.
    if request.prior_review_feedback is not None:
        spec["prior_review_feedback"] = request.prior_review_feedback
    # Feature C: human task/plan comments → the runtime folds them into a contextual
    # preamble (`build_comments_preamble`). Only emit when present (backward-compat).
    if request.task_comments is not None:
        spec["task_comments"] = request.task_comments
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
    acceptance_criteria: list[Any] | None = None,
    wall_clock_budget_s: float | None = None,
    max_iterations_budget: int | None = None,
    max_tokens_budget: int | None = None,
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
        "AGENT_TASK_SPEC": json.dumps(
            _agent_spec(
                request,
                approval_policy,
                model_spec=model_spec,
                acceptance_criteria=acceptance_criteria,
                wall_clock_budget_s=wall_clock_budget_s,
                max_iterations_budget=max_iterations_budget,
                max_tokens_budget=max_tokens_budget,
            )
        ),
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


def _scan_logs_for_terminal(logs: str) -> tuple[dict[str, Any] | None, str | None]:
    """Re-parse the COMPLETE captured container logs for a terminal event the live
    stream dropped (F16/P1.1).

    The live drain pumps lines as they arrive and a torn follow read can lose the
    final `execution.finished`/`execution.error` line even though the container
    emitted it (Fase 1 guarantees ``ContainerResult.logs`` holds the full capture).
    Returns ``(finished_result, error)`` — the LAST ``execution.finished`` ``result``
    payload found (or ``None``) and the LAST ``execution.error`` message (or ``None``).
    """
    finished: dict[str, Any] | None = None
    error: str | None = None
    for line in logs.splitlines():
        event = _parse_line(line)
        if event is None or not event.get("event"):
            continue
        kind = str(event["event"])
        if kind == "execution.finished":
            result = event.get("result")
            if isinstance(result, dict):
                finished = result
        elif kind == "execution.error":
            error = event.get("error")
    return finished, error


def _assemble_result(
    final_result: dict[str, Any] | None,
    steps: list[dict[str, Any]],
    *,
    timed_out: bool,
    exit_code: int,
    runtime_error: str | None,
    logs: str | None = None,
) -> _RuntimeResult:
    """Fold the streamed steps + final result line into a `_RuntimeResult`.

    When the container produced an `execution.finished` line, that is
    the result. Otherwise the run failed (crash, timeout, or an
    `execution.error` line) — keep whatever steps streamed and mark it
    `failed`.

    F16/P1.1: before declaring a clean exit (exit 0, no timeout, no
    `execution.error`) a failure, re-parse the COMPLETE captured ``logs`` for a
    terminal line the live drain missed — the container DID emit
    `execution.finished`, the worker just lost it on the wire.
    """
    if final_result is not None:
        return _RuntimeResult(
            status=final_result.get("status", "failed"),
            abort_code=final_result.get("abort_code"),
            output=final_result.get("output"),
            iterations=int(final_result.get("iterations", 0)),
            steps=steps,
            usage=final_result.get("usage") or dict(_EMPTY_USAGE),
            finish_status=final_result.get("finish_status"),
        )

    # F16/P1.1: a clean exit with no result on the live stream — recover from the
    # full log capture before treating it as a failure. Only for an otherwise-clean
    # exit (exit 0, no timeout, no live error); a crash/timeout keeps the hard path.
    if logs and exit_code == 0 and not timed_out and runtime_error is None:
        recovered, recovered_error = _scan_logs_for_terminal(logs)
        if recovered is not None:
            return _RuntimeResult(
                status=recovered.get("status", "failed"),
                abort_code=recovered.get("abort_code"),
                output=recovered.get("output"),
                iterations=int(recovered.get("iterations", 0)),
                steps=steps,
                usage=recovered.get("usage") or dict(_EMPTY_USAGE),
                finish_status=recovered.get("finish_status"),
            )
        if recovered_error is not None:
            runtime_error = recovered_error

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
    """El store de Vault del worker, construido desde la config DEL WORKER.

    El worker corre con su propia config (``WORKERS_VAULT_URL`` /
    ``WORKERS_VAULT_TOKEN``) y NO lleva el env ``API_SERVER_VAULT_*`` que el
    builder del api-server (``get_provider_vault_store``) lee — usarlo aquí
    devolvía ``None`` en el worker, así que la credencial del proveedor NUNCA se
    leía de Vault y toda ejecución corría con ``has_credential=False``.

    Construimos el store con el MISMO :class:`HvacLLMProviderVaultStore` (mismo
    mount KV) que el api-server, pero a partir de los settings del worker.
    Devuelve ``None`` si no hay token (la resolución degrada a sin-credencial,
    suficiente para un Ollama local sin clave)."""
    from workers.config import get_settings

    settings = get_settings()
    if not settings.vault_token:
        return None
    import hvac
    from api_server.llm_providers.vault import HvacLLMProviderVaultStore

    client = hvac.Client(url=settings.vault_url, token=settings.vault_token)
    return HvacLLMProviderVaultStore(client=client)


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
      - ``needs_human_review`` (ADR 0087: the authoritative self-review could not
        certify the output — inconclusive verdict / exhausted retries) -> ``blocked``;
        the deliverable is preserved on the execution row and the human inbox
        surfaces the blocked task with the motive (``abort_code`` =
        ``review_inconclusive`` / ``max_review_retries_exhausted``). At the TASK
        level there is no ``pending_human_validation`` (that is a PLAN status,
        CLAUDE.md ppio 7), so escalation REUSES the existing ``blocked`` + inbox path.
      - ``cancelled`` (operator cancel) -> ``cancelled`` (F11): an operator cancel
        is NOT a failure, so it must land the task in ``cancelled``, not ``blocked``.
        ``in_progress -> cancelled`` is a legal §7.2 move.
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
    elif result_status == "cancelled":
        # F11: an operator cancel is not a failure — land the task in `cancelled`,
        # not `blocked`. `in_progress -> cancelled` is a legal §7.2 move.
        target = TaskStatus.CANCELLED.value
    else:
        # `needs_human_review` and the hard-failure codes all converge on
        # `blocked`; the execution row's status + abort_code distinguish an
        # escalation-for-validation from a genuine failure for the inbox/UI.
        target = TaskStatus.BLOCKED.value
    transition_task_status(task, target)
    if task.status == TaskStatus.DONE.value:
        task.completed_at = datetime.now(UTC)
    if task.status == old_status:
        return None
    return (task, old_status, task.status)


async def _apply_review_verdict(
    session: AsyncSession,
    task_id: UUID,
    tenant_id: UUID,
    result: _RuntimeResult,
) -> tuple[Task, str, str] | None:
    """Apply an AI reviewer run's verdict to the reviewed task (prod-17 loop_03).

    Parses the reviewer's stdout (``<verdict>…</verdict>`` tags) and calls
    ``apply_reviewer_verdict``: approve → ``done``, reject → ``backlog`` (or
    ``blocked`` once ``max_retries`` is hit). An UNPARSEABLE verdict (``unknown``)
    is treated as a defensive ``reject`` so the task converges instead of stalling
    in ``in_review`` (a bounded re-prompt is a future refinement — ADR 0084 / plan
    decision 6). Returns ``(task, old, new)`` for event publication, or ``None``
    when no transition applies (task already moved / guarded by apply)."""
    from api_server.db.task_audit_repo import append_audit_event
    from api_server.reviewer_bridge import (
        ReviewerVerdict,
        apply_reviewer_verdict,
        parse_reviewer_output,
    )

    task = await session.get(Task, task_id)
    if task is None or task.status != TaskStatus.IN_REVIEW.value:
        return None
    old_status = task.status
    verdict = parse_reviewer_output(result.output or "")
    if verdict.label == "unknown":
        # Audit C1 (F03/P0.2): a missing verdict means one of two very different
        # things. (a) The reviewer RUN finished cleanly (`done`) but the model did
        # not format a verdict → a defensive reject keeps the task converging
        # (bounded by retry_count, which escalates to `blocked`). (b) The reviewer
        # RUN failed at infra level (crash / timeout / cancel / model_unresolved →
        # status != done): `result.output` is an error string, NOT a judgement.
        # Treating that as a reject re-implements a possibly-correct task. So we
        # re-dispatch (now the reviewer SEES the code — ADR 0095 — so it should
        # converge), but CAP it: ADR 0095 D3 bumps retry_count and, at max_retries,
        # escalates the task to a human (`blocked`) instead of an infinite
        # in_review ↔ re-dispatch loop. Below the cap, stay in_review.
        if result.status != "done":
            task.retry_count += 1
            _log.warning(
                "workers.review_infra_error",
                task_id=str(task_id),
                review_status=result.status,
                abort_code=result.abort_code,
                retry_count=task.retry_count,
            )
            if task.retry_count >= task.max_retries:
                transition_task_status(task, TaskStatus.BLOCKED.value)
                await append_audit_event(
                    session,
                    tenant_id=tenant_id,
                    task_id=task_id,
                    kind="review_comment",
                    actor="ai-reviewer",
                    payload={
                        "escalated": True,
                        "reason": "review_inconclusive",
                        "abort_code": result.abort_code,
                        "retry_count": task.retry_count,
                    },
                )
                await session.flush()
                return (task, old_status, task.status)
            return None
        verdict = ReviewerVerdict(
            label="reject",
            failed_criterion="reviewer produced no parseable verdict",
            what_to_fix="re-run the review and end with a <verdict>approve|reject</verdict> tag",
        )
    if verdict.label == "approve" and result.status != "done":
        # ADR 0096 (auditoría 2026-07-02, F1.2): un run de review que NO terminó
        # `done` (escalado needs_human_review o abortado) no puede CERRAR la
        # task con su approve — "escalar a humano" y "aprobar automáticamente"
        # son contradictorios (2 tasks pasaron a done sin el humano que el
        # propio run pedía). El approve se degrada a recomendación: la task va
        # a `blocked` (panel de escaladas) con el verdict anotado para que el
        # humano lo confirme con `approve_manual`. El REJECT de un run no-done
        # SÍ se aplica (dirección conservadora; caso beneficioso 019f1828).
        transition_task_status(task, TaskStatus.BLOCKED.value)
        await append_audit_event(
            session,
            tenant_id=tenant_id,
            task_id=task_id,
            kind="review_comment",
            actor="ai-reviewer",
            payload={
                "escalated": True,
                "reason": "escalated_review_approve",
                "verdict": "approve",
                "review_status": result.status,
                "abort_code": result.abort_code,
            },
        )
        await session.flush()
        return (task, old_status, task.status)
    await apply_reviewer_verdict(session, task_id=task_id, tenant_id=tenant_id, verdict=verdict)
    # apply_* loaded the SAME identity-mapped Task in this session → status updated.
    if task.status == old_status:
        return None
    return (task, old_status, task.status)


class RepoHistoryLostError(RuntimeError):
    """El bare repo del proyecto ya no contiene el historial del plan aunque el
    plan tiene tareas completadas — el data_root fue arrasado/sustituido (p. ej.
    el engine-restart de Docker Desktop del 2026-07-02 recreó el bind vacío).
    Re-seedear un repo VACÍO y dejar correr al agente fabricaría un estado roto
    en silencio (churn estéril + escalada confusa); el run debe abortar en
    segundos con ``abort_code=repo_history_lost`` y un motivo accionable."""


async def _provision_worktree(
    settings: Settings,
    *,
    tenant_slug: str,
    project_slug: str,
    plan_id: str,
    plan_slug: str,
    task_id: str,
    expect_plan_history: bool = False,
) -> str | None:
    """Materialise the per-task git worktree and return its host path (prod-18
    task_prod18_provision_01 / ADR 0085).

    Ensures the project's bare repo, adds a worktree for ``task_id`` on the plan
    branch (``plan/{id8}-{slug}``, HEAD detached so sibling tasks share it), and
    syncs it to the branch HEAD — reusing the Plan 06 libraries. The returned path
    is the absolute HOST path the daemon resolves for the ``/workspace`` bind (DooD).
    Any failure logs and returns ``None``; el CALLER decide la política — para un
    run implementador que esperaba worktree eso es fail-fast `workspace_unavailable`
    (auditoría 2026-07-02 F0.2), no un fallback silencioso a tmpfs.

    ``expect_plan_history=True`` (guarda 2026-07-03): el plan ya tiene tareas
    completadas, así que el bare DEBE contener la rama del plan con contenido.
    Si la rama no existe (repo recién re-seedeado) o su checkout está vacío,
    lanza :class:`RepoHistoryLostError` en vez de fabricar un workspace vacío."""
    from pathlib import Path

    from workers.git_repos import BareRepoLayout, BareRepoManager, WorktreeManager
    from workers.plan_git import make_plan_branch_name

    def _git() -> str:
        layout = BareRepoLayout(
            data_root=Path(settings.data_root),
            tenant_slug=tenant_slug,
            project_slug=project_slug,
        )
        repo_name = project_slug  # ADR 0085 decision 2: one bare repo per project (MVP).
        branch = make_plan_branch_name(plan_id, plan_slug)
        mgr = BareRepoManager(layout)
        mgr.ensure_repo(repo_name)
        # A fresh local bare (no remote/clone) is empty → seed a root commit so the
        # worktree can branch off a valid HEAD.
        seeded = mgr.seed_initial_commit_if_empty(repo_name)
        wt = WorktreeManager(layout, repo_name)
        if expect_plan_history and not wt.branch_exists(branch):
            raise RepoHistoryLostError(
                f"La rama del plan '{branch}' no existe en el bare repo "
                f"{layout.bare_repo_path(repo_name)} pese a que el plan tiene tareas "
                "completadas"
                + (" (el repo se acaba de re-seedear vacío)" if seeded else "")
                + " — el historial del proyecto se perdió (¿wipe/pérdida de data_root?)."
            )
        path = wt.add(task_id, branch=branch)
        wt.sync_to_head(task_id, branch=branch)
        if expect_plan_history and not any(entry.name != ".git" for entry in Path(path).iterdir()):
            raise RepoHistoryLostError(
                f"El checkout de la rama '{branch}' está VACÍO pese a que el plan tiene "
                "tareas completadas — los commits previos ya no están en el bare repo "
                "(historial perdido). No se lanza al agente sobre un workspace vacío."
            )
        return str(path)

    try:
        return await asyncio.to_thread(_git)
    except RepoHistoryLostError:
        raise
    except Exception as exc:  # pragma: no cover - defensive: never fail the run on git
        _log.warning("workers.worktree_provision_failed", task_id=task_id, error=str(exc))
        return None


def _resolve_review_worktree(
    settings: Settings, tenant_slug: str, project_slug: str, task_id: str
) -> str | None:
    """Resolve the implementer's EXISTING per-task worktree for a READ-ONLY review
    mount (ADR 0095).

    No git operations — the directory reflects the post-implementation state
    (committed + uncommitted) exactly as the implementer left it; the reviewer
    only reads it. Returns the host path, or ``None`` when the worktree does not
    exist (the implementer ran in an ephemeral tmpfs) so the reviewer falls back
    to an empty ``/workspace``."""
    from pathlib import Path

    from workers.git_repos import BareRepoLayout

    layout = BareRepoLayout(
        data_root=Path(settings.data_root),
        tenant_slug=tenant_slug,
        project_slug=project_slug,
    )
    path = layout.worktree_path(task_id)
    return str(path) if path.is_dir() else None


async def _commit_and_push_worktree(
    settings: Settings,
    *,
    host_path: str,
    tenant_slug: str,
    project_slug: str,
    plan_id: str,
    plan_slug: str,
    task_id: str,
    execution_id: str,
    escalated: bool = False,
) -> bool:
    """Commit the agent's worktree output (with the mandatory trailers) and push it
    to the plan branch on the local bare repo (prod-18 task_prod18_commit_01 / ADR 0085).

    The WORKER does this — the sandbox has no git credentials (principle 2). When
    ``escalated`` the run did not certify the output (``needs_human_review``); the
    commit is labelled WIP so the human validator can tell it apart from a clean
    ``done`` (P2.3/F26). The bare→remote push stays with the existing ``open_plan_pr``
    path (final_only at plan close).

    Returns ``True`` when a REAL git error prevented the commit/push — the caller
    stamps a visible ``commit_failed`` marker so we never report a deliverable with
    an empty diff (P2.3/F13). Returns ``False`` when the commit succeeded OR the tree
    was legitimately clean (the agent produced no file change — a no-op, not an error).
    """
    from pathlib import Path

    from workers.git_repos import BareRepoLayout, GitCommandError
    from workers.plan_git import (
        CommitTrailers,
        PlanGitPolicies,
        PlanGitWorkflow,
        commit_task,
        make_plan_branch_name,
    )

    message = (
        f"wip(escalated): task {task_id} — needs human review" if escalated else f"task {task_id}"
    )

    def _git() -> str | None:
        layout = BareRepoLayout(
            data_root=Path(settings.data_root),
            tenant_slug=tenant_slug,
            project_slug=project_slug,
        )
        branch = make_plan_branch_name(plan_id, plan_slug)
        try:
            sha = commit_task(
                Path(host_path),
                message=message,
                trailers=CommitTrailers(
                    plan_id=plan_id, task_id=task_id, execution_id=execution_id
                ),
            )
        except GitCommandError as exc:
            if "clean" in str(exc).lower():
                return None  # agent produced no file change — not an error
            raise
        PlanGitWorkflow(
            bare_repo_path=layout.bare_repo_path(project_slug),
            plan_branch=branch,
            policies=PlanGitPolicies(),
        ).push_review_to_bare(Path(host_path))
        return sha

    try:
        sha = await asyncio.to_thread(_git)
        if sha is not None:
            _log.info(
                "workers.worktree_committed", task_id=task_id, sha=sha[:8], escalated=escalated
            )
        return False
    except Exception as exc:  # pragma: no cover - requires a live git failure
        # P2.3(b)/F13: a REAL git failure (NOT a clean tree) must be VISIBLE — the
        # run reported a deliverable but produced no diff. Signal the caller to
        # stamp a `commit_failed` marker instead of silently reporting success.
        _log.warning("workers.worktree_commit_failed", task_id=task_id, error=str(exc))
        return True


async def _mark_commit_failed(
    sessionmaker: async_sessionmaker[AsyncSession], execution_id: UUID
) -> None:
    """Stamp a visible ``commit_failed`` marker on a finalised execution whose
    worktree commit/push hit a real git error (P2.3(b)/F13).

    The run already reported a deliverable, but it never reached the plan branch —
    surface that on the execution row (``abort_code`` + an appended ``output`` note)
    instead of silently reporting success with an empty diff. Best-effort: opens its
    own short txn on the BYPASSRLS worker engine; a failure here never breaks the run.
    """
    try:
        async with sessionmaker() as session, session.begin():
            execution = await get_execution(session, execution_id)
            if execution is None:
                return
            execution.abort_code = "commit_failed"
            note = "worktree commit/push failed — deliverable not persisted to the plan branch"
            execution.output = f"{execution.output}\n{note}" if execution.output else note
    except Exception as exc:  # pragma: no cover - defensive best-effort
        _log.warning(
            "workers.commit_failed_marker_error", execution_id=str(execution_id), error=str(exc)
        )


async def _run_task_tests(
    settings: Settings,
    *,
    tenant_id: UUID,
    task_id: UUID,
    worktree_host_path: str,
    acceptance_criteria: list[Any],
) -> None:
    """Run the project's automated tests in the test-runtime over the agent's
    worktree and persist the TestReport (prod-18 task_prod18_test_01).

    Closes the loop so the AI reviewer (prod-17 task_prod17_test_02) finds a real
    ``<test-report>`` when it is dispatched. Only runs when the task carries
    automated acceptance criteria; Docker-aware (``run_test_runtime`` falls back to
    a stub when no daemon). Best-effort — a test-runtime failure never breaks the
    finished agent run (the task still moves to review)."""
    autos = [
        c
        for c in acceptance_criteria
        if isinstance(c, dict) and c.get("runtime") and c.get("command")
    ]
    if not autos:
        return
    from workers.tasks import _run_test_runtime

    test_request = {
        "tenant_id": str(tenant_id),
        "task_id": str(task_id),
        "acceptance_criteria": autos,
        "worktree_host_path": worktree_host_path,
    }
    try:
        await _run_test_runtime(test_request, settings)
    except Exception as exc:  # pragma: no cover - never break a finished run on tests
        _log.warning("workers.task_tests_failed", task_id=str(task_id), error=str(exc))


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
    # The task's git worktree host path (prod-18), set when an implementer run is
    # provisioned with one; used to bind /workspace and, on success, to commit +
    # push the agent's output (Fase C). `None` keeps the legacy tmpfs behaviour.
    workspace_host_path: str | None = None
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
        # Eligibility guard (R5): a re-delivered message (acks_late, e.g. after a
        # worker restart that recovers an R1 hang) must NOT launch a runtime for a
        # task the operator has since moved out of the launchable state (the
        # "phantom docker" on a `blocked` task). Skip BEFORE creating the
        # execution / provisioning the worktree / launching the container. The
        # early return commits the (orphan-closing) supersede above and ACKs the
        # Celery message — no re-queue.
        if not _task_is_launchable(task.status, is_review=request.review):
            _log.warning(
                "workers.ineligible_task_skipped",
                task_id=str(task_id),
                status=task.status,
                is_review=request.review,
            )
            return ExecutionOutcome(
                execution_id="", status="skipped", abort_code="ineligible_task_status"
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
        # prod-18 task_prod18_provision_01: gather the (stable) slugs needed to
        # materialise the task's git worktree. An IMPLEMENTER run gets a fresh RW
        # worktree; a REVIEW run mounts the implementer's existing worktree READ-ONLY
        # so the reviewer can read the code (ADR 0095 — was blind on review_context
        # only). Missing any → no worktree (empty tmpfs).
        worktree_inputs: tuple[str, str, str, str] | None = None
        review_worktree: tuple[str, str] | None = None  # (tenant_slug, project_slug), read-only
        # The task's automated acceptance criteria, captured here (the session closes
        # below) to drive the test-runtime after the agent commits (prod-18 test_01).
        task_acceptance_criteria: list[Any] = list(task.acceptance_criteria or [])
        if task.plan_id is not None and project is not None and project.slug:
            plan = await session.get(Plan, task.plan_id)
            org = await session.get(Organization, tenant_id)
            if plan is not None and plan.slug and org is not None and org.slug:
                if request.review:
                    review_worktree = (org.slug, project.slug)
                else:
                    worktree_inputs = (org.slug, project.slug, str(plan.id), plan.slug)
        # Guarda repo_history_lost (2026-07-03): si el plan ya tiene tareas
        # completadas, el bare repo DEBE contener la rama del plan con su
        # historial — un data_root recién arrasado (incidente 2026-07-02) no
        # debe re-seedearse en silencio como repo vacío para este plan.
        plan_has_prior_work = False
        if worktree_inputs is not None:
            prior = await session.scalar(
                select(func.count())
                .select_from(Task)
                .where(
                    Task.plan_id == task.plan_id,
                    Task.id != task_id,
                    Task.status.in_((TaskStatus.DONE.value, TaskStatus.IN_REVIEW.value)),
                )
            )
            plan_has_prior_work = bool(prior)
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
    # prod-18 task_prod18_provision_01: materialise the task's git worktree
    # OUTSIDE the DB transaction (git subprocess I/O) and bind-mount it RW as
    # /workspace so the agent's file writes persist (the worker commits them in
    # Fase C). Auditoría 2026-07-02 (F0.2): para un run IMPLEMENTADOR que
    # esperaba worktree, un fallo de provisión ya NO degrada a tmpfs "a ciegas"
    # (el agente quemaba 50 iteraciones alucinando entregables sobre un
    # workspace vacío) — se aborta ANTES de lanzar el contenedor. El fallback a
    # tmpfs se conserva para reviews (ADR 0095) y tasks sin plan/slugs.
    workspace_read_only = False
    workspace_error: str | None = None
    workspace_error_code = "workspace_unavailable"
    if resolution_error is None:
        if worktree_inputs is not None:
            tenant_slug, project_slug, plan_id_str, plan_slug = worktree_inputs
            try:
                workspace_host_path = await _provision_worktree(
                    settings,
                    tenant_slug=tenant_slug,
                    project_slug=project_slug,
                    plan_id=plan_id_str,
                    plan_slug=plan_slug,
                    task_id=str(task_id),
                    expect_plan_history=plan_has_prior_work,
                )
            except RepoHistoryLostError as exc:
                # Guarda 2026-07-03: NO fabricar un workspace vacío para un plan
                # con trabajo previo — abortar con motivo accionable.
                workspace_host_path = None
                workspace_error_code = "repo_history_lost"
                workspace_error = (
                    f"{exc} Restaura el backup de data_root (o re-ejecuta el plan "
                    "desde cero) y relanza la tarea."
                )
            if workspace_host_path is None and workspace_error is None:
                workspace_error = (
                    "No se pudo provisionar el worktree git de la tarea (data_root "
                    f"'{settings.data_root}' inaccesible o fallo git). Ejecución abortada "
                    "antes de lanzar el contenedor para no correr sin workspace. Revisa "
                    f"la propiedad de {settings.data_root} (uid 1000) y relanza la tarea."
                )
        elif review_worktree is not None:
            # ADR 0095: mount the implementer's existing worktree READ-ONLY for the
            # reviewer. No git ops, no commit (worktree_inputs stays None). Missing
            # worktree → empty /workspace (the reviewer still has review_context).
            r_tenant_slug, r_project_slug = review_worktree
            workspace_host_path = _resolve_review_worktree(
                settings, r_tenant_slug, r_project_slug, str(task_id)
            )
            workspace_read_only = workspace_host_path is not None

    failfast: tuple[str, str] | None = None
    if resolution_error is not None:
        # Fail-fast (ADR 0057 F1): sin proveedor resoluble NO se lanza el
        # contenedor — la ejecución termina `failed` con motivo explícito en
        # vez de correr en silencio con el cliente scripted.
        _log.error("workers.model_resolution_failed", execution_id=exec_id, error=resolution_error)
        failfast = ("model_unresolved", resolution_error)
    elif workspace_error is not None:
        # Fail-fast (F0.2): sin workspace NO se lanza el contenedor. El código
        # distingue el data_root inaccesible (`workspace_unavailable`) del
        # historial perdido (`repo_history_lost`, guarda 2026-07-03).
        _log.error(
            "workers.workspace_unavailable",
            execution_id=exec_id,
            task_id=request.task_id,
            data_root=settings.data_root,
            abort_code=workspace_error_code,
        )
        failfast = (workspace_error_code, workspace_error)
    if failfast is not None:
        failfast_code, failfast_msg = failfast
        await publish_execution_event(
            redis, exec_id, event_type="execution.error", payload={"error": failfast_msg}
        )
        result = _RuntimeResult(
            status="failed",
            abort_code=failfast_code,
            output=failfast_msg,
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
        # Per-provider wall-clock budget: claude_sdk spawns the Node CLI and its
        # high-effort/xhigh model calls are slow, so it gets a much longer budget
        # than the fast HTTP providers (ollama/azure_foundry/copilot). F19: the
        # agent loop's INTERNAL wall-clock safeguard uses the bare per-kind budget,
        # while the container's HARD kill gets a grace margin ON TOP — so the
        # internal abort (`max_wall_clock_exceeded`, keeping partials + finish_status)
        # fires FIRST and the container kill is only a last-resort backstop, instead
        # of the kill always winning and mislabelling every exhaustion as
        # 'container timed out'. (The internal default 600s would otherwise still
        # abort a long claude_sdk run early — aligning them fixes that too.)
        resolved_kind = (resolved_model or {}).get("kind")
        # F2b.5: los runs de REVIEW usan su presupuesto propio, más corto
        # (25 iter / 1h) — la evidencia post-ADR-0095 muestra reviews
        # convergiendo en 13-22 steps; el de implementador es 50 iter / 2h.
        wall_clock_budget_s = settings.container_timeout_for_kind(
            resolved_kind, is_review=request.review
        )
        container_timeout = settings.container_timeout_with_grace_for_kind(
            resolved_kind, is_review=request.review
        )
        container_spec = ContainerSpec(
            image=settings.agent_runtime_image,
            env=_build_runtime_env(
                request,
                approval_policy,
                agent_internal_api_url=settings.agent_internal_api_url,
                # El spec RESUELTO (kind + endpoint + credencial) — ADR 0057 F1.
                model_spec=resolved_model,
                # La definición de "hecho" de la tarea → al prompt de decisión,
                # para que el comportamiento (leer/escribir/test) lo dicte la tarea.
                acceptance_criteria=task_acceptance_criteria,
                # Budget interno del loop = el del contenedor MENOS el grace (F19):
                # el aborto limpio del loop gana al kill duro del contenedor.
                wall_clock_budget_s=wall_clock_budget_s,
                # Tope de iteraciones por-provider (claude_sdk necesita más para
                # escribir todos los ficheros Y finalizar); un run de REVIEW usa
                # su cap propio, más bajo (F2b.5).
                max_iterations_budget=settings.agent_max_iterations_for_kind(
                    resolved_kind, is_review=request.review
                ),
                # Presupuesto de tokens por-provider: con usage real (F1.4) el
                # default de 100k cortaba runs sanos de claude_sdk a ~23 iter.
                max_tokens_budget=settings.agent_max_tokens_for_kind(
                    resolved_kind, is_review=request.review
                ),
            ),
            labels={"com.agentic-platform.execution-id": exec_id},
            workspace_host_path=workspace_host_path,
            workspace_read_only=workspace_read_only,
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
            # `container_timeout` = the per-kind budget + grace (F19): the hard
            # backstop, set ABOVE the loop's internal wall-clock so the clean abort wins.
            container_result = await asyncio.to_thread(
                active_runner.run_streamed, container_spec, on_line, timeout=container_timeout
            )
        finally:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher
        await queue.put(None)
        await drainer

        # P3.3/F15: a cancel sealed between the watcher's last poll and the
        # container exit is missed by the watcher. Do a final one-shot read of
        # `cancel_requested_at` so an operator cancel is never lost to that race.
        if not cancel_seen:
            async with sessionmaker() as cancel_session:
                ex = await get_execution(cancel_session, execution_id)
            if ex is not None and ex.cancel_requested_at is not None:
                cancel_seen = True

        result = _assemble_result(
            final_result,
            steps,
            timed_out=container_result.timed_out,
            exit_code=container_result.exit_code,
            runtime_error=runtime_error,
            # F16/P1.1: the FULL captured log to recover a dropped terminal line.
            logs=container_result.logs,
        )
        if cancel_seen and final_result is None:
            # P3.3/F20: only force `cancelled` when the run did NOT actually finish.
            # If `final_result` is present the container reached `execution.finished`
            # before the kill — preserve its real output/finish_status instead of
            # masking a completed run as cancelled. (A killed container exits non-zero,
            # so the F16 log-recovery above does not fire here.)
            result = _RuntimeResult(
                status="cancelled",
                abort_code="cancelled",
                output="cancelled by operator",
                iterations=result.iterations,
                steps=result.steps,
                usage=result.usage,
                finish_status=result.finish_status,
            )
        approval = final_result.get("approval") if final_result else None

    # F12: the runtime parked on `awaiting_human_approval` but emitted NO approval
    # payload, so we cannot build an ApprovalRequest. Falling to the implementer path
    # would strand the task `in_progress` with no inbox item. Treat the combination as
    # invalid: rewrite the result to `failed` with an explicit abort_code so the run
    # finalises failed and the task is blocked with a motive — never a silent stall.
    if not request.review and result.status == _AWAITING_APPROVAL and not approval:
        _log.error(
            "workers.approval_payload_missing", execution_id=exec_id, task_id=request.task_id
        )
        result = _RuntimeResult(
            status="failed",
            abort_code="approval_payload_missing",
            output="runtime reported awaiting_human_approval but emitted no approval payload",
            iterations=result.iterations,
            steps=result.steps,
            usage=result.usage,
            finish_status=result.finish_status,
        )

    task_event: tuple[Any, str, str] | None = None
    # P0.5: for the implementer path the task transition is persisted ATOMICALLY with
    # finalize (same txn) so a crash here can never leave the execution terminal but
    # the task `in_progress` forever. Only the EVENT publication is deferred until
    # after the worktree commit exists (prod-18 ordering), so a dispatched
    # reviewer/validator finds the committed diff + the <test-report>. The review +
    # approval paths transition inside the txn too and publish immediately (no git).
    implementer_path = False
    async with sessionmaker() as session, session.begin():
        await finalize_execution(session, execution_id, result=result)
        # A run parked on a sensitive action becomes a real
        # ApprovalRequest — the approval engine on the live run (task_02_33).
        # request_approval_if_needed also moves the TASK to
        # `awaiting_human_approval` and frees its agent (ADR 0020).
        if request.review:
            # prod-17 task_prod17_loop_03: this run was the AI reviewer reviewing
            # the task. Apply its parsed verdict (approve -> done, reject ->
            # backlog/blocked) instead of the normal post-run transition.
            task_event = await _apply_review_verdict(session, task_id, tenant_id, result)
        elif result.status == _AWAITING_APPROVAL and approval:
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
            # P0.5: transition ATOMICALLY with finalize (crash-safe). The resulting
            # in_review/blocked/cancelled/done event is published BELOW, after the
            # worktree commit exists (prod-18 ordering).
            implementer_path = True
            task_event = await transition_task_after_run(session, task_id, result.status)

    # Publish the review / approval event now (these paths have no git/test follow-up).
    if task_event is not None and not implementer_path:
        task_obj, old, new = task_event
        await publish_task_status_changed(redis, task_obj, old_status=old, new_status=new)

    # prod-18 implementer post-processing — commit + tests BEFORE publishing the
    # (already-persisted) state-change event:
    if implementer_path:
        # task_prod18_commit_01: a run that wrote into a worktree gets committed (with
        # trailers) + pushed to the plan branch by the WORKER (the sandbox has no git
        # credentials). P2.3/F26: commit for a clean `done` AND for an escalation
        # (`needs_human_review`) so the human validator gets the diff. task_prod18_
        # test_01: tests run over the worktree only for a `done` run. All best-effort.
        if (
            result.status in ("done", "needs_human_review")
            and workspace_host_path is not None
            and worktree_inputs is not None
        ):
            c_tenant_slug, c_project_slug, c_plan_id, c_plan_slug = worktree_inputs
            commit_failed = await _commit_and_push_worktree(
                settings,
                host_path=workspace_host_path,
                tenant_slug=c_tenant_slug,
                project_slug=c_project_slug,
                plan_id=c_plan_id,
                plan_slug=c_plan_slug,
                task_id=str(task_id),
                execution_id=exec_id,
                escalated=result.status == "needs_human_review",
            )
            if result.status == "done":
                await _run_task_tests(
                    settings,
                    tenant_id=tenant_id,
                    task_id=task_id,
                    worktree_host_path=workspace_host_path,
                    acceptance_criteria=task_acceptance_criteria,
                )
            if commit_failed:
                # P2.3(b)/F13: a real git failure — surface it on the execution row
                # instead of reporting a deliverable with an empty diff.
                await _mark_commit_failed(sessionmaker, execution_id)
        # Now publish the deferred state-change event (the commit, if any, exists, so
        # a reviewer/validator dispatched by it finds the diff).
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
