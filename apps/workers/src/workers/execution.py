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
import dataclasses
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import structlog
from api_server.auth.internal_agent import mint_agent_token
from api_server.db.approval_repo import read_approved_actions, request_approval_if_needed
from api_server.db.domain import Plan, Project, Task, TaskStatus
from api_server.db.execution_repo import (
    apply_steps_rollup,
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
from workers.container import AgentContainerRunner, ContainerResult, ContainerSpec
from workers.memorizer import trigger_memorize
from workers.model_resolver import (
    ModelResolutionError,
    resolve_model_spec,
    safe_spec_summary,
)
from workers.model_secret import (
    STAGING_SUBDIR,
    split_model_credentials,
    stage_model_credentials,
)
from workers.review_diff import compute_task_review_diff
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
from workers.secrets import SECRETS_DIR, StagedSecrets, stage_secrets
from workers.tracked_paths import TRACKED_PATHS_ENV, compute_tracked_paths

# Re-exports EXPLÍCITOS: la casa histórica de estos símbolos es este módulo —
# tasks/maintenance/tests siguen importando de workers.execution. `__all__`
# marca el re-export para mypy (no_implicit_reexport) y para ruff F401.
__all__ = [
    "_EMPTY_USAGE",
    "_SDK_BASE_SHELL_COMMANDS",
    "CrossTenantExecutionError",
    "ExecutionOutcome",
    "ExecutionRequest",
    "_RuntimeResult",
    "_agent_spec",
    "_assemble_result",
    "_parse_line",
    "_resolve_tool_spec_images",
    "_scan_logs_for_terminal",
    "conduct_execution",
    "transition_task_after_run",
]

_log = structlog.get_logger("workers.execution")

# Status the agent loop reports when it parks on a sensitive action —
# mirrors agent_runtime.state.STATUS_AWAITING_APPROVAL and
# ExecutionStatus.AWAITING_HUMAN_APPROVAL.
_AWAITING_APPROVAL = "awaiting_human_approval"

# How often the run polls `cancel_requested_at` while the container runs, to
# kill it cooperatively on an operator cancel (POST /executions/{id}/cancel).
_CANCEL_POLL_INTERVAL_S = 3.0

#: `kind` del evento de auditoría que lleva la métrica de contaminación del
#: revisor (`task_gov_06`). Kind propio, y no un campo dentro de
#: `review_comment`, porque un APPROVE sin desglose de criterios no emite
#: `review_comment`: colgar la métrica de ahí la perdería justo en la mitad de
#: los casos que interesa medir. El front filtra por `kind`
#: (`components/tasks/task-review-criteria.tsx`), así que uno nuevo es inerte
#: para la UI.
REVIEW_CONTAMINATION_EVENT_KIND = "review_contamination"


# Eligibility (R5): the task status the orchestrator sets right before enqueueing
# a run of each kind — the ONLY status a run of that kind may launch from.
_LAUNCHABLE_STATUS_BY_KIND: dict[bool, str] = {
    False: TaskStatus.IN_PROGRESS.value,  # implementer run
    True: TaskStatus.IN_REVIEW.value,  # reviewer run
}


@dataclass(frozen=True)
class _SkippedRun:
    """`_prepare_run` decidió NO correr y quiere decir por qué (`task_cv_13`)."""

    abort_code: str


def _claim_is_current(*, task_claim_id: str | None, request_claim_id: str | None) -> bool:
    """¿Es este mensaje el de la reclamación VIGENTE de la tarea? (`task_cv_13`, A-05)

    Un mensaje sin `claim_id` (orquestador anterior al campo, invocación manual)
    se acepta: no hay con qué comparar y se conserva el comportamiento previo,
    lo que hace seguro desplegar el worker antes que el orquestador. Un mensaje
    CON `claim_id` sólo corre si coincide con el de la tarea; si la tarea no
    tiene ninguno (la reclamación se revirtió y se limpió) tampoco es vigente."""
    if request_claim_id is None:
        return True
    return task_claim_id == request_claim_id


def _task_is_launchable(status: str, *, is_review: bool) -> bool:
    """Whether a task in ``status`` may start a run of this kind.

    A re-delivered Celery message (``acks_late``) can re-fire ``run_execution``
    for a task the operator moved to ``blocked``/``cancelled`` in the meantime
    (e.g. after the worker restart that recovers an R1 hang). Only the in-flight
    status the orchestrator set right before enqueueing is launchable; anything
    else means the task moved on and the run must be a no-op (R5).
    """
    return status == _LAUNCHABLE_STATUS_BY_KIND[is_review]


def _internal_mcp_hosts(request: ExecutionRequest) -> list[str]:
    """Hosts sin punto de los servidores MCP del proyecto: servicios internos del
    compose. Van a NO_PROXY y (`task_cv_25`) al bridge de la ejecución."""
    return sorted(
        {
            host
            for server in (request.mcp_servers or [])
            if (url := str(server.get("url") or ""))
            and (host := urlparse(url).hostname)
            and "." not in host
        }
    )


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
    code_diff: str | None = None,
    approved_actions: list[dict[str, Any]] | None = None,
    tracked_paths: list[str] | None = None,
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

    ``tracked_paths`` llega YA CALCULADO (:mod:`workers.tracked_paths`, desde la
    provisión del workspace): esta función es PURA y no habla con git ni con
    docker, que es lo que permite testear el spec en aislamiento.
    """
    spec = _agent_spec(
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
        code_diff=code_diff,
    )
    # ADR 0135: las acciones que un humano YA aprobó en esta task. Sin esto,
    # aprobar no autorizaba nada — el gate del sandbox, que no tiene BD ni
    # memoria entre runs, volvía a aparcar la MISMA acción y el bucle no tenía
    # techo. Solo se emite la clave cuando hay algo autorizado: «sin clave» es
    # el comportamiento de siempre para un primer despacho.
    if approved_actions:
        spec["approved_actions"] = approved_actions
    env: dict[str, str] = {"AGENT_TASK_SPEC": json.dumps(spec)}
    # Sin agente asignado no hay sujeto para el token: lo dejamos fuera y el
    # runtime mantiene su comportamiento sin API interna (backward-compat).
    if request.agent_id:
        env["AGENTIC_INTERNAL_TOKEN"] = mint_agent_token(
            agent_id=UUID(request.agent_id),
            tenant_id=UUID(request.tenant_id),
            task_id=UUID(request.task_id),
        )
        env["AGENTIC_API_URL"] = agent_internal_api_url
    # MCP servers INTERNOS del compose (hostname sin punto = nombre de servicio
    # Docker, solo resoluble en la red de agentes): exentos del egress-proxy via
    # NO_PROXY, o el transporte httpx del cliente MCP muere con `403 Filtered`
    # (tinyproxy FilterDefaultDeny; cazado en vivo en la prueba Atlassian
    # 2026-07-18). La declaracion del server en el proyecto ES la autorizacion
    # (RBAC tenant_admin). Un MCP EXTERNO (FQDN con punto) sigue saliendo por el
    # proxy y exige su host en la allowlist: deny-by-default intacto.
    internal_mcp_hosts = _internal_mcp_hosts(request)
    if internal_mcp_hosts:
        joined = ",".join(internal_mcp_hosts)
        env["NO_PROXY"] = joined
        env["no_proxy"] = joined
    # Los DIRECTORIOS versionados en la rama del plan, a cualquier profundidad,
    # para que el runtime no borre en bloque un árbol que es el entregable ya
    # commiteado de otra tarea. El 2026-08-31 un `delete_file` recursivo sobre
    # `app/` se llevó 85 ficheros del entregable anterior en «Hello World CI4 v3»
    # (mediapro): el sandbox no puede distinguirlo de `vendor/` porque el ADR
    # 0163 le esconde el `.git`. Separados por `\n` — el nombre y el formato son
    # el contrato.
    #
    # Lista vacía => NO se emite la clave. Un proyecto vacío (primera tarea del
    # plan, sin commit todavía) y un worker anterior a esto tienen que verse
    # IGUAL desde el runtime, que sin la variable no aplica la protección nueva.
    if tracked_paths:
        env[TRACKED_PATHS_ENV] = "\n".join(tracked_paths)
    return env


async def _resolve_effective_guardrails(
    session: AsyncSession, project: Project | None
) -> dict[str, Any] | None:
    """La config de guardrails EFECTIVA del run (ADR 0102 D3 + prod-03 task_prod03_11).

    Delega en ``api_server.db.guardrail_config.get_effective_guardrail_config``,
    que fusiona las TRES capas —plataforma → tenant → proyecto— con
    ``resolve_config``: los checks ``locked`` de plataforma no pueden relajarse
    ni eliminarse abajo. Antes esta función fusionaba solo dos, porque la capa
    TENANT no existía en ninguna parte hasta la migración 0132.

    ``None`` cuando no hay capas (el runtime cae a su baseline LOG). El
    resultado lleva una clave ``version`` hermana de ``guardrails`` para
    invalidación/trazabilidad; el runtime la ignora (``parse_config`` solo mira
    ``guardrails``).

    Best-effort: un error aquí degrada a ``None`` (baseline), jamás rompe el
    dispatch. El cap de tamaño y la degradación a plataforma-sola viven en el
    servicio, que es donde vive la resolución."""
    try:
        from api_server.db.guardrail_config import get_effective_guardrail_config

        if project is None:
            return None
        return await get_effective_guardrail_config(
            session, tenant_id=project.tenant_id, project_id=project.id
        )
    except Exception as exc:  # baseline del runtime como red de seguridad
        _log.warning("workers.guardrails_resolve_failed", error=str(exc))
        return None


async def _load_project(session: AsyncSession, task_id: UUID) -> Project | None:
    """The task's project — its `human_approval_policy` gates the run."""
    task = await session.get(Task, task_id)
    if task is None:
        return None
    return await session.get(Project, task.project_id)


def _review_run_policy() -> dict[str, Any]:
    """La política de aprobación que recibe un run de REVIEW (`task_cv_15`, A-08).

    Ninguna categoría gatea y las no listadas tampoco (`unlisted_category:
    auto`, ADR 0153). No es una relajación de seguridad: el workspace del
    review se monta de sólo lectura (ADR 0095) y su único producto es el
    veredicto, así que no hay acción sensible que proteger — y un review que
    aparca en `awaiting_human_approval` no lo reanuda nadie: quedaba no
    terminal para siempre consumiendo `retry_count`. `human_question` sigue
    siendo siempre humana por diseño (ADR 0114); si un reviewer pregunta, el
    run se sella (`_seal_invalid_park`) y el veredicto cuenta como
    inconcluyente."""
    from api_server.db.approval_repo import UNLISTED_CATEGORY_KEY
    from shared_domain.approval_categories import APPROVAL_CATEGORIES

    return {
        "categories": dict.fromkeys(APPROVAL_CATEGORIES, "auto"),
        UNLISTED_CATEGORY_KEY: "auto",
    }


def _seal_invalid_park(
    *,
    is_review: bool,
    result: _RuntimeResult,
    approval: dict[str, Any] | None,
    exec_id: str | None = None,
) -> _RuntimeResult:
    """Sella como `failed` con nombre un `awaiting_human_approval` que nadie puede
    reanudar; devuelve el resultado intacto en cualquier otro caso.

    * **F12** (implementador sin payload): el runtime aparcó pero no emitió la
      acción, así que no hay `ApprovalRequest` que crear; caer al camino del
      implementador dejaría la tarea `in_progress` sin nada en la bandeja.
    * **A-08** (`task_cv_15`, review): un run de review no se reanuda —su
      workspace es de sólo lectura y su producto es el veredicto—; aparcado
      quedaba no terminal para siempre. Sellado, `_apply_review_verdict` lo
      cuenta como inconcluyente (acotado por `retry_count`)."""
    if result.status != _AWAITING_APPROVAL:
        return result
    if is_review:
        abort_code = "review_parked"
        output = (
            (result.output or "")
            + "\n\n[review run parked on a sensitive action: a review cannot wait for"
            " approval — its workspace is read-only and its only product is the verdict]"
        ).strip()
    elif not approval:
        abort_code = "approval_payload_missing"
        output = "runtime reported awaiting_human_approval but emitted no approval payload"
    else:
        return result
    _log.error(f"workers.{abort_code}", execution_id=exec_id)
    return _RuntimeResult(
        status="failed",
        abort_code=abort_code,
        output=output,
        iterations=result.iterations,
        steps=result.steps,
        usage=result.usage,
        finish_status=result.finish_status,
        guardrail_events=result.guardrail_events,
        prompt_version=result.prompt_version,
        runtime_image_digest=result.runtime_image_digest,
    )


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
    from api_server.llm_providers.vault import HvacLLMProviderVaultStore

    # prod-10 task_prod10_07: por la fábrica compartida, que mantiene vivo el
    # token del worker. Con el `hvac.Client` construido aquí a mano, el día que
    # el token caducase TODA ejecución volvería a correr con
    # `has_credential=False` — sin un cambio de configuración que lo explicase.
    from workers.vault_client import build_worker_vault_client

    client = build_worker_vault_client(settings)
    if client is None:
        return None
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


async def _record_review_contamination(
    session: AsyncSession,
    *,
    task_id: UUID,
    tenant_id: UUID,
    review_execution_id: UUID,
    reviewer_agent_id: UUID | None,
    reviewer_output: str,
) -> None:
    """Mide cuánto del veredicto venía ya en el relato del autor (`task_gov_06`).

    El detector de Goodhart del plan `gov-01`: el revisor ve los tres últimos
    intentos del implementador —el último verbatim— y resuelve el mismo modelo,
    así que hereda su encuadre antes de opinar. En vez de pagar de entrada la
    pasada de review ciega (4-6 días y un ADR), se **mide** cada review y la
    decisión se toma con el número delante. El resultado es un dato: no bloquea,
    no avisa y no cambia el veredicto, que ya está aplicado cuando esto corre.

    Cuál es «el relato del autor»: la ejecución más reciente de la misma task que
    NO sea ésta ni de este mismo agente revisor. Excluir sólo `review_execution_id`
    no basta — un review no concluyente se re-despacha (ADR 0095 D3), así que la
    ejecución anterior puede ser otra pasada del propio revisor, y compararlo
    consigo mismo daría contaminación altísima por construcción.

    Best-effort dentro de un SAVEPOINT, igual que `_persist_guardrail_events`:
    esto es instrumentación, y romper aquí anularía un veredicto correcto por un
    fallo de medición.
    """
    try:
        from api_server.db.domain import Execution
        from api_server.db.task_audit_repo import append_audit_event
        from api_server.review_contamination import measure_review_contamination
        from api_server.reviewer_bridge import parse_reviewer_output

        async with session.begin_nested():
            author_filter = [
                Execution.task_id == task_id,
                Execution.tenant_id == tenant_id,
                Execution.id != review_execution_id,
            ]
            if reviewer_agent_id is not None:
                author_filter.append(Execution.agent_id.is_distinct_from(reviewer_agent_id))
            author = (
                await session.execute(
                    select(Execution.id, Execution.output, Execution.finish_status)
                    .where(*author_filter)
                    .order_by(Execution.created_at.desc())
                    .limit(1)
                )
            ).first()
            if author is None:
                # Sin run del implementador no hay nada con lo que comparar (una
                # task cuyo único run es el review). No se emite fila: un cero
                # aquí contaría como «revisor limpio» en el agregado.
                return
            author_id, author_output, author_finish_status = author
            metric = measure_review_contamination(
                reviewer_text=reviewer_output or "",
                author_text=str(author_output or ""),
                verdict=parse_reviewer_output(reviewer_output or "").label,
                author_finish_status=author_finish_status,
            )
            payload = {
                **metric.as_payload(),
                "review_execution_id": str(review_execution_id),
                "author_execution_id": str(author_id),
            }
            await append_audit_event(
                session,
                tenant_id=tenant_id,
                task_id=task_id,
                kind=REVIEW_CONTAMINATION_EVENT_KIND,
                actor="platform:goodhart-detector",
                payload=payload,
            )
        # El log estructurado va a Loki (ADR 0139), que es donde se lee la
        # ventana de «una semana de runs» del test humano `human_gov_03` sin
        # tener que escribir SQL contra `task_audit_events`.
        _log.info(
            "workers.review_contamination",
            task_id=str(task_id),
            review_execution_id=str(review_execution_id),
            **{k: v for k, v in payload.items() if k != "review_execution_id"},
        )
    except Exception:
        _log.warning(
            "workers.review_contamination_failed",
            task_id=str(task_id),
            review_execution_id=str(review_execution_id),
        )


class RepoHistoryLostError(RuntimeError):
    """El bare repo del proyecto ya no contiene el historial del plan aunque el
    plan tiene tareas completadas — el data_root fue arrasado/sustituido (p. ej.
    el engine-restart de Docker Desktop del 2026-07-02 recreó el bind vacío).
    Re-seedear un repo VACÍO y dejar correr al agente fabricaría un estado roto
    en silencio (churn estéril + escalada confusa); el run debe abortar en
    segundos con ``abort_code=repo_history_lost`` y un motivo accionable."""


async def _align_base_from_remote_if_empty(
    settings: Settings,
    *,
    tenant_slug: str,
    project_slug: str,
    plan_id: str,
    plan_slug: str,
    project_id: str,
) -> None:
    """Root the project's bare from the REMOTE before any synthetic seed.

    The «no history in common» fix (workflow diagnosis 2026-07-23): the bare is
    created empty by ``git init --bare`` and, historically, this execution path
    seeded a SYNTHETIC root commit if the clone/sync task had not run yet — so
    the plan branch rooted on an orphan history that the final PR could never
    merge (GitHub 422). Here we run the SAME proven sequence ``repo_clone`` uses
    (``ensure_repo(remote) → fetch_remote → align_default_branch``) via
    :func:`_clone_project_repo_async`, so the local default branch descends from
    ``origin/<default>``. Gated on ``has_commits`` → only the FIRST provisioning
    pays the network cost; later worktrees see a born base and skip. Best-effort:
    a failure (offline/auth) leaves ``_git``'s seed as the fallback (i.e. the
    pre-fix behaviour, which the PR guard still catches) — it never fails the run.
    """
    from uuid import UUID

    from workers.git_repos import BareRepoManager
    from workers.plan_git import worktree_coordinates
    from workers.repo_clone import _clone_project_repo_async

    layout, _branch = worktree_coordinates(
        data_root=settings.data_root,
        tenant_slug=tenant_slug,
        project_slug=project_slug,
        plan_id=plan_id,
        plan_slug=plan_slug,
    )
    repo_name = project_slug
    if BareRepoManager(layout).has_commits(repo_name):
        return  # already provisioned — do NOT re-fetch on every worktree
    try:
        result = await _clone_project_repo_async(UUID(str(project_id)), settings=settings)
        _log.info(
            "workers.worktree_base_aligned_from_remote",
            project_id=str(project_id),
            status=result.get("status") if isinstance(result, dict) else None,
            alignment=result.get("alignment") if isinstance(result, dict) else None,
        )
    except Exception as exc:  # pragma: no cover - defensive: seed fallback still runs
        _log.warning(
            "workers.worktree_base_align_failed", project_id=str(project_id), error=str(exc)
        )


async def _provision_worktree(
    settings: Settings,
    *,
    tenant_slug: str,
    project_slug: str,
    plan_id: str,
    plan_slug: str,
    task_id: str,
    project_id: str | None = None,
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

    from shared_test_runtimes import catalog as runtime_catalog

    from workers.git_repos import BareRepoManager, WorktreeManager
    from workers.plan_git import repair_worktree_link, worktree_coordinates

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
        # ADR 0163: un worker que muere DE GOLPE —hard limit de Celery, OOM,
        # reinicio del contenedor— no ejecuta el `finally` de `git_link_hidden` y
        # deja el worktree sin su puntero. Sin esta línea el reintento moría aquí
        # mismo (`sync_to_head` sale 128) y la tarea quedaba en
        # `workspace_unavailable` en CADA relanzamiento: irrecuperable sin manos.
        #
        # La red que ya existe vive en `commit_task`, y a `commit_task` no se
        # llega si la provisión revienta antes. Repararlo aquí es lo que la
        # convierte en una red de verdad. Lo destapó la auditoría del 2026-08-31
        # midiendo la muerte dura, no razonando sobre ella.
        #
        # El lock del worktree sobrevive a esa muerte a propósito: es lo que
        # impide que un prune se lleve los metadatos antes de que reparemos.
        # `repair_worktree_link` lo suelta al terminar.
        repair_worktree_link(Path(path))
        # task_wf_24 (C-06): el `clean -fdx` del sync barría también `vendor/`,
        # `node_modules/`, `.venv/`… así que cada reintento reinstalaba en frío.
        # Los nombres los declara cada plantilla de runtime; se pasa la UNIÓN
        # porque un worktree puede tener varios stacks a la vez (monorepo con
        # backend PHP y frontend node) y limitarse al template por defecto del
        # proyecto seguiría arrasando los del otro.
        wt.sync_to_head(task_id, branch=branch, preserve=runtime_catalog.dependency_dirs())
        # AQUÍ NO SE ESCRIBE NADA EN EL WORKSPACE, y en particular NO el
        # `.gitignore` base (2026-09-01). Lo escribe `plan_git.commit_task`, al
        # CERRAR la tarea, y la razón es el ADR 0163: lo que la plataforma deja en
        # el workspace se lo encuentra el andamiador. Medido con la provisión real
        # y Composer 2.9.4 — `Filesystem::isDirEmpty()` usa
        # `ignoreDotFiles(false)`, o sea que un dotfile CUENTA:
        #
        #     workspace tras la provisión: ['.git', '.gitignore']
        #     composer create-project codeigniter4/framework .  ->  rc=1
        #         "Project directory is not empty."
        #     (control, workspace vacío: rc=0, instala v4.7.4)
        #
        # Dentro del sandbox el beneficio de ese fichero es CERO —la exclusión de
        # `commit_task` es la que protege a la plataforma— y el coste es que el
        # andamiador canónico del proyecto del incidente no arranca. Escrito al
        # commitear, llega igual al repositorio (que es para lo que se quería: que
        # quien clone y trabaje fuera de la plataforma no se coma el mismo
        # `git add -A`) sin existir nunca mientras corre el agente.
        if expect_plan_history and not any(entry.name != ".git" for entry in Path(path).iterdir()):
            raise RepoHistoryLostError(
                f"El checkout de la rama '{branch}' está VACÍO pese a que el plan tiene "
                "tareas completadas — los commits previos ya no están en el bare repo "
                "(historial perdido). No se lanza al agente sobre un workspace vacío."
            )
        return str(path)

    # «no history in common» fix: root the base from origin BEFORE _git's seed,
    # so the plan branch descends from the remote's history (not a synthetic root).
    if project_id:
        await _align_base_from_remote_if_empty(
            settings,
            tenant_slug=tenant_slug,
            project_slug=project_slug,
            plan_id=plan_id,
            plan_slug=plan_slug,
            project_id=project_id,
        )

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
    ``done`` (P2.3/F26). The bare→remote push follows immediately via
    ``push_plan_branch_to_remote`` when the project's ``branch_push_mode`` is
    ``incremental`` (the default, T3/P3); ``final_only`` defers it to plan close,
    where ``open_plan_pr`` pushes the tip regardless of mode.

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
    *,
    task_id: UUID | None = None,
    tenant_id: UUID | None = None,
) -> tuple[Any, str, str] | None:
    """Stamp a visible ``abort_code`` marker on a finalised execution whose
    worktree commit/push hit a real git error (P2.3(b)/F13, P7).

    The run already reported a deliverable, but it never reached the plan branch —
    surface that on the execution row (``abort_code`` + an appended ``output`` note)
    instead of silently reporting success with an empty diff. ``rebase_conflict``
    (P7) is escalatable — it lands on the escalation panel with a resolution note.
    **Y bloquea la tarea** (`task_cv_11`, auditoría 2026-09-01 A-06/C-04). La
    transición a `in_review` se persistió en la fase 4, ANTES del commit; con el
    marcador solo, el reviewer revisaba un worktree cuyo trabajo no está en la
    rama del plan, podía aprobar a `done` y el siguiente `sync_to_head` lo
    borraba. Con ``task_id``, en la MISMA transacción: si la tarea sigue
    `in_review`, pasa a `blocked` con un evento de auditoría escalado, y se
    devuelve ``(task, old, new)`` para que SUSTITUYA al evento `in_review` que el
    caller aún no ha publicado (H1). Si otro camino ya la movió, no se toca.

    Best-effort: opens its own short txn on the BYPASSRLS worker engine; a failure
    here never breaks the run.
    """
    try:
        async with sessionmaker() as session, session.begin():
            execution = await get_execution(session, execution_id)
            if execution is None:
                return None
            execution.abort_code = abort_code
            note, conflict_step = _conflict_note(
                abort_code, conflict_context, steps_len=len(execution.steps_log or [])
            )
            execution.output = f"{execution.output}\n{note}" if execution.output else note
            if conflict_step is not None:
                # Anticipo ADR 0099: el contexto estructurado viaja en steps_log
                # (JSONB ya renderizado por el visor y consultable por SQL).
                execution.steps_log = [*(execution.steps_log or []), conflict_step]
                # Este es el SEGUNDO escritor de `steps_log` (el otro es
                # `db/execution_repo.py`), y las columnas `last_model` /
                # `tokens_in` / `tokens_out` son una proyección suya: sin esto
                # describirían un log que ya no existe. Hoy el paso anexado es
                # `kind: node` y el rollup lo ignora, así que no cambia ningún
                # número — se llama igual porque la garantía que el diseño invoca
                # tiene que ser cierta por construcción, no por casualidad del
                # `kind` que hoy usa `_conflict_note`.
                apply_steps_rollup(execution, execution.steps_log)
            if task_id is None or tenant_id is None:
                return None
            return await _block_task_with_lost_work(
                session, task_id=task_id, tenant_id=tenant_id, abort_code=abort_code, note=note
            )
    except Exception as exc:  # pragma: no cover - defensive best-effort
        _log.warning(
            "workers.commit_failed_marker_error", execution_id=str(execution_id), error=str(exc)
        )
        return None


async def _block_task_with_lost_work(
    session: AsyncSession, *, task_id: UUID, tenant_id: UUID, abort_code: str, note: str
) -> tuple[Any, str, str] | None:
    """`in_review → blocked` para una tarea cuyo entregable no llegó a la rama del
    plan (`task_cv_11`). Sólo si sigue `in_review`: un humano o el reviewer
    pueden haberla movido antes, y entonces manda lo que hicieron."""
    from api_server.db.task_audit_repo import append_audit_event
    from api_server.task_state_machine import transition_task_status

    task = await session.get(Task, task_id, with_for_update=True)
    if task is None or task.status != TaskStatus.IN_REVIEW.value:
        return None
    old_status = task.status
    transition_task_status(task, TaskStatus.BLOCKED.value)
    await append_audit_event(
        session,
        tenant_id=tenant_id,
        task_id=task_id,
        kind="review_comment",
        actor="worker",
        payload={
            "escalated": True,
            "reason": "deliverable_not_on_plan_branch",
            "abort_code": abort_code,
            "note": note,
        },
    )
    await session.flush()
    _log.warning("workers.task_blocked_lost_work", task_id=str(task_id), abort_code=abort_code)
    return (task, old_status, task.status)


# ---------------------------------------------------------------------------
# ADR 0162 (opción A, ola 2) — la declaración del implementador se vuelve criterio
# ---------------------------------------------------------------------------
#
# **El tramo que faltaba.** La ola 1 dejó al agente declarando con qué se
# verifica cada criterio, y la declaración se quedaba en `executions.steps_log`:
# se contaba y no se usaba. Con eso, el run SIGUIENTE de la misma tarea tampoco
# disparaba el test-runtime — que es exactamente lo que la opción A venía a
# arreglar.
#
# Aquí la declaración pasa a `tasks.acceptance_criteria`. Y la disciplina de esa
# escritura no es opcional, porque tiene precedente escrito y caro:
# `api_server.chat.sync_to_kanban._merge_acceptance` existe porque un replan
# convertía en prosa el único dato que hacía verificable a una tarea. La regla
# que enunció —*una escritura no puede destruir información que la otra mitad no
# sabe expresar*— vale aquí en su forma más estricta, porque **nada distingue en
# la columna lo que escribió el operador a mano de lo que dejó un run anterior**.
# Así que se trata todo lo ya escrito como del operador: la declaración RELLENA
# huecos y no pisa ninguno.
#
# Y persistir es INFORMACIÓN, no gate: el número de criterios no cambia, ninguno
# desaparece y `all_passed()` sigue saliendo sólo del código de salida. La opción
# C no está firmada.

# El conjunto CERRADO de campos que una declaración puede aportar a un criterio.
# Mismo vocabulario que `agent_runtime.check_declarations` un contenedor más
# allá, y que `test_runtime._coerce_check` un piso más abajo: inventar aquí un
# tercer nombre para lo mismo es cómo se acaba con una clave que nadie lee.
_DECLARATION_FIELDS = ("check_type", "runtime", "command", "expected_signal", "reason")
# Techo por campo y por lista. Lo que llega aquí lo escribió un LLM dentro de un
# sandbox y acaba en una columna JSONB: sin tope, una respuesta degenerada engorda
# la fila de la tarea para siempre.
_MAX_DECLARED_FIELD_LEN = 500
_MAX_DECLARATIONS = 32


def _criterion_key(text: Any) -> str:
    """La forma con la que se casa un criterio con su declaración.

    Colapsa espacios y mayúsculas porque quien reescribe el criterio en la
    declaración es un modelo copiando de su propio prompt: exigir igualdad byte a
    byte convertiría cada espacio de más en un «no declarado» falso.

    Está duplicada respecto a ``agent_runtime.check_declarations._criterion_key``
    porque el worker NO importa el paquete del contenedor (ni puede: corre en otra
    imagen). La paridad la fija un test, no la buena voluntad — igual que la del
    predicado de criterio ejecutable con ``orchestrator.dispatch``.
    """
    return " ".join(str(text or "").split()).casefold()


def _criterion_names(criterion: Any) -> set[str]:
    """Con qué nombres puede referirse una declaración a ESTE criterio."""
    names: set[str] = set()
    if isinstance(criterion, dict):
        for key in ("description", "text", "criterion", "name", "id"):
            value = criterion.get(key)
            if isinstance(value, str) and value.strip():
                names.add(_criterion_key(value))
    else:
        names.add(_criterion_key(criterion))
    names.discard("")
    return names


def _declared_checks_from_result(final_result: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Las declaraciones que trae la línea ``execution.finished``, revalidadas.

    Viaja por la MISMA vía que ``approval`` y ``finish_status`` — el sobre del
    resultado del run— y no por un canal propio: un segundo camino para sacar
    cosas del contenedor es un segundo camino que mantener y desincronizar.

    Se revalida en vez de fiarse porque al otro lado hay JSON escrito por un
    modelo dentro de un sandbox. Una entrada sin ``criterion`` no dice de qué
    criterio habla y una sin ``check_type`` no declara nada: ninguna de las dos es
    una declaración, y contarlas como tales le devolvería al silencio la categoría
    de respuesta válida que la opción A retira. Nunca lanza: el entregable ya está
    escrito y la execution ya está finalizada.
    """
    raw = (final_result or {}).get("check_declarations")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw[:_MAX_DECLARATIONS]:
        if not isinstance(item, dict):
            continue
        clean = {
            key: value.strip()[:_MAX_DECLARED_FIELD_LEN]
            for key in ("criterion", *_DECLARATION_FIELDS)
            if isinstance(value := item.get(key), str) and value.strip()
        }
        if "criterion" in clean and "check_type" in clean:
            out.append(clean)
    return out


def _accepted_fields(base: dict[str, Any], declaration: dict[str, Any]) -> dict[str, Any]:
    """Qué de una declaración se puede escribir sobre ``base`` sin destruir nada."""
    fields = {
        key: value
        for key in _DECLARATION_FIELDS
        if isinstance(value := declaration.get(key), str) and value and not base.get(key)
    }
    # LO QUE DECLARA EL AGENTE, que es la pregunta que gobierna estas guardas — y
    # NO lo que va a escribirse. El filtro de arriba descarta todo campo que el
    # criterio YA trae (`not base.get(key)`), así que un criterio con `check_type`
    # propio nunca lo mete en `fields`, y leer la rama de `fields` contestaba
    # "automated" pasara lo que pasara: las dos guardas de abajo se saltaban
    # enteras.
    #
    # Lo que eso dejaba pasar, medido: criterio ya `automated` + declaración
    # `manual` con `command: "true"` acababa ejecutando `true`, saliendo con 0 y
    # llegándole al reviewer como PASSED — un verde por un criterio cuya propia
    # declaración decía que ninguna máquina lo comprueba. Es literalmente la
    # salida barata que estas guardas existen para cerrar.
    declarado = declaration.get("check_type") or "automated"
    if declarado == "automated":
        if base.get("check_type") and base.get("check_type") != "automated":
            # El criterio ya estaba declarado NO automático, y eso no se pisa
            # (arriba se filtra `check_type`). Escribirle igualmente el comando
            # dejaría una fila que se contradice: «ninguna máquina comprueba esto»
            # con una máquina apuntada al lado — y que además nunca correría,
            # porque `test_runtime` salta todo lo que no sea `automated`. Un dato
            # muerto que induce a error al leer la ficha vale menos que ninguno.
            for key in ("runtime", "command", "expected_signal"):
                fields.pop(key, None)
        return fields
    # Declaración NO automática. Dos casos, y son distintos:
    if base.get("runtime") and base.get("command"):
        # (1) El criterio YA era ejecutable y no lo escribió este run. Dejar que
        # una declaración lo marque manual sería apagar con una frase el test que
        # otro escribió: `test_runtime` salta todo lo que no sea `automated`, y el
        # resultado se leería como un proyecto que legítimamente no tiene nada que
        # automatizar. Es la salida barata del §«El riesgo de que se juegue».
        fields.pop("check_type", None)
        fields.pop("reason", None)
        return fields
    # (2) El agente dice que no es automatizable Y adjunta un comando: la
    # declaración se contradice a sí misma. Se respeta la mitad que DECIDE y se
    # descarta el comando — quedarse con él ejecutaría algo que su propio autor
    # dijo que no verifica nada.
    for key in ("runtime", "command", "expected_signal"):
        fields.pop(key, None)
    return fields


def merge_declared_checks(criteria: list[Any], declarations: list[dict[str, Any]]) -> list[Any]:
    """Los criterios de la tarea con lo que el implementador declaró, FUSIONADO.

    Casa cada declaración con su criterio por texto normalizado o por ``id``, y
    la que no casa con ninguno **se descarta**: un modelo que se inventa un
    criterio no puede fabricar trabajo que nadie pidió, ni hacer desaparecer el
    silencio sobre uno que sí existe.

    Devuelve una lista con exactamente los mismos criterios en el mismo orden.
    Sin nada que aportar devuelve una lista igual a la de entrada, y entonces el
    caller ni siquiera escribe.
    """
    if not declarations:
        return list(criteria)
    pending = list(declarations)
    out: list[Any] = []
    for criterion in criteria:
        names = _criterion_names(criterion)
        match = next(
            (d for d in pending if _criterion_key(d.get("criterion")) in names),
            None,
        )
        if match is None:
            out.append(criterion)
            continue
        pending.remove(match)
        base: dict[str, Any] = (
            dict(criterion) if isinstance(criterion, dict) else {"description": str(criterion)}
        )
        fields = _accepted_fields(base, match)
        # Un criterio en prosa al que la declaración no aporta nada se queda en
        # prosa: convertirlo en dict sin añadir información sólo cambia la forma
        # del dato, y una escritura sin novedad es ruido de auditoría.
        out.append({**base, **fields} if fields else criterion)
    if pending:
        _log.info(
            "workers.declared_checks_unmatched",
            unmatched=len(pending),
            criteria=len(criteria),
        )
    return out


async def _persist_declared_checks(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    task_id: UUID,
    declarations: list[dict[str, Any]],
    fallback: list[Any],
) -> list[Any]:
    """Escribir la declaración en ``tasks.acceptance_criteria`` y devolver el resultado.

    Se fusiona contra lo que hay **en la fila**, no contra la foto que el run se
    llevó al empezar: el operador puede haber editado los criterios mientras el
    contenedor corría, y escribir una lista construida sobre una versión que ya
    no existe sería el pisotón por la puerta de atrás.

    Best-effort como el resto del post-proceso: el entregable ya está en el
    worktree y la execution ya está finalizada, así que un fallo aquí devuelve la
    foto del run (``fallback``) y la fase de tests sigue con lo que tenía.
    """
    if not declarations:
        return fallback
    try:
        async with sessionmaker() as session, session.begin():
            task = await session.get(Task, task_id)
            if task is None:
                return fallback
            current = list(task.acceptance_criteria or [])
            merged = merge_declared_checks(current, declarations)
            if merged == current:
                return current
            task.acceptance_criteria = merged
            _log.info(
                "workers.declared_checks_persisted",
                task_id=str(task_id),
                declarations=len(declarations),
                criteria=len(merged),
            )
            return merged
    except Exception as exc:  # pragma: no cover - defensive best-effort
        _log.warning("workers.declared_checks_persist_failed", task_id=str(task_id), error=str(exc))
        return fallback


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
    from workers.tasks import test_runtime_task

    # ADR 0129: thread the project's repository_config so the test-runtime brings
    # up the declared services (+ connection env) for the acceptance checks.
    # Best-effort: a lookup failure just runs the checks without services.
    repository_config: dict[str, Any] | None = None
    try:
        from api_server.db.domain import Project, Task
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from workers.db import worker_engine

        engine = worker_engine(settings)
        try:
            sm = async_sessionmaker(engine, expire_on_commit=False)
            async with sm() as session:
                proj_id = (
                    await session.execute(select(Task.project_id).where(Task.id == task_id))
                ).scalar_one_or_none()
                if proj_id is not None:
                    rc = (
                        await session.execute(
                            select(Project.repository_config).where(Project.id == proj_id)
                        )
                    ).scalar_one_or_none()
                    if isinstance(rc, dict):
                        repository_config = rc
        finally:
            await engine.dispose()
    except Exception as exc:  # pragma: no cover - best-effort enrichment
        _log.warning("workers.task_tests_repo_cfg_failed", task_id=str(task_id), error=str(exc))

    test_request = {
        "tenant_id": str(tenant_id),
        "task_id": str(task_id),
        "acceptance_criteria": autos,
        "worktree_host_path": worktree_host_path,
        "repository_config": repository_config,
    }
    # task_wf_22 (C-04): esto era `await _run_test_runtime(...)` EN PROCESO, o sea
    # el slot que este run acaba de liberar en la cola `default` se quedaba
    # orquestando Docker (runtime + servicios auxiliares + N checks de hasta
    # 600 s + teardown) con los recursos del worker equivocado. Ahora va a la cola
    # `test`, como `stack_exec` desde ADR 0093. Se sigue ESPERANDO a propósito: el
    # reviewer se despacha después y necesita un `<test-report>` real (C1/F51).
    try:
        await test_runtime_task.dispatch_test_runtime_and_wait(test_request)
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
    # ADR 0135: las acciones que un humano ya aprobó en ESTA task, por huella
    # canónica — el gate del sandbox las canjea en vez de re-aparcarlas.
    approved_actions: list[dict[str, Any]]
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
    # El abort_code que trae la ModelResolutionError (prod-07 task_prod07_07):
    # `model_unresolved` (catálogo) o `vault_unavailable` (Vault caído). Estaba
    # fijado a mano en el fail-fast, así que un fallo de Vault se reportaba como
    # problema de catálogo y mandaba a mirar al sitio equivocado.
    resolution_abort_code: str = "model_unresolved"


async def _prepare_run(
    session: AsyncSession,
    request: ExecutionRequest,
    *,
    task_id: UUID,
    tenant_id: UUID,
    vault_store: Any | None,
    celery_task_id: str | None,
) -> _PreparedRun | _SkippedRun | None:
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
    # `task_cv_13` (A-05): un mensaje de una reclamación que ya no es la vigente
    # se descarta ANTES de tocar nada — ni el supersede (cerraría la fila viva
    # de la reclamación actual), ni fila, ni worktree. Sólo el camino
    # implementador reclama; un review no lleva claim.
    if not request.review and not _claim_is_current(
        task_claim_id=getattr(task, "claim_id", None), request_claim_id=request.claim_id
    ):
        _log.warning(
            "workers.stale_claim_skipped",
            task_id=str(task_id),
            task_claim_id=getattr(task, "claim_id", None),
            request_claim_id=request.claim_id,
        )
        return _SkippedRun(abort_code="stale_claim")
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
    # `task_cv_15` (A-08): un run de REVIEW recibe una política que no aparca.
    # Su workspace es de sólo lectura y su único producto es el veredicto; un
    # review parado en `awaiting_human_approval` no lo reanuda nadie.
    approval_policy = (
        _review_run_policy()
        if request.review
        else await _resolve_effective_approval_policy(session, project)
    )
    guardrails_config = await _resolve_effective_guardrails(session, project)
    # ADR 0135: lo que un humano ya autorizó en esta task viaja al run siguiente.
    # SOLO al implementador: un run de REVIEW propone sus propias acciones y
    # nadie aprobó ninguna para él — canjearle las del implementador sería darle
    # una capacidad que ningún revisor leyó.
    approved_actions: list[dict[str, Any]] = []
    if not request.review:
        approved_actions = await read_approved_actions(
            session, task_id=task_id, tenant_id=tenant_id
        )
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
    resolution_abort_code = "model_unresolved"
    try:
        resolved_model = await resolve_model_spec(
            session,
            dict(request.model or {}),
            vault=vault_store if vault_store is not None else _default_vault_store(),
        )
    except ModelResolutionError as exc:
        resolution_error = str(exc)
        resolution_abort_code = exc.abort_code
    return _PreparedRun(
        execution_id=execution.id,
        approval_policy=approval_policy,
        approved_actions=approved_actions,
        guardrails=guardrails_config,
        worktree_inputs=worktree_inputs,
        review_worktree=review_worktree,
        task_acceptance_criteria=list(task.acceptance_criteria or []),
        plan_has_prior_work=plan_has_prior_work,
        resolved_model=resolved_model,
        resolution_error=resolution_error,
        resolution_abort_code=resolution_abort_code,
    )


@dataclass
class _Workspace:
    """Salida de la provisión del workspace (P3, fase 2 — git fuera de txn)."""

    host_path: str | None = None
    read_only: bool = False
    error: str | None = None
    error_code: str = "workspace_unavailable"
    # `task_wf_60`: el diff que el reviewer debe juzgar (solo en runs de review;
    # `None` cuando no hay worktree o git no da nada — el reviewer cae entonces
    # a la cosecha de ficheros de siempre).
    code_diff: str | None = None
    # Contrato `AGENT_TRACKED_PATHS`: los directorios VERSIONADOS en la rama del
    # plan, a cualquier profundidad. Vacía = sin protección (proyecto sin
    # commits, run sin worktree, workspace read-only o fallo de git).
    tracked_paths: list[str] = field(default_factory=list)


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
                project_id=str(_wt_project_id) if _wt_project_id else None,
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
        # `task_wf_60`: el DIFF de la tarea, calculado aquí porque este es el
        # único punto que tiene a la vez el worktree resuelto y git. Se entrega
        # ya hecho en el prompt del reviewer, igual que el `<test-report>`: al
        # sandbox no se le da git (principio 2).
        ws.code_diff = compute_task_review_diff(ws.host_path, str(task_id))
    # Contrato `AGENT_TRACKED_PATHS`: qué parte de `/workspace` está versionada.
    # Se calcula AQUÍ por la misma razón que el diff del reviewer —es el único
    # punto con worktree y git a la vez— y además porque justo después
    # `conduct_execution` esconde el `.git` (ADR 0163): dentro del sandbox ya no
    # queda a quién preguntar.
    #
    # Sólo cuando el workspace es ESCRIBIBLE. En un run de review el worktree del
    # implementador se monta read-only (ADR 0095), así que el agente no puede
    # borrar nada y no hay protección que publicar; el mismo criterio que usa la
    # guarda de `git_link_hidden`.
    if ws.host_path is not None and not ws.read_only and ws.error is None:
        ws.tracked_paths = compute_tracked_paths(ws.host_path)
    return ws


RUNTIME_SPEC_FILENAME = "task-spec.json"
RUNTIME_TOKEN_FILENAME = "internal-token"


def _stage_runtime_secrets(
    env: dict[str, str], *, settings: Settings, existing: StagedSecrets | None = None
) -> tuple[dict[str, str], StagedSecrets | None]:
    """Saca el spec y el token interno del env del contenedor y los deja en
    `/run/secrets` (`task_cv_20`, auditoría 2026-09-01 D-01).

    El patrón es el de la credencial del modelo (prod-07): ficheros read-only
    en un staging del worker, bind-mounteado en `/run/secrets`, y en el env
    sólo los PUNTEROS (`AGENT_TASK_SPEC_FILE`, `AGENTIC_INTERNAL_TOKEN_FILE`).
    Lo que se protege: las cabeceras y env de los servidores MCP, las acciones
    aprobadas y el código de las python_function que viajan en el spec, y el
    token que autoriza `mcp-oauth-token` — todo legible antes por cualquier
    hijo del runtime.

    Un contenedor sólo puede montar `/run/secrets` una vez: si la credencial
    del modelo ya tiene su staging (``existing``), los ficheros se escriben en
    ESE directorio y se devuelve ``None`` (su ``cleanup()`` los retira); si no,
    se crea uno propio. Mismo flag y misma caída en abierto que la credencial:
    si el staging no se puede escribir, se vuelve al formato en línea con aviso
    (y el runtime nuevo lo retira igualmente del env al arrancar)."""
    if not settings.model_credential_file:
        return env, None
    files: dict[str, str] = {}
    if "AGENT_TASK_SPEC" in env:
        files[RUNTIME_SPEC_FILENAME] = env["AGENT_TASK_SPEC"]
    if env.get("AGENTIC_INTERNAL_TOKEN"):
        files[RUNTIME_TOKEN_FILENAME] = env["AGENTIC_INTERNAL_TOKEN"]
    if not files:
        return env, None
    staged: StagedSecrets | None = None
    try:
        if existing is not None:
            for name, payload in files.items():
                target = existing.staging_dir / name
                target.write_text(payload, encoding="utf-8")
                os.chmod(target, 0o444)
        else:
            staging_root = Path(settings.data_root) / STAGING_SUBDIR
            staging_root.mkdir(parents=True, exist_ok=True)
            staged = stage_secrets(files, base_dir=str(staging_root))
    except OSError as exc:
        _log.warning(
            "workers.runtime_secrets_staging_failed",
            error=str(exc),
            detail="el spec y el token siguen en el env del contenedor (formato antiguo)",
        )
        return env, None
    public = {
        k: v for k, v in env.items() if k not in ("AGENT_TASK_SPEC", "AGENTIC_INTERNAL_TOKEN")
    }
    public["AGENT_TASK_SPEC_FILE"] = f"{SECRETS_DIR}/{RUNTIME_SPEC_FILENAME}"
    if RUNTIME_TOKEN_FILENAME in files:
        public["AGENTIC_INTERNAL_TOKEN_FILE"] = f"{SECRETS_DIR}/{RUNTIME_TOKEN_FILENAME}"
    return public, staged


def _stage_model_credentials(
    resolved_model: dict[str, Any] | None,
    *,
    settings: Settings,
) -> tuple[dict[str, Any] | None, StagedSecrets | None]:
    """Saca la credencial del spec y la deja en un mount read-only (prod-07 task_prod07_10).

    Devuelve ``(spec público, staging)``. El staging es ``None`` —y no hay mount—
    en los tres casos en que no hay nada que esconder: el flag apagado, un modelo
    sin credencial (ollama local, kind ``scripted``) y un ``resolved_model``
    vacío. Quien lo reciba **debe** llamar a ``cleanup()`` en un ``finally``.

    Falla en abierto a propósito: si el staging no se puede escribir —disco
    lleno, permisos, ``data_root`` no montado— se vuelve al formato en línea con
    un aviso en vez de tumbar el run. La alternativa (abortar) convertiría un
    problema de disco del worker en «ninguna tarea del tenant se ejecuta», que es
    un fallo mucho peor que el que esta tarea previene.
    """
    if not settings.model_credential_file:
        return resolved_model, None
    public_model, secrets = split_model_credentials(resolved_model)
    if not secrets:
        return resolved_model, None
    staging_root = Path(settings.data_root) / STAGING_SUBDIR
    try:
        staging_root.mkdir(parents=True, exist_ok=True)
        staged = stage_model_credentials(secrets, base_dir=str(staging_root))
    except OSError as exc:
        _log.warning(
            "workers.model_credential_staging_failed",
            error=str(exc),
            staging_root=str(staging_root),
            detail=(
                "no se pudo escribir el fichero de credencial del modelo; el run "
                "sigue con el formato antiguo (credencial en AGENT_TASK_SPEC)"
            ),
        )
        return resolved_model, None
    return public_model, staged


async def _launch_and_stream(  # noqa: PLR0912, PLR0915 - lanzamiento + streaming + poll de cancelación
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
) -> tuple[_RuntimeResult, dict[str, Any] | None, list[dict[str, Any]]]:
    """Fase 3 (P3): lanza el contenedor agent-runtime, streamea su stdout al
    stream Redis `exec:{id}` y pliega los eventos en el resultado del run.

    Devuelve ``(resultado, approval, declaraciones)``. ``approval`` es el payload
    que emite un run aparcado en una acción sensible (task_02_33), ``None`` en el
    resto; ``declaraciones`` es con qué dijo el implementador que se verifica cada
    criterio (ADR 0162, opción A), lista vacía cuando no declaró nada.

    Las dos salen del MISMO sitio —el sobre `execution.finished`— y no de
    `_RuntimeResult`: ninguna de las dos es una columna de la fila `executions`,
    son payloads que dirigen el post-proceso. Meterlas en el resultado que
    `finalize_execution` persiste sería guardarlas donde nadie las va a leer."""
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
    # prod-07 task_prod07_10: la credencial sale del env y entra por un mount
    # read-only; `public_model` es el mismo spec con el PUNTERO en su lugar. Sin
    # credencial que mover (ollama local, kind scripted) no se monta nada y el
    # spec sale idéntico — no se paga por lo que no se usa.
    public_model, staged_credentials = _stage_model_credentials(
        prepared.resolved_model, settings=settings
    )
    container_spec = ContainerSpec(
        image=settings.agent_runtime_image,
        env=_build_runtime_env(
            request,
            prepared.approval_policy,
            agent_internal_api_url=settings.agent_internal_api_url,
            # El spec RESUELTO (kind + endpoint + credencial) — ADR 0057 F1.
            model_spec=public_model,
            # La definición de "hecho" de la tarea → al prompt de decisión,
            # para que el comportamiento (leer/escribir/test) lo dicte la tarea.
            acceptance_criteria=prepared.task_acceptance_criteria,
            # ADR 0102 D3: config de guardrails resuelta (o None → baseline).
            guardrails=prepared.guardrails,
            # ADR 0110 (mitad HTTP, EXPERIMENTAL, default OFF).
            conversation_thread=settings.runtime_conversation_thread,
            # ADR 0112 fase 2 (EXPERIMENTAL, default OFF).
            reflection_assess=settings.runtime_reflection_assess,
            # `task_wf_60`: el diff de la tarea para el prompt del reviewer, que
            # lo calculó la provisión del workspace (único punto con worktree +
            # git). `None` en runs de implementación y en reviews sin worktree.
            code_diff=workspace.code_diff,
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
            # ADR 0135: lo que un humano ya aprobó en esta task — el gate del
            # sandbox lo canjea en vez de volver a aparcar la misma acción.
            approved_actions=prepared.approved_actions,
            # Qué hay VERSIONADO en `/workspace`, calculado por la provisión (el
            # único punto con git). Sin esto el runtime no puede distinguir el
            # entregable commiteado de otra tarea de un artefacto reconstruible.
            tracked_paths=workspace.tracked_paths,
        ),
        labels={"com.agentic-platform.execution-id": exec_id},
        workspace_host_path=workspace.host_path,
        workspace_read_only=workspace.read_only,
        extra_mounts=tuple(staged_credentials.mounts) if staged_credentials else (),
        # `task_cv_25`: los servidores MCP internos del proyecto se conectan al
        # bridge de esta ejecución (los mismos que van a NO_PROXY).
        peers=tuple(_internal_mcp_hosts(request)),
    )
    # `task_cv_20`: el spec y el token salen del env y van por fichero.
    runtime_env, staged_runtime_secrets = _stage_runtime_secrets(
        dict(container_spec.env), settings=settings, existing=staged_credentials
    )
    container_spec = dataclasses.replace(
        container_spec,
        env=runtime_env,
        extra_mounts=(
            *container_spec.extra_mounts,
            *(staged_runtime_secrets.mounts if staged_runtime_secrets is not None else ()),
        ),
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
        and the run finalises as ``cancelled``.

        El vigía es un ACCESORIO del run, no su dueño (auditoría 2026-09-01,
        A-03): son ~2.400 consultas en un run de dos horas, y un blip de BD en
        cualquiera de ellas no puede costar el run. Antes la excepción subía sin
        capturar y el `finally` de abajo la re-lanzaba al `await watcher` justo
        cuando el contenedor acababa de terminar bien: `execution.finished`
        descartado, fila `running` hasta que el sweeper la sellaba con una
        etiqueta falsa, tarea `blocked` y la credencial staged en disco. Ahora un
        sondeo que falla se registra y se reintenta en el siguiente tick."""
        nonlocal cancel_seen
        while True:
            await asyncio.sleep(cancel_poll_interval_s)
            try:
                async with sessionmaker() as cancel_session:
                    ex = await get_execution(cancel_session, prepared.execution_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _log.warning(
                    "workers.cancel_watch_poll_failed",
                    execution_id=exec_id,
                    error=str(exc)[:200],
                    note="se reintenta en el siguiente sondeo; el run sigue",
                )
                continue
            if ex is not None and ex.cancel_requested_at is not None:
                cancel_seen = True
                await asyncio.to_thread(active_runner.kill_by_label, exec_id)
                return

    watcher = asyncio.create_task(_watch_for_cancel())
    # `task_cv_15` (A-07): un `APIError` del daemon al crear/arrancar el contenedor
    # salía de aquí sin capturar — fila `running`, mensaje a la DLQ y, cinco
    # minutos después, un sello falso de «worker loss». El lanzamiento que falla
    # es un resultado `failed` con nombre y se finaliza como cualquier otro. El
    # `SoftTimeLimitExceeded` de Celery NO se captura: tiene su propio camino en
    # `tasks.run_cycle` (`_finalize_soft_timeout`).
    from celery.exceptions import SoftTimeLimitExceeded

    launch_error: Exception | None = None
    container_result: ContainerResult | None = None
    try:
        # `container_timeout` = the per-kind budget + grace (F19): the hard
        # backstop, set ABOVE the loop's internal wall-clock so the clean abort wins.
        container_result = await asyncio.to_thread(
            active_runner.run_streamed, container_spec, on_line, timeout=container_timeout
        )
    except SoftTimeLimitExceeded:
        raise
    except Exception as exc:
        launch_error = exc
    finally:
        # El staging del secreto se borra SIEMPRE — timeout, cancelación,
        # excepción del daemon — y ANTES de esperar al vigía: un `finally` propio
        # que no dependa de nada más, porque lo que deja ficheros olvidados es
        # justamente el camino que revienta (prod-07 task_prod07_10; A-03).
        try:
            if staged_credentials is not None:
                staged_credentials.cleanup()
            if staged_runtime_secrets is not None:
                staged_runtime_secrets.cleanup()
        finally:
            watcher.cancel()
            # Sólo la cancelación es esperable aquí. Cualquier otra excepción del
            # vigía es un defecto suyo, y se registra en vez de propagarse por
            # encima del resultado real del contenedor.
            try:
                await watcher
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                _log.error(
                    "workers.cancel_watch_crashed",
                    execution_id=exec_id,
                    error=str(exc)[:200],
                    note="el resultado del contenedor manda; el vigía no",
                )
    await queue.put(None)
    await drainer
    if launch_error is not None or container_result is None:
        _log.error(
            "workers.container_launch_failed",
            execution_id=exec_id,
            task_id=request.task_id,
            error_type=type(launch_error).__name__ if launch_error else "no_result",
            error=str(launch_error)[:300],
        )
        failed = _RuntimeResult(
            status="failed",
            abort_code="container_launch_failed",
            output=(
                "el contenedor del run no pudo lanzarse: "
                f"{type(launch_error).__name__}: {launch_error}"
                if launch_error
                else "el contenedor del run no devolvió resultado"
            )[:2000],
            iterations=0,
            steps=steps,
            usage={},
        )
        return failed, None, _declared_checks_from_result(None)

    # P3.3/F15: a cancel sealed between the watcher's last poll and the
    # container exit is missed by the watcher. Do a final one-shot read of
    # `cancel_requested_at` so an operator cancel is never lost to that race.
    if not cancel_seen:
        # Misma regla que el vigía (A-03): un blip de BD en esta lectura no
        # puede costar el resultado real del contenedor. Si no se puede leer, se
        # asume «no cancelado»: el sello de `finalize_execution` es quien manda,
        # y un cancel llegado tan tarde lo recoge el reconciler.
        try:
            async with sessionmaker() as cancel_session:
                ex = await get_execution(cancel_session, prepared.execution_id)
        except Exception as exc:
            _log.warning(
                "workers.cancel_final_read_failed", execution_id=exec_id, error=str(exc)[:200]
            )
            ex = None
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
        # `task_wf_62`: el digest de la imagen que corrió DE VERDAD.
        image_digest=container_result.image_digest,
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
            prompt_version=result.prompt_version,
            runtime_image_digest=result.runtime_image_digest,
        )
    approval = final_result.get("approval") if final_result else None
    return result, approval, _declared_checks_from_result(final_result)


async def _finalize_and_transition(
    sessionmaker: async_sessionmaker[AsyncSession],
    request: ExecutionRequest,
    *,
    execution_id: UUID,
    task_id: UUID,
    tenant_id: UUID,
    result: _RuntimeResult,
    approval: dict[str, Any] | None,
    approval_policy: dict[str, Any] | None = None,
) -> tuple[tuple[Any, str, str] | None, bool]:
    """Fase 4 (P3): finaliza la fila + persiste guardrails + transiciona la task,
    TODO en una txn (P0.5 — un crash aquí no puede dejar la execution terminal
    con la task `in_progress` para siempre).

    ``approval_policy`` es la política EFECTIVA con la que se lanzó el run (la
    misma que recibió el runtime, ADR 0104). El gate tiene dos mitades —el
    runtime aparca, el worker crea la solicitud— y las dos tienen que leer la
    misma política (auditoría 2026-09-01, A-01).

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
            # `task_gov_06`: y, con el veredicto YA aplicado, se mide cuánto de
            # él venía en el relato del implementador. Post-proceso puro (cero
            # tokens, cero llamadas al modelo) y best-effort — ver
            # `_record_review_contamination`.
            await _record_review_contamination(
                session,
                task_id=task_id,
                tenant_id=tenant_id,
                review_execution_id=execution_id,
                reviewer_agent_id=UUID(request.agent_id) if request.agent_id else None,
                reviewer_output=result.output or "",
            )
        elif result.status == _AWAITING_APPROVAL and approval:
            execution = await get_execution(session, execution_id)
            project = await _load_project(session, task_id)
            task = await session.get(Task, task_id)
            if execution is not None and project is not None and task is not None:
                old_status = task.status
                created = await request_approval_if_needed(
                    session,
                    execution=execution,
                    project=project,
                    category=str(approval.get("category", "")),
                    action=dict(approval.get("action") or {}),
                    policy=approval_policy,
                )
                if created is None:
                    # El runtime aparcó y el worker decide que no hacía falta: las
                    # dos mitades del gate discrepan. Antes esto dejaba la fila
                    # `awaiting_human_approval` sin solicitud y la tarea
                    # `in_progress` para siempre (auditoría 2026-09-01, A-01):
                    # ninguna red lo recupera. Se falla CERRADO y con nombre, como
                    # F12 hace con un aparcado sin payload, para que la tarea siga
                    # su curso y el motivo quede escrito.
                    _log.error(
                        "workers.approval_policy_mismatch",
                        execution_id=str(execution_id),
                        task_id=str(task_id),
                        category=str(approval.get("category", "")),
                    )
                    execution.status = "failed"
                    execution.abort_code = "approval_policy_mismatch"
                    execution.completed_at = datetime.now(UTC)
                    note = (
                        "[approval_policy_mismatch] el runtime aparcó la acción "
                        f"'{approval.get('category', '')}' pero la política con la que se "
                        "evaluó al cerrar no la exige; se falla cerrado en vez de dejar la "
                        "ejecución esperando una aprobación que nadie va a crear"
                    )
                    execution.output = f"{execution.output}\n{note}" if execution.output else note
                    implementer_path = True
                    task_event = await transition_task_after_run(session, task_id, "failed")
                elif task.status != old_status:
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
    check_declarations: list[dict[str, Any]],
) -> tuple[Any, str, str] | None:
    """Fase 5 (P3): post-proceso del camino implementador (prod-18) — commit +
    tests ANTES de que el evento de estado sea visible (ya persistido en fase 4;
    lo publica el caller de conduct_execution tras soltar el run-lock, H1).

    task_prod18_commit_01: a run that wrote into a worktree gets committed (with
    trailers) + pushed to the plan branch by the WORKER (the sandbox has no git
    credentials). P2.3/F26: commit for a clean `done` AND for an escalation
    (`needs_human_review`) so the human validator gets the diff. task_prod18_
    test_01: tests run over the worktree only for a `done` run. All best-effort.

    ADR 0162 (opción A, ola 2): ``check_declarations`` es lo que el implementador
    declaró sobre cómo se verifica cada criterio. Va como argumento OBLIGATORIO
    —sin default— porque un default lo convertiría en la lista vacía por omisión
    en cuanto alguien añadiese un caller, y una lista vacía significa «nadie
    declaró nada»: exactamente el silencio que la opción A retira."""
    # AUD16-02: aplicar los task_comment que el agente emitió durante el run
    # (efectos del sink → steps_log → PlanComment). Best-effort, para TODOS los
    # estados terminales — una nota de un run fallido es a menudo la más útil.
    from workers.orchestration_drain import drain_task_comment_effects

    await drain_task_comment_effects(
        sessionmaker,
        steps=result.steps,
        task_id=task_id,
        tenant_id=tenant_id,
    )
    # ADR 0162 (opción A, ola 2): lo que el implementador declaró pasa a ser
    # criterio de la tarea, ANTES de la fase de tests. El orden no es estético: si
    # se escribiera después, este run seguiría lanzando los tests con los
    # criterios en prosa que el agente acaba de sustituir y el circuito sólo se
    # cerraría un run más tarde. Se hace para TODOS los estados terminales del
    # camino implementador, no sólo para `done`: lo que un run escalado averiguó
    # sobre cómo se verifica la tarea le vale a quien la retome.
    acceptance_criteria = await _persist_declared_checks(
        sessionmaker,
        task_id=task_id,
        declarations=check_declarations,
        fallback=prepared.task_acceptance_criteria,
    )
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
                acceptance_criteria=acceptance_criteria,
            )
        if commit_abort_code:
            # P2.3(b)/F13 + P7: a real git failure — surface it (with its
            # specific abort_code) on the execution row instead of reporting a
            # deliverable with an empty diff. Anticipo ADR 0099: el contexto
            # estructurado del conflicto viaja junto al código.
            code, conflict_context = commit_abort_code
            # `task_cv_11`: el marcador también bloquea la tarea; su evento
            # sustituye al `in_review` pendiente (lo devuelve al caller).
            return await _mark_commit_failed(
                sessionmaker,
                prepared.execution_id,
                code,
                conflict_context=conflict_context,
                task_id=task_id,
                tenant_id=tenant_id,
            )
    return None
    # The deferred state-change event is NOT published here (H1): it travels on
    # the ExecutionOutcome so the caller publishes it after releasing the
    # run-lock. The prod-18 ordering still holds — the commit above exists
    # before any consumer can see the event.


# NOTIF-3: estados de run que notifican execution_failed (prioridad). `done`
# emite execution_finished (opt-in: sin default de canal para no inundar).
_EXECUTION_FAILED_STATUSES = frozenset({"failed", "aborted"})

# AUD16-23: marcadores de fallo de CREDENCIAL/cuota del provider en la salida
# de un abort provider_error/provider_timeout. El probe manual de claude_sdk
# solo verifica PRESENCIA de la credencial — la caducidad (oauth) y la cuota
# solo se manifiestan en el primer run que las pisa; ese run debe avisar YA.
_CREDENTIAL_FAILURE_MARKERS: tuple[str, ...] = (
    "not logged in",
    "auth failed",
    "(401)",
    "401 unauthorized",
    "invalid api key",
    "credential",
    "session limit",
    "rate-limited",
    "http 429",
)
_PROVIDER_ABORT_CODES = frozenset({"provider_error", "provider_timeout"})


def _is_credential_failure_output(output: str | None) -> bool:
    """Whether an aborted run's output smells like a credential/quota failure."""
    if not output:
        return False
    lowered = output.lower()
    return any(marker in lowered for marker in _CREDENTIAL_FAILURE_MARKERS)


async def _notify_execution_outcome(
    *,
    tenant_id: str,
    task_id: str,
    task_title: str,
    status: str,
    abort_code: str | None,
    output: str | None = None,
) -> None:
    """Encola execution_failed/execution_finished al dispatcher (NOTIF-3).

    AUD16-23: un abort de provider con marcadores de credencial/cuota emite
    ADEMÁS ``provider_credential_invalid`` platform-scoped (tenant NULL — la
    credencial del provider es de plataforma y la resuelve el System Admin;
    en el ciclo 07-02→07-08 hubo 17 aborts así y nadie se enteró hasta la
    forense).

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
        if (
            event_type == "execution_failed"
            and abort_code in _PROVIDER_ABORT_CODES
            and _is_credential_failure_output(output)
        ):
            await enqueue_event_dispatch(
                {
                    "event_type": "provider_credential_invalid",
                    "tenant_id": None,
                    "context": {
                        "task_title": task_title,
                        "task_id": task_id,
                        "abort_code": abort_code or "",
                        # Fragmento acotado con el marcador — la salida de un
                        # abort de auth no lleva credenciales.
                        "detail": (output or "")[:300],
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
    if prepared is None or isinstance(prepared, _SkippedRun):
        return ExecutionOutcome(
            execution_id="",
            status="skipped",
            abort_code=prepared.abort_code if prepared is not None else "ineligible_task_status",
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
    # ADR 0162 (opción A, ola 2): con qué declaró el implementador que se verifica
    # cada criterio. Vacío en los caminos de fail-fast — sin contenedor no hay
    # quien declare, y eso es AUSENCIA, nunca «no hay nada que verificar».
    check_declarations: list[dict[str, Any]] = []
    failfast: tuple[str, str] | None = None
    if prepared.resolution_error is not None:
        # Fail-fast (ADR 0057 F1): sin proveedor resoluble NO se lanza el
        # contenedor — la ejecución termina `failed` con motivo explícito en
        # vez de correr en silencio con el cliente scripted.
        _log.error(
            "workers.model_resolution_failed",
            execution_id=exec_id,
            error=prepared.resolution_error,
            abort_code=prepared.resolution_abort_code,
        )
        # El código VIENE del error (task_prod07_07): `vault_unavailable` manda a
        # revisar Vault, `model_unresolved` a revisar el catálogo. Fijarlo aquí
        # borraba esa distinción justo en el sitio donde el operador la lee.
        failfast = (prepared.resolution_abort_code, prepared.resolution_error)
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
        # ADR 0163: el `.git` del worktree NO existe mientras corre el agente.
        # Dentro del sandbox es inutil (apunta a metadatos no montados), y en
        # cambio estorba a los andamiadores que exigen directorio vacio. El
        # 2026-08-31 un agente lo borro para poder ejecutar `composer
        # create-project`, instalo CodeIgniter correctamente y el cierre murio con
        # `fatal: not a git repository`.
        #
        # Solo cuando el workspace es ESCRIBIBLE. En un run de review el worktree
        # del implementador se monta de solo lectura (ADR 0095), asi que el agente
        # no puede romperlo y no hay nada que proteger; tocarlo ahi ademas
        # arriesgaria pisar a un run concurrente sobre el mismo worktree.
        #
        # `owner` es esta ejecucion: el lock del worktree lleva su id, y solo ella
        # repone el puntero y suelta el lock. Con ejecuciones solapadas de la
        # misma tarea (gotcha «deploy relaunches frozen tasks»), la vieja no puede
        # pisar a la nueva (auditoria 2026-09-01).
        from workers.plan_git import git_link_hidden

        ocultar = workspace.host_path is not None and not workspace.read_only
        wt_path = Path(workspace.host_path) if workspace.host_path else None
        with (
            git_link_hidden(wt_path, owner=str(exec_id))
            if ocultar and wt_path
            else contextlib.nullcontext()
        ):
            result, approval, check_declarations = await _launch_and_stream(
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

    # F12 + A-08: un aparcamiento que no puede reanudarse se sella con nombre en
    # vez de dejar el run no terminal (ver `_seal_invalid_park`).
    result = _seal_invalid_park(
        is_review=request.review, result=result, approval=approval, exec_id=exec_id
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
        # La MISMA política que recibió el runtime (A-01): sin ella la mitad del
        # worker evaluaba la cruda del proyecto y no creaba la solicitud.
        approval_policy=prepared.approval_policy,
    )

    # H1: NO event is published here (neither path). The finish event travels on
    # the ExecutionOutcome (`pending_task_event`) and the caller publishes it
    # AFTER releasing the per-task run-lock — the orchestrator's immediate
    # dispatch (reviewer on in_review, re-dispatch on reject→backlog) otherwise
    # lands while the lock is still held and is dropped (`concurrent_run_locked`).
    if implementer_path:
        blocked_event = await _implementer_post_process(
            settings,
            sessionmaker,
            prepared=prepared,
            workspace=workspace,
            result=result,
            task_id=task_id,
            tenant_id=tenant_id,
            exec_id=exec_id,
            check_declarations=check_declarations,
        )
        if blocked_event is not None:
            # `task_cv_11`: el trabajo no llegó a la rama del plan y la tarea
            # quedó `blocked`; publicar el `in_review` de la fase 4 haría que el
            # orquestador despachara un reviewer sobre trabajo perdido.
            task_event = blocked_event

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
        # AUD16-23: la salida del abort lleva los marcadores de credencial.
        output=result.output,
    )

    _log.info("workers.execution_finished", execution_id=exec_id, status=result.status)
    return ExecutionOutcome(
        execution_id=exec_id,
        status=result.status,
        abort_code=result.abort_code,
        pending_task_event=task_event,
    )
