"""Vocabularios cerrados del dominio de fase 1.

Son `StrEnum` a proposito: el valor que viaja a PostgreSQL es el string, y varias
migraciones derivan sus `CHECK` de estas listas (`0101_tasks_status_check` copia
`TaskStatus` inline). Renombrar un miembro no rompe ningun import de Python y si
deja filas historicas fuera del `CHECK` que las validaba.
"""

from __future__ import annotations

import enum


# =============================================================================
# Enums (StrEnum so values are stable strings persisted as TEXT)
# =============================================================================
class AgentType(enum.StrEnum):
    AI = "ai"
    HUMAN = "human"


class AssignmentMode(enum.StrEnum):
    """How a human agent's tasks are routed to actual people (Plan 16).

    - ``SPECIFIC_USER``: tasks go to one fixed :class:`User`
      (``human_agent_config.assigned_user_id``). The ONLY mode in the MVP
      (Plan 16 Decisiones Clave) — a DB CHECK constrains the column to it.
    - ``ROLE_QUEUE`` / ``TEAM_POOL``: queue- and team-based routing. Modelled
      here for forward-compatibility but explicitly out of scope this plan;
      the CHECK rejects them until a later plan lifts the constraint.
    """

    SPECIFIC_USER = "specific_user"
    ROLE_QUEUE = "role_queue"
    TEAM_POOL = "team_pool"


class AgentScope(enum.StrEnum):
    """Where an agent lives in the linked-vs-forked taxonomy (spec §5.7.5).

    - GLOBAL_BUILTIN: shipped with the platform, owned by the platform
      tenant. Visible to every tenant; only system admins write.
    - GLOBAL_TENANT_TEMPLATE: a tenant's own template, available to its
      projects.
    - PROJECT_LOCAL: tied to a single project; `project_id` is mandatory.
    """

    GLOBAL_BUILTIN = "global_builtin"
    GLOBAL_TENANT_TEMPLATE = "global_tenant_template"
    PROJECT_LOCAL = "project_local"


class AgentRole(enum.StrEnum):
    """Built-in agent roles. CUSTOM is the escape hatch for tenant-defined
    roles whose label lives in `description` or a future role catalog."""

    PROJECT_MANAGER = "project_manager"
    ARCHITECT = "architect"
    BACKEND_DEV = "backend_dev"
    FRONTEND_DEV = "frontend_dev"
    QA = "qa"
    REVIEWER = "reviewer"
    LEADER = "leader"
    WORKER = "worker"
    SPECIALIST = "specialist"
    RESEARCHER = "researcher"
    DEVOPS = "devops"
    SECURITY = "security"
    TECHNICAL_WRITER = "technical_writer"
    CUSTOM = "custom"


class MemoryScope(enum.StrEnum):
    PRIVATE = "private"
    TEAM_SHARED = "team_shared"
    PROJECT_SHARED = "project_shared"
    GLOBAL = "global"


class MemoryType(enum.StrEnum):
    """Episodic vs semantic distinction for agent memory (Plan 04 task_04_01).

    - ``episodic``: a concrete event the agent lived through ("the
      psycopg3 import failed in project X on 2026-05-25").
    - ``semantic``: a rule or generalisation distilled from one or
      more episodes ("project X uses asyncpg, not psycopg3").

    The Memorizer (`task_04_03`) decides which type each entry gets
    when it distils an `Execution`; the `memory_recall` tool can
    filter by type."""

    EPISODIC = "episodic"
    SEMANTIC = "semantic"


class SkillCategory(enum.StrEnum):
    """Cerrada *categoría* de skill (ADR 0050, task_06_18_13).

    Los valores son EXACTAMENTE las categorías que usan las skills seedeadas
    (``api_server.seeds.builtin_skills``). Antes este enum tenía nueve valores
    divergentes (``coding``/``review``/``planning``/``data``/``security``…) que
    no coincidían con el seed y nunca se validaban; ADR 0050 los alinea y aplica
    un ``CHECK`` en BD (migración 0078, reconstruido por 0117) construido desde
    este mismo conjunto, de modo que base de datos y aplicación concuerdan.

    ``atlassian`` (2026-07-23) es un bucket de INTEGRACIÓN, no un dominio de
    trabajo como los otros seis: agrupa las skills que enseñan a los agentes a
    usar Jira/Confluence vía el MCP de Atlassian del proyecto (ADR 0127/0128).
    """

    BACKEND = "backend"
    FRONTEND = "frontend"
    DEVOPS = "devops"
    QA = "qa"
    RESEARCH = "research"
    DOCS = "docs"
    ATLASSIAN = "atlassian"


class ToolCategory(enum.StrEnum):
    """Closed *Función* facet of the tool taxonomy (ADR 0049).

    The seven function buckets are exactly the ``category`` values used by the
    built-in seed rows (``api_server.seeds.builtin_tools`` — currently 15, the
    git family was retired in task_06_18_06); a CI contract test (task_06_18_14)
    asserts this enum stays a superset of the seed, so the exact count is not
    hardcoded here to avoid drift.

    Three extra buckets carry tools that are *not* in the catalog seed:

      - ``MCP`` — tools imported from an MCP server (origin facet ``mcp_tool``;
        ADR 0052), namespaced ``<server>.<tool>``.
      - ``ORCHESTRATION`` — runtime-registered orchestration tools
        (``kanban_update`` / ``task_comment`` / ``agent_invoke``; ADR 0048).
      - ``CUSTOM`` — the catch-all for tenant-authored tools that do not map to
        a function bucket above.

    The DB-level CHECK on ``tools.category`` (migration 0077) is built from this
    same value set so the database and the application agree.
    """

    FILE = "file"
    RUNTIME = "runtime"
    GIT = "git"
    NETWORK = "network"
    KNOWLEDGE = "knowledge"
    NOTIFICATION = "notification"
    COMMAND = "command"
    MCP = "mcp"
    ORCHESTRATION = "orchestration"
    CUSTOM = "custom"


class ToolImplementationType(enum.StrEnum):
    BUILTIN = "builtin"
    PYTHON_FUNCTION = "python_function"
    HTTP_ENDPOINT = "http_endpoint"
    MCP_TOOL = "mcp_tool"
    DOCKER_COMMAND = "docker_command"


class ToolSecurityLevel(enum.StrEnum):
    SAFE = "safe"
    SANDBOXED = "sandboxed"
    PRIVILEGED = "privileged"


class AgentSkillProficiency(enum.StrEnum):
    BASIC = "basic"
    STANDARD = "standard"
    EXPERT = "expert"


class ProjectStatus(enum.StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class HumanTaskReviewMode(enum.StrEnum):
    """How a human task's deliverable is reviewed once submitted (Plan 16
    task_16_11), a project-level setting (``projects.human_task_review_mode``).

    - ``AUTO_APPROVE`` (default): the submit path (task_16_09) takes the task
      straight to ``done`` — no extra review step. The MVP default (Plan 16
      Decisiones Clave): suitable for "firma"-style tasks where the act of
      submitting IS the completion.
    - ``PEER_HUMAN_REVIEWER``: the task stays ``in_review`` and a SECOND
      :class:`HumanTaskAssignment` is created for another Human Agent (the
      reviewer), who approves (-> ``done``) or rejects with feedback (-> back
      to ``backlog`` with ``retry_count`` bumped; after ``max_retries`` the
      §7.9 retry/escalation infra parks it in ``awaiting_human_approval``).

    The ``ai_reviewer`` mode is explicitly out of scope this plan (Plan 16
    Alcance); the DB CHECK ``ck_projects_human_task_review_mode`` rejects any
    value outside these two.
    """

    AUTO_APPROVE = "auto_approve"
    PEER_HUMAN_REVIEWER = "peer_human_reviewer"


class BudgetPeriod(enum.StrEnum):
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"
    CUSTOM = "custom"


class PlanStatus(enum.StrEnum):
    """Full lifecycle of a plan (Plan 03 task_03_16 / task_03_25).

    Transitions are enforced in `api_server.chat.plan_state_machine`.
    A freshly POSTed plan from the chat lands in ``draft``; the human
    moves it to ``pending_approval`` to start the review.

    When the AI cost estimate exceeds the platform-configured double-
    signature threshold (task_03_25), the first approval moves the
    plan to ``pending_second_approval``; a **different** signer must
    confirm to reach ``approved``. Below the threshold a single firma
    is enough (``pending_approval -> approved``).

    Executions of approved plans flip them through ``in_progress``,
    ``blocked``, then ``pending_human_validation`` and finally
    ``completed``.
    """

    PENDING_APPROVAL = "pending_approval"
    PENDING_SECOND_APPROVAL = "pending_second_approval"
    DRAFT = "draft"
    APPROVED = "approved"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    PENDING_HUMAN_VALIDATION = "pending_human_validation"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class TaskStatus(enum.StrEnum):
    BACKLOG = "backlog"
    READY = "ready"
    # La tarea ha sido asignada a un Human Agent (agent_type=human) y
    # espera que el User asignado la acepte (Plan 16 §7.2 / task_16_04).
    # Solo alcanzable desde `ready` y SOLO para tareas cuyo agente asignado
    # es humano — el orchestrator (task_16_05) la fija al rutear una tarea
    # humana en lugar de pedir contenedor. Desde aquí: `in_progress` (el
    # humano acepta), `assigned_to_human` (reasignación) o `blocked`
    # (escalación agotada, task_16_06).
    ASSIGNED_TO_HUMAN = "assigned_to_human"
    IN_PROGRESS = "in_progress"
    # La tarea está aparcada esperando una decisión humana sobre una
    # acción sensible (ADR 0020). El agente queda libre; al aprobar la
    # tarea vuelve a `backlog`; al rechazar pasa a `blocked`.
    AWAITING_HUMAN_APPROVAL = "awaiting_human_approval"
    IN_REVIEW = "in_review"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


class TaskPriority(enum.StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TaskComplexity(enum.StrEnum):
    XS = "xs"
    S = "s"
    M = "m"
    L = "l"
    XL = "xl"


class ExecutionStatus(enum.StrEnum):
    RUNNING = "running"
    DONE = "done"
    ABORTED = "aborted"
    FAILED = "failed"
    # Paused mid-run waiting on a human_approval_policy decision (Fase F).
    AWAITING_HUMAN_APPROVAL = "awaiting_human_approval"
    # Stopped by an explicit operator cancel request (POST /executions/{id}/cancel).
    CANCELLED = "cancelled"
    # The AUTHORITATIVE self-review could not certify the output (ADR 0087):
    # inconclusive verdict or exhausted retries. Terminal; the worker maps the
    # TASK to `blocked` and the human inbox surfaces it for validation.
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class DocumentStatus(enum.StrEnum):
    """Lifecycle of a `Document` in a Knowledge Base (Plan 04 task_04_07).

    A freshly uploaded document lands in ``pending``. The ingestion
    worker (Plan 04 Fase C, task_04_11) flips it to ``processing``
    while Docling parses + chunks + embeds, then to ``indexed`` on
    success or ``failed`` on error. The transitions are linear and
    one-way — re-ingestion creates a *new* Document row, never
    rewinds the existing one.
    """

    PENDING = "pending"
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"


class ApprovalRequestStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"
    # The task/plan/project was cancelled while this request was pending — the
    # parked execution is sealed and the request leaves the inbox (CANCELAWAIT).
    # Distinct from REJECTED ("a human said no") for honest audit. Fits the
    # VARCHAR(16) column (9 chars), so no migration is needed.
    CANCELLED = "cancelled"


class HumanTaskAssignmentStatus(enum.StrEnum):
    """Lifecycle of one :class:`HumanTaskAssignment` (Plan 16 task_16_05).

    The assignment row tracks WHO a human task is currently with and where
    that person is in the accept/work cycle — distinct from, but kept in step
    with, the Task's own §7.2 status (an ``assigned_to_human`` Task has a
    ``pending_acceptance`` assignment; the Task moves to ``in_progress`` when
    the assignment moves to ``accepted``).

    - ``PENDING_ACCEPTANCE``: freshly created by the orchestrator (task_16_05)
      — the assigned user has been notified and has up to
      ``acceptance_timeout_hours`` to accept before escalation (task_16_06).
    - ``ACCEPTED``: the user accepted; work has started.
    - ``REASSIGNED``: superseded by a newer assignment (the acceptance-timeout
      escalation hands the task to the ``escalation_target_user_id``,
      task_16_06). The superseding row is a fresh ``pending_acceptance`` one.
    - ``DECLINED``: the user explicitly rejected the task (Fase C inbox).
    - ``EXPIRED``: the acceptance window lapsed with no decision (task_16_06).
    """

    PENDING_ACCEPTANCE = "pending_acceptance"
    ACCEPTED = "accepted"
    REASSIGNED = "reassigned"
    DECLINED = "declined"
    EXPIRED = "expired"
