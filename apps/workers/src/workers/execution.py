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
from api_server.events import publish_execution_event
from api_server.task_state_machine import transition_task_status
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workers.config import Settings
from workers.container import AgentContainerRunner, ContainerSpec
from workers.memorizer import trigger_memorize
from workers.model_resolver import (
    ModelResolutionError,
    resolve_model_spec,
    safe_spec_summary,
)
from workers.run_contract import (
    CrossTenantExecutionError,
    ExecutionOutcome,
    ExecutionRequest,
)
from workers.run_result import (
    _EMPTY_USAGE,
    _assemble_result,
    _parse_line,
    _RuntimeResult,
    _scan_logs_for_terminal,
)
from workers.run_spec import (
    _SDK_BASE_SHELL_COMMANDS,
    _agent_spec,
    _resolve_tool_spec_images,
)

# Re-exports EXPLÍCITOS: la casa histórica de estos símbolos es este módulo —
# tasks/maintenance/tests siguen importando de workers.execution. `__all__`
# marca el re-export para mypy (no_implicit_reexport) y para ruff F401.
__all__ = [
    "CrossTenantExecutionError",
    "ExecutionOutcome",
    "ExecutionRequest",
    "conduct_execution",
    "transition_task_after_run",
    "_EMPTY_USAGE",
    "_RuntimeResult",
    "_SDK_BASE_SHELL_COMMANDS",
    "_agent_spec",
    "_assemble_result",
    "_parse_line",
    "_resolve_tool_spec_images",
    "_scan_logs_for_terminal",
]

_log = structlog.get_logger("workers.execution")

# Status the agent loop reports when it parks on a sensitive action —
# mirrors agent_runtime.state.STATUS_AWAITING_APPROVAL and
# ExecutionStatus.AWAITING_HUMAN_APPROVAL.
_AWAITING_APPROVAL = "awaiting_human_approval"

# How often the run polls `cancel_requested_at` while the container runs, to
# kill it cooperatively on an operator cancel (POST /executions/{id}/cancel).
_CANCEL_POLL_INTERVAL_S = 3.0


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
    guardrails: dict[str, Any] | None = None,
    conversation_thread: bool = False,
    reflection_assess: bool = False,
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
                guardrails=guardrails,
                conversation_thread=conversation_thread,
                reflection_assess=reflection_assess,
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


async def _resolve_effective_guardrails(
    session: AsyncSession, project: Project | None
) -> dict[str, Any] | None:
    """La config de guardrails EFECTIVA del run (ADR 0102 D3).

    Fusiona la capa PLATAFORMA (platform_settings.guardrails_config) con la
    capa PROYECTO (projects.guardrails_config) via resolve_config — los checks
    ``locked`` de plataforma no pueden relajarse. ``None`` cuando no hay capas
    (el runtime cae a su baseline LOG). Best-effort: un error aqui degrada a
    None (baseline), jamas rompe el dispatch. Cap 64KB (D3): si el resultado
    excede, se degrada a la capa plataforma sola con warning."""
    try:
        import json as _json

        from api_server.db import platform_settings
        from shared_guardrails.layers import LayerConfig, resolve_config

        platform_raw = await platform_settings.get_guardrails_config(session)
        project_raw = (
            dict(project.guardrails_config)
            if project is not None and project.guardrails_config
            else None
        )
        if not platform_raw and not project_raw:
            return None
        resolved = resolve_config(
            LayerConfig.from_dict("platform", platform_raw or None),
            None,
            LayerConfig.from_dict("project", project_raw) if project_raw else None,
        )
        if resolved.config.is_empty:
            return None
        out = resolved.config.to_dict()
        if len(_json.dumps(out)) > 64_000:
            _log.warning("workers.guardrails_config_over_cap", dropped_layer="project")
            platform_only = resolve_config(LayerConfig.from_dict("platform", platform_raw or None))
            out = platform_only.config.to_dict()
            if len(_json.dumps(out)) > 64_000:
                return None
        return out
    except Exception as exc:  # baseline del runtime como red de seguridad
        _log.warning("workers.guardrails_resolve_failed", error=str(exc))
        return None


async def _load_project(session: AsyncSession, task_id: UUID) -> Project | None:
    """The task's project — its `human_approval_policy` gates the run."""
    task = await session.get(Task, task_id)
    if task is None:
        return None
    return await session.get(Project, task.project_id)


async def _resolve_effective_approval_policy(
    session: AsyncSession, project: Project | None
) -> dict[str, Any] | None:
    """The approval policy that gates this run (A8b).

    A project's explicit ``human_approval_policy`` wins. When it is None/empty the
    run used to be FAIL-OPEN — the gate was never instantiated and every sensitive
    category ran in auto. Now a project without a policy inherits the platform
    DEFAULT preset (``default_approval_policy_preset`` setting, default
    ``development``): the coding-loop categories stay auto but comms / http_post /
    secrets / deploy / infra / PII / user_mgmt gate. The preset's decisions cover
    all canonical categories, so there is no unlisted-category gap (never fail-open)."""
    if project is not None and project.human_approval_policy:
        policy: dict[str, Any] = project.human_approval_policy
        return policy
    from api_server.db.platform_settings import get_platform_setting
    from api_server.seeds.builtin_approval_policies import (
        DEFAULT_APPROVAL_POLICY_PRESET,
        preset_decisions,
    )

    preset = await get_platform_setting(
        session, "default_approval_policy_preset", default=DEFAULT_APPROVAL_POLICY_PRESET
    )
    return preset_decisions(str(preset))


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

    from workers.git_repos import BareRepoManager, WorktreeManager
    from workers.plan_git import worktree_coordinates

    def _git() -> str:
        # Coordenadas ÚNICAS (hallazgo #10a): mismo (layout, branch) que resuelven la
        # resolución read-only, el commit/push, el review y el back-fill.
        layout, branch = worktree_coordinates(
            data_root=settings.data_root,
            tenant_slug=tenant_slug,
            project_slug=project_slug,
            plan_id=plan_id,
            plan_slug=plan_slug,
        )
        repo_name = project_slug  # ADR 0085 decision 2: one bare repo per project (MVP).
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
    to an empty ``/workspace``.

    El layout sale de ``worktree_layout`` — la MISMA primitiva que usa
    ``worktree_coordinates`` en la provisión (remate I-2, auditoría 2026-07-10):
    este path alimenta un bind DooD (mount read-only del reviewer, ADR 0095) y no
    puede divergir de donde el implementador dejó el worktree."""
    from workers.plan_git import worktree_layout

    layout = worktree_layout(
        data_root=settings.data_root,
        tenant_slug=tenant_slug,
        project_slug=project_slug,
    )
    path = layout.worktree_path(task_id)
    return str(path) if path.is_dir() else None


def _commit_abort_code(exc: Exception) -> str:
    """Classify a worktree commit/push failure into its ``abort_code`` (P7).

    A rebase CONFLICT (a sibling task changed the same lines — ``push_review_to_bare``
    raises a ``... conflicted ...`` error) needs a human to resolve it and is
    escalatable; any other git error is a generic ``commit_failed``.
    """
    return "rebase_conflict" if "conflict" in str(exc).lower() else "commit_failed"


async def _commit_and_push_worktree(
    settings: Settings,
    *,
    host_path: str,
    tenant_slug: str,
    project_slug: str,
    project_id: str,
    plan_id: str,
    plan_slug: str,
    task_id: str,
    execution_id: str,
    escalated: bool = False,
) -> tuple[str, dict[str, Any] | None] | None:
    """Commit the agent's worktree output (with the mandatory trailers) and push it
    to the plan branch on the local bare repo (prod-18 task_prod18_commit_01 / ADR 0085).

    The WORKER does this — the sandbox has no git credentials (principle 2). When
    ``escalated`` the run did not certify the output (``needs_human_review``); the
    commit is labelled WIP so the human validator can tell it apart from a clean
    ``done`` (P2.3/F26). The bare→remote push stays with the existing ``open_plan_pr``
    path (final_only at plan close).

    Returns the ``abort_code`` when a REAL git error prevented the commit/push —
    ``"rebase_conflict"`` when a sibling task changed the same lines (needs human
    resolution, P7) or ``"commit_failed"`` for any other git error — so the caller
    stamps a visible, escalatable marker instead of reporting a deliverable with an
    empty diff (P2.3/F13). Returns ``None`` when the commit succeeded OR the tree was
    legitimately clean (the agent produced no file change — a no-op, not an error).
    """
    from pathlib import Path

    from workers.git_repos import GitCommandError
    from workers.plan_git import (
        CommitTrailers,
        PlanGitPolicies,
        PlanGitWorkflow,
        commit_task,
        worktree_coordinates,
    )

    message = (
        f"wip(escalated): task {task_id} — needs human review" if escalated else f"task {task_id}"
    )

    def _git() -> str | None:
        layout, branch = worktree_coordinates(
            data_root=settings.data_root,
            tenant_slug=tenant_slug,
            project_slug=project_slug,
            plan_id=plan_id,
            plan_slug=plan_slug,
        )
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
            # P3/T3 (ADR 0085 dec.5): push the plan branch bare→remote per
            # branch_push_mode. The helper is best-effort and NEVER raises (the commit
            # is already durable in the bare), so a remote push failure or a local-only
            # project can't fail the task — it only affects the remote mirror.
            from workers.plan_pr import push_plan_branch_to_remote

            push_status = await push_plan_branch_to_remote(
                settings,
                project_id=UUID(project_id),
                plan_id=plan_id,
                plan_slug=plan_slug,
                tenant_slug=tenant_slug,
                project_slug=project_slug,
            )
            _log.info("workers.incremental_remote_push", task_id=task_id, status=push_status)
        return None
    except Exception as exc:  # pragma: no cover - requires a live git failure
        # P2.3(b)/F13 + P7: a REAL git failure (NOT a clean tree) must be VISIBLE — the
        # run reported a deliverable but produced no diff. A rebase CONFLICT (a sibling
        # task changed the same lines) gets its own escalatable abort_code so it lands
        # on the escalation panel; any other git error stays `commit_failed`.
        abort_code = _commit_abort_code(exc)
        _log.warning(
            "workers.worktree_commit_failed",
            task_id=task_id,
            abort_code=abort_code,
            error=str(exc),
        )
        # Anticipo ADR 0099: el contexto estructurado del conflicto (si la capa
        # git lo capturó antes del abort) viaja adherido a la excepción.
        return abort_code, getattr(exc, "conflict_context", None)


def _conflict_note(
    abort_code: str,
    conflict_context: dict[str, Any] | None,
    *,
    steps_len: int,
) -> tuple[str, dict[str, Any] | None]:
    """La nota humana + el step ESTRUCTURADO del marcador de commit fallido
    (anticipo ADR 0099). Puro (testeable): con contexto de conflicto devuelve
    ademas un step para steps_log con {plan_branch, files, worktree_sha,
    branch_sha} — los datos que el visor de diffs futuro necesita para mostrar
    ambos lados; sin contexto, la nota de texto historica y step None."""
    if abort_code == "rebase_conflict":
        note = (
            "worktree rebase conflicted with a sibling task — deliverable not "
            "persisted to the plan branch; needs human resolution"
        )
        if conflict_context:
            files = [str(f) for f in conflict_context.get("files") or []]
            if files:
                listed = "\n".join(f"- {f}" for f in files)
                note += f"\nconflicting files:\n{listed}"
            step = {
                "index": steps_len,
                "kind": "node",
                "node": "commit",
                "status": "error",
                "summary": f"Rebase conflict: {len(files)} file(s) in dispute",
                "conflict_context": dict(conflict_context),
            }
            return note, step
        return note, None
    return (
        "worktree commit/push failed — deliverable not persisted to the plan branch",
        None,
    )


async def _mark_commit_failed(
    sessionmaker: async_sessionmaker[AsyncSession],
    execution_id: UUID,
    abort_code: str = "commit_failed",
    conflict_context: dict[str, Any] | None = None,
) -> None:
    """Stamp a visible ``abort_code`` marker on a finalised execution whose
    worktree commit/push hit a real git error (P2.3(b)/F13, P7).

    The run already reported a deliverable, but it never reached the plan branch —
    surface that on the execution row (``abort_code`` + an appended ``output`` note)
    instead of silently reporting success with an empty diff. ``rebase_conflict``
    (P7) is escalatable — it lands on the escalation panel with a resolution note.
    Best-effort: opens its own short txn on the BYPASSRLS worker engine; a failure
    here never breaks the run.
    """
    try:
        async with sessionmaker() as session, session.begin():
            execution = await get_execution(session, execution_id)
            if execution is None:
                return
            execution.abort_code = abort_code
            note, conflict_step = _conflict_note(
                abort_code, conflict_context, steps_len=len(execution.steps_log or [])
            )
            execution.output = f"{execution.output}\n{note}" if execution.output else note
            if conflict_step is not None:
                # Anticipo ADR 0099: el contexto estructurado viaja en steps_log
                # (JSONB ya renderizado por el visor y consultable por SQL).
                execution.steps_log = [*(execution.steps_log or []), conflict_step]
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


async def _persist_guardrail_events(
    session: AsyncSession,
    result: _RuntimeResult,
    *,
    tenant_id: UUID,
    task_id: UUID,
    execution_id: UUID,
    agent_id: UUID | None,
) -> None:
    """Persist the runtime's post_tool guardrail events (ADR 0102 / g1) tenant-scoped.

    Runs inside the finalize transaction (same RLS session) but inside a SAVEPOINT,
    so a persistence failure can never roll back the already-finished execution —
    these events are LOG-mode observability, never a reason to fail a run.
    """
    events = result.guardrail_events or []
    if not events:
        return
    try:
        from api_server.guardrails.events import record_guardrail_event

        async with session.begin_nested():
            # Everything DB-touching lives INSIDE the SAVEPOINT: a failing SELECT
            # here (statement timeout / serialization failure) rolls back to the
            # savepoint and is caught below, so it can never poison the outer
            # finalize transaction (P0.5 atomic finalize). _load_project outside
            # the savepoint would leave the txn in a failed state and roll finalize
            # back on commit.
            project = await _load_project(session, task_id)
            project_id = project.id if project is not None else None
            for event in events:
                await record_guardrail_event(
                    session,
                    tenant_id=tenant_id,
                    guardrail_type=str(event.get("guardrail_type") or "unknown"),
                    hook_point=str(event.get("hook_point") or "post_tool"),
                    severity=str(event.get("severity") or "info"),
                    action=event.get("action"),
                    detail=str(event.get("detail") or ""),
                    detail_payload=event.get("detail_payload") or {},
                    project_id=project_id,
                    agent_id=agent_id,
                    execution_id=execution_id,
                )
    except Exception:
        _log.warning(
            "workers.guardrail_events_persist_failed",
            execution_id=str(execution_id),
            count=len(events),
        )


@dataclass
class _PreparedRun:
    """Salida de la fase de preparación (P3) — todo lo que la txn inicial deriva."""

    execution_id: UUID
    approval_policy: dict[str, Any] | None
    # ADR 0102 D3: config de guardrails resuelta (plataforma+proyecto) o None.
    guardrails: dict[str, Any] | None
    # (tenant_slug, project_slug, project_id, plan_id, plan_slug) del worktree RW
    # del implementador; None = sin worktree (tmpfs legacy / review / sin plan).
    worktree_inputs: tuple[str, str, str, str, str] | None
    # (tenant_slug, project_slug) del worktree del implementador que un run de
    # REVIEW monta READ-ONLY (ADR 0095); None = no es review / sin slugs.
    review_worktree: tuple[str, str] | None
    task_acceptance_criteria: list[Any]
    plan_has_prior_work: bool
    resolved_model: dict[str, Any] | None
    resolution_error: str | None


async def _prepare_run(
    session: AsyncSession,
    request: ExecutionRequest,
    *,
    task_id: UUID,
    tenant_id: UUID,
    vault_store: Any | None,
    celery_task_id: str | None,
) -> _PreparedRun | None:
    """Fase 1 (P3): frontera de tenant, idempotencia, elegibilidad, fila `running`
    y resolución de insumos — DENTRO de la txn del caller.

    ``None`` = tarea ya no elegible (R5): el caller commitea el supersede que esta
    fase dejó hecho y ACKa el mensaje sin lanzar nada."""
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
    superseded = await supersede_running_executions(session, tenant_id=tenant_id, task_id=task_id)
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
        return None
    execution = await create_running_execution(
        session,
        tenant_id=tenant_id,
        task_id=task_id,
        agent_id=UUID(request.agent_id) if request.agent_id else None,
        # prod-06 cancel_01: persist the Celery job id so an operator cancel
        # can `revoke` a still-queued/running job (was NULL → revoke dead code).
        celery_task_id=celery_task_id,
    )
    project = await session.get(Project, task.project_id)
    approval_policy = await _resolve_effective_approval_policy(session, project)
    guardrails_config = await _resolve_effective_guardrails(session, project)
    # prod-18 task_prod18_provision_01: gather the (stable) slugs needed to
    # materialise the task's git worktree. An IMPLEMENTER run gets a fresh RW
    # worktree; a REVIEW run mounts the implementer's existing worktree READ-ONLY
    # so the reviewer can read the code (ADR 0095 — was blind on review_context
    # only). Missing any → no worktree (empty tmpfs).
    worktree_inputs: tuple[str, str, str, str, str] | None = None
    review_worktree: tuple[str, str] | None = None  # (tenant_slug, project_slug), read-only
    if task.plan_id is not None and project is not None and project.slug:
        plan = await session.get(Plan, task.plan_id)
        org = await session.get(Organization, tenant_id)
        if plan is not None and plan.slug and org is not None and org.slug:
            if request.review:
                review_worktree = (org.slug, project.slug)
            else:
                worktree_inputs = (
                    org.slug,
                    project.slug,
                    str(project.id),
                    str(plan.id),
                    plan.slug,
                )
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
    return _PreparedRun(
        execution_id=execution.id,
        approval_policy=approval_policy,
        guardrails=guardrails_config,
        worktree_inputs=worktree_inputs,
        review_worktree=review_worktree,
        task_acceptance_criteria=list(task.acceptance_criteria or []),
        plan_has_prior_work=plan_has_prior_work,
        resolved_model=resolved_model,
        resolution_error=resolution_error,
    )


@dataclass
class _Workspace:
    """Salida de la provisión del workspace (P3, fase 2 — git fuera de txn)."""

    host_path: str | None = None
    read_only: bool = False
    error: str | None = None
    error_code: str = "workspace_unavailable"


async def _provision_workspace(
    settings: Settings, prepared: _PreparedRun, *, task_id: UUID
) -> _Workspace:
    """Fase 2 (P3): materialise the task's git worktree OUTSIDE the DB transaction
    (git subprocess I/O) so it can be bind-mounted RW as /workspace (prod-18
    task_prod18_provision_01). Auditoría 2026-07-02 (F0.2): para un run
    IMPLEMENTADOR que esperaba worktree, un fallo de provisión ya NO degrada a
    tmpfs "a ciegas" (el agente quemaba 50 iteraciones alucinando entregables
    sobre un workspace vacío) — se aborta ANTES de lanzar el contenedor. El
    fallback a tmpfs se conserva para reviews (ADR 0095) y tasks sin plan/slugs."""
    ws = _Workspace()
    if prepared.resolution_error is not None:
        return ws
    if prepared.worktree_inputs is not None:
        tenant_slug, project_slug, _wt_project_id, plan_id_str, plan_slug = prepared.worktree_inputs
        try:
            ws.host_path = await _provision_worktree(
                settings,
                tenant_slug=tenant_slug,
                project_slug=project_slug,
                plan_id=plan_id_str,
                plan_slug=plan_slug,
                task_id=str(task_id),
                expect_plan_history=prepared.plan_has_prior_work,
            )
        except RepoHistoryLostError as exc:
            # Guarda 2026-07-03: NO fabricar un workspace vacío para un plan
            # con trabajo previo — abortar con motivo accionable.
            ws.host_path = None
            ws.error_code = "repo_history_lost"
            ws.error = (
                f"{exc} Restaura el backup de data_root (o re-ejecuta el plan "
                "desde cero) y relanza la tarea."
            )
        if ws.host_path is None and ws.error is None:
            ws.error = (
                "No se pudo provisionar el worktree git de la tarea (data_root "
                f"'{settings.data_root}' inaccesible o fallo git). Ejecución abortada "
                "antes de lanzar el contenedor para no correr sin workspace. Revisa "
                f"la propiedad de {settings.data_root} (uid 1000) y relanza la tarea."
            )
    elif prepared.review_worktree is not None:
        # ADR 0095: mount the implementer's existing worktree READ-ONLY for the
        # reviewer. No git ops, no commit (worktree_inputs stays None). Missing
        # worktree → empty /workspace (the reviewer still has review_context).
        r_tenant_slug, r_project_slug = prepared.review_worktree
        ws.host_path = _resolve_review_worktree(
            settings, r_tenant_slug, r_project_slug, str(task_id)
        )
        ws.read_only = ws.host_path is not None
    return ws


async def _launch_and_stream(  # noqa: PLR0915 - lanzamiento + streaming + poll de cancelación
    request: ExecutionRequest,
    *,
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    redis: Redis,
    prepared: _PreparedRun,
    workspace: _Workspace,
    exec_id: str,
    runner: AgentContainerRunner | None,
    cancel_poll_interval_s: float,
) -> tuple[_RuntimeResult, dict[str, Any] | None]:
    """Fase 3 (P3): lanza el contenedor agent-runtime, streamea su stdout al
    stream Redis `exec:{id}` y pliega los eventos en el resultado del run.

    Devuelve ``(resultado, approval)`` — ``approval`` es el payload que emite un
    run aparcado en una acción sensible (task_02_33), ``None`` en el resto."""
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
    resolved_kind = (prepared.resolved_model or {}).get("kind")
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
            prepared.approval_policy,
            agent_internal_api_url=settings.agent_internal_api_url,
            # El spec RESUELTO (kind + endpoint + credencial) — ADR 0057 F1.
            model_spec=prepared.resolved_model,
            # La definición de "hecho" de la tarea → al prompt de decisión,
            # para que el comportamiento (leer/escribir/test) lo dicte la tarea.
            acceptance_criteria=prepared.task_acceptance_criteria,
            # ADR 0102 D3: config de guardrails resuelta (o None → baseline).
            guardrails=prepared.guardrails,
            # ADR 0110 (mitad HTTP, EXPERIMENTAL, default OFF).
            conversation_thread=settings.runtime_conversation_thread,
            # ADR 0112 fase 2 (EXPERIMENTAL, default OFF).
            reflection_assess=settings.runtime_reflection_assess,
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
        workspace_host_path=workspace.host_path,
        workspace_read_only=workspace.read_only,
    )
    active_runner = runner or AgentContainerRunner(settings)
    cancel_seen = False

    # M1: provisioning succeeded and the container is about to be created — stamp
    # it so the orphan sweeper can tell a lost container (reap after grace) from a
    # run still provisioning (protect). Short own txn, both implementer & review.
    async with sessionmaker() as launch_session, launch_session.begin():
        ex = await get_execution(launch_session, prepared.execution_id)
        if ex is not None and ex.container_launched_at is None:
            ex.container_launched_at = datetime.now(UTC)

    async def _watch_for_cancel() -> None:
        """Poll ``cancel_requested_at`` while the container runs; on an operator
        cancel, kill the container (the LLM-cost source) so ``run_streamed`` exits
        and the run finalises as ``cancelled``."""
        nonlocal cancel_seen
        while True:
            await asyncio.sleep(cancel_poll_interval_s)
            async with sessionmaker() as cancel_session:
                ex = await get_execution(cancel_session, prepared.execution_id)
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
            ex = await get_execution(cancel_session, prepared.execution_id)
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
            guardrail_events=result.guardrail_events,
        )
    approval = final_result.get("approval") if final_result else None
    return result, approval


async def _finalize_and_transition(
    sessionmaker: async_sessionmaker[AsyncSession],
    request: ExecutionRequest,
    *,
    execution_id: UUID,
    task_id: UUID,
    tenant_id: UUID,
    result: _RuntimeResult,
    approval: dict[str, Any] | None,
) -> tuple[tuple[Any, str, str] | None, bool]:
    """Fase 4 (P3): finaliza la fila + persiste guardrails + transiciona la task,
    TODO en una txn (P0.5 — un crash aquí no puede dejar la execution terminal
    con la task `in_progress` para siempre).

    Devuelve ``(task_event, implementer_path)``: el evento de cambio de estado a
    publicar (el caller decide CUÁNDO — el camino implementador lo difiere hasta
    después del commit del worktree, orden prod-18) y si el run era el camino
    implementador normal."""
    task_event: tuple[Any, str, str] | None = None
    implementer_path = False
    async with sessionmaker() as session, session.begin():
        await finalize_execution(session, execution_id, result=result)
        # g1 (ADR 0102 D4): persist the runtime's post_tool guardrail events under
        # the same tenant-scoped RLS txn (SAVEPOINT-isolated, best-effort).
        await _persist_guardrail_events(
            session,
            result,
            tenant_id=tenant_id,
            task_id=task_id,
            execution_id=execution_id,
            agent_id=UUID(request.agent_id) if request.agent_id else None,
        )
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
            # in_review/blocked/cancelled/done event is published by the CALLER
            # after the worktree commit exists (prod-18 ordering).
            implementer_path = True
            task_event = await transition_task_after_run(session, task_id, result.status)
    return task_event, implementer_path


async def _implementer_post_process(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    prepared: _PreparedRun,
    workspace: _Workspace,
    result: _RuntimeResult,
    task_id: UUID,
    tenant_id: UUID,
    exec_id: str,
) -> None:
    """Fase 5 (P3): post-proceso del camino implementador (prod-18) — commit +
    tests ANTES de que el evento de estado sea visible (ya persistido en fase 4;
    lo publica el caller de conduct_execution tras soltar el run-lock, H1).

    task_prod18_commit_01: a run that wrote into a worktree gets committed (with
    trailers) + pushed to the plan branch by the WORKER (the sandbox has no git
    credentials). P2.3/F26: commit for a clean `done` AND for an escalation
    (`needs_human_review`) so the human validator gets the diff. task_prod18_
    test_01: tests run over the worktree only for a `done` run. All best-effort."""
    if (
        result.status in ("done", "needs_human_review")
        and workspace.host_path is not None
        and prepared.worktree_inputs is not None
    ):
        c_tenant_slug, c_project_slug, c_project_id, c_plan_id, c_plan_slug = (
            prepared.worktree_inputs
        )
        commit_abort_code = await _commit_and_push_worktree(
            settings,
            host_path=workspace.host_path,
            tenant_slug=c_tenant_slug,
            project_slug=c_project_slug,
            project_id=c_project_id,
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
                worktree_host_path=workspace.host_path,
                acceptance_criteria=prepared.task_acceptance_criteria,
            )
        if commit_abort_code:
            # P2.3(b)/F13 + P7: a real git failure — surface it (with its
            # specific abort_code) on the execution row instead of reporting a
            # deliverable with an empty diff. Anticipo ADR 0099: el contexto
            # estructurado del conflicto viaja junto al código.
            code, conflict_context = commit_abort_code
            await _mark_commit_failed(
                sessionmaker,
                prepared.execution_id,
                code,
                conflict_context=conflict_context,
            )
    # The deferred state-change event is NOT published here (H1): it travels on
    # the ExecutionOutcome so the caller publishes it after releasing the
    # run-lock. The prod-18 ordering still holds — the commit above exists
    # before any consumer can see the event.


# NOTIF-3: estados de run que notifican execution_failed (prioridad). `done`
# emite execution_finished (opt-in: sin default de canal para no inundar).
_EXECUTION_FAILED_STATUSES = frozenset({"failed", "aborted"})


async def _notify_execution_outcome(
    *, tenant_id: str, task_id: str, task_title: str, status: str, abort_code: str | None
) -> None:
    """Encola execution_failed/execution_finished al dispatcher (NOTIF-3).

    Best-effort (mismo contrato que el _notify_plan_unblocked del reconciler):
    un fallo de broker se loguea y JAMÁS rompe el run ya terminado."""
    if status in _EXECUTION_FAILED_STATUSES:
        event_type = "execution_failed"
    elif status == "done":
        event_type = "execution_finished"
    else:
        return  # needs_human_review/cancelled tienen sus propios raíles
    try:
        from api_server.celery_client import enqueue_event_dispatch

        await enqueue_event_dispatch(
            {
                "event_type": event_type,
                "tenant_id": tenant_id,
                "context": {
                    "task_title": task_title,
                    "task_id": task_id,
                    "status": status,
                    "abort_code": abort_code or "",
                },
            }
        )
    except Exception as exc:  # la notificación nunca rompe el run
        _log.warning("workers.execution_outcome_notify_failed", task_id=task_id, error=str(exc))


async def conduct_execution(
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
    """Run one task end to end: container → Redis stream → `executions` row.

    P3 (refactor 2026-07-08): orquestador fino de cinco fases nombradas, con el
    MISMO comportamiento y orden (prod-18) de siempre:

      1. :func:`_prepare_run` — txn inicial (frontera, elegibilidad, fila
         `running`, insumos, modelo resuelto);
      2. :func:`_provision_workspace` — git I/O fuera de txn;
      3. fail-fast (modelo/workspace) o :func:`_launch_and_stream` — docker +
         streaming a Redis + poll de cancelación;
      4. :func:`_finalize_and_transition` — finalize + transición ATÓMICAS (P0.5);
      5. :func:`_implementer_post_process` (commit/tests, prod-18) + budgets +
         memorize. El evento de estado NO se publica aquí: viaja en
         ``ExecutionOutcome.pending_task_event`` y lo publica el caller tras
         soltar el run-lock (H1 — evita `concurrent_run_locked` en el despacho
         inmediato del review / re-dispatch).
    """
    task_id = UUID(request.task_id)
    tenant_id = UUID(request.tenant_id)
    async with sessionmaker() as session, session.begin():
        prepared = await _prepare_run(
            session,
            request,
            task_id=task_id,
            tenant_id=tenant_id,
            vault_store=vault_store,
            celery_task_id=celery_task_id,
        )
    if prepared is None:
        return ExecutionOutcome(
            execution_id="", status="skipped", abort_code="ineligible_task_status"
        )
    exec_id = str(prepared.execution_id)
    _log.info("workers.execution_started", execution_id=exec_id, task_id=request.task_id)
    if prepared.resolved_model is not None:
        # Solo claves no sensibles (safe_spec_summary) — la credencial vive en
        # el env del contenedor efímero y nunca se loguea.
        _log.info(
            "workers.model_resolved",
            execution_id=exec_id,
            **safe_spec_summary(prepared.resolved_model),
        )

    workspace = await _provision_workspace(settings, prepared, task_id=task_id)

    approval: dict[str, Any] | None = None
    failfast: tuple[str, str] | None = None
    if prepared.resolution_error is not None:
        # Fail-fast (ADR 0057 F1): sin proveedor resoluble NO se lanza el
        # contenedor — la ejecución termina `failed` con motivo explícito en
        # vez de correr en silencio con el cliente scripted.
        _log.error(
            "workers.model_resolution_failed",
            execution_id=exec_id,
            error=prepared.resolution_error,
        )
        failfast = ("model_unresolved", prepared.resolution_error)
    elif workspace.error is not None:
        # Fail-fast (F0.2): sin workspace NO se lanza el contenedor. El código
        # distingue el data_root inaccesible (`workspace_unavailable`) del
        # historial perdido (`repo_history_lost`, guarda 2026-07-03).
        _log.error(
            "workers.workspace_unavailable",
            execution_id=exec_id,
            task_id=request.task_id,
            data_root=settings.data_root,
            abort_code=workspace.error_code,
        )
        failfast = (workspace.error_code, workspace.error)
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
        result, approval = await _launch_and_stream(
            request,
            settings=settings,
            sessionmaker=sessionmaker,
            redis=redis,
            prepared=prepared,
            workspace=workspace,
            exec_id=exec_id,
            runner=runner,
            cancel_poll_interval_s=cancel_poll_interval_s,
        )

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
            guardrail_events=result.guardrail_events,
        )

    # P0.5: for the implementer path the task transition is persisted ATOMICALLY with
    # finalize (same txn) so a crash here can never leave the execution terminal but
    # the task `in_progress` forever. The EVENT publication is deferred until after
    # the worktree commit exists (prod-18 ordering) AND until the caller has released
    # the run-lock (H1) — it travels on the returned outcome for ALL paths.
    task_event, implementer_path = await _finalize_and_transition(
        sessionmaker,
        request,
        execution_id=prepared.execution_id,
        task_id=task_id,
        tenant_id=tenant_id,
        result=result,
        approval=approval,
    )

    # H1: NO event is published here (neither path). The finish event travels on
    # the ExecutionOutcome (`pending_task_event`) and the caller publishes it
    # AFTER releasing the per-task run-lock — the orchestrator's immediate
    # dispatch (reviewer on in_review, re-dispatch on reject→backlog) otherwise
    # lands while the lock is still held and is dropped (`concurrent_run_locked`).
    if implementer_path:
        await _implementer_post_process(
            settings,
            sessionmaker,
            prepared=prepared,
            workspace=workspace,
            result=result,
            task_id=task_id,
            tenant_id=tenant_id,
            exec_id=exec_id,
        )

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
    trigger_memorize(prepared.execution_id, result.status)

    # NOTIF-3 (auditoría 2026-07-12): los eventos execution_failed /
    # execution_finished estaban registrados (+plantillas ES/EN) pero NADIE los
    # emitía — un run que moría solo dejaba log. Best-effort, nunca rompe el run.
    await _notify_execution_outcome(
        tenant_id=str(tenant_id),
        task_id=str(task_id),
        task_title=str((request.task or {}).get("title") or ""),
        status=result.status,
        abort_code=result.abort_code,
    )

    _log.info("workers.execution_finished", execution_id=exec_id, status=result.status)
    return ExecutionOutcome(
        execution_id=exec_id,
        status=result.status,
        abort_code=result.abort_code,
        pending_task_event=task_event,
    )
