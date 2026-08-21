"""Phase 1 domain models — agents, skills, tools, teams, projects, plans, tasks.

Eleven entities derived from spec §3.1 and §4.2:

  Agent              reusable template; AI or human, with prompt + tools.
  Skill              declarative capability (prompt fragment, no exec code).
  Tool               executable function; type/security_level/schemas.
  AgentSkill         M:N junction with proficiency.
  AgentTool          M:N junction with per-agent config_override.
  Team               group of agents with optional default workflow.
  TeamMember         M:N junction with role-in-team + leader flag.
  Project            work container; budget, repository, approval policy.
  Plan               materialized output of a planning session.
  Task               unit of work; flows through the Kanban statuses.
  TaskDependency     M:N self-join expressing "depends-on".

Notes:
  - tenant_id (via TenantScopedMixin) is the multi-tenancy boundary;
    RLS policies on every table enforce isolation.
  - agent_type, scope and the linked-vs-forked fields arrive in task_01_03;
    this file ships the base shape per spec §4.2.1.
  - JSON-shaped columns (model_config, mcp_servers, ...) use JSONB so
    we can index into them later without migrations.

## Por qué esto es un paquete (plan prod-16, `task_prod16_11`)

Era un solo `db/domain.py` de **1840 líneas** (1506 cuando se escribió el plan;
crece con cada migración) con nueve agregados dentro. Repartido:

  * :mod:`.enums`       — los 22 vocabularios cerrados. **Sin tablas.**
  * :mod:`.agents`      — Agent, Skill, Tool y las dos junctions M:N.
  * :mod:`.teams`       — Team y TeamMember.
  * :mod:`.projects`    — Project, la tabla ancha (35 columnas).
  * :mod:`.plans_tasks` — Plan, Task y las aristas del DAG.
  * :mod:`.approvals`   — ApprovalPolicyTemplate y ApprovalRequest.
  * :mod:`.executions`  — Execution, la tabla pesada (particionada, ADR 0151).
  * :mod:`.humans`      — los tres modelos de agente humano del Plan 16.

**Este módulo es una fachada: re-exporta, no define.** El enunciado lo pedía
así —«manteniendo `db/domain.py` como fachada de re-export para no romper los
imports existentes»— porque 233 ficheros de cuatro árboles (api-server, workers,
orchestrator y los tres de tests) importan de aquí, unos por nombre
(`from api_server.db.domain import Task`) y otros por módulo
(`from api_server.db import domain as d`; `d.Task`). Las dos formas siguen
funcionando exactamente igual.

**Los imports de abajo NO son adorno y no se pueden podar.** Un mapeador de
SQLAlchemy se registra en ``Base.metadata`` **al importarse su módulo**. Un
submódulo que la fachada no importe deja sus tablas fuera de la metadata; nada
se rompe en el arranque, y el siguiente ``alembic revision --autogenerate``
propone borrarlas. `tests/unit/test_domain_models_package.py::
test_the_facade_imports_every_submodule` recorre el directorio y exige que
todos estén importados, precisamente para que añadir un agregado nuevo y
olvidar la línea sea un rojo y no una migración destructiva.
"""

from __future__ import annotations

# Plan 03: Conversation/Message live in their own module but are re-exported
# from `domain` so existing `from api_server.db import domain as d` callers
# keep finding the whole multi-table domain in one place.
from api_server.db.conversation import (  # (re-export)
    ChatMode,
    Conversation,
    Message,
    MessageAuthorKind,
)
from api_server.db.domain.agents import (
    Agent,
    AgentPromptVersion,
    AgentSkill,
    AgentTool,
    Skill,
    Tool,
)
from api_server.db.domain.approvals import ApprovalPolicyTemplate, ApprovalRequest
from api_server.db.domain.enums import (
    AgentRole,
    AgentScope,
    AgentSkillProficiency,
    AgentType,
    ApprovalRequestStatus,
    AssignmentMode,
    BudgetPeriod,
    ExecutionStatus,
    HumanTaskAssignmentStatus,
    HumanTaskReviewMode,
    MemoryScope,
    PlanStatus,
    ProjectStatus,
    SkillCategory,
    TaskComplexity,
    TaskPriority,
    TaskStatus,
    ToolImplementationType,
    ToolSecurityLevel,
)

# Los tres de abajo son públicos —`ToolCategory` lo importan 4 ficheros,
# `DocumentStatus` y `MemoryType` uno cada uno— pero el monolito NUNCA los puso
# en `__all__`. El alias redundante (`X as X`) es la forma de PEP 484 de decir
# "esto es una re-exportación deliberada", y es lo que hace que ruff no lo lea
# como import muerto sin suprimir la regla.
#
# **No se añaden a `__all__` a propósito**: este troceo es un refactor puro, y
# tocar `__all__` cambiaría lo que ve un `from … import *`. Que falten es una
# incoherencia heredada, no algo que arreglar de paso —arreglarla pide su propio
# cambio, con su propio motivo.
from api_server.db.domain.enums import DocumentStatus as DocumentStatus
from api_server.db.domain.enums import MemoryType as MemoryType
from api_server.db.domain.enums import ToolCategory as ToolCategory
from api_server.db.domain.executions import Execution
from api_server.db.domain.humans import HumanAgentConfig, HumanTaskAssignment, HumanWorkSession
from api_server.db.domain.plans_tasks import Plan, Task, TaskDependency
from api_server.db.domain.projects import Project
from api_server.db.domain.teams import Team, TeamMember

__all__ = [
    "Agent",
    "AgentPromptVersion",
    "AgentRole",
    "AgentScope",
    "AgentSkill",
    "AgentSkillProficiency",
    "AgentTool",
    "AgentType",
    "ApprovalPolicyTemplate",
    "ApprovalRequest",
    "ApprovalRequestStatus",
    "AssignmentMode",
    "BudgetPeriod",
    "ChatMode",
    "Conversation",
    "Execution",
    "ExecutionStatus",
    "HumanAgentConfig",
    "HumanTaskAssignment",
    "HumanTaskAssignmentStatus",
    "HumanTaskReviewMode",
    "HumanWorkSession",
    "MemoryScope",
    "Message",
    "MessageAuthorKind",
    "Plan",
    "PlanStatus",
    "Project",
    "ProjectStatus",
    "Skill",
    "SkillCategory",
    "Task",
    "TaskComplexity",
    "TaskDependency",
    "TaskPriority",
    "TaskStatus",
    "Team",
    "TeamMember",
    "Tool",
    "ToolImplementationType",
    "ToolSecurityLevel",
]
