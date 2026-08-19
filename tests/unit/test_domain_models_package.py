"""El troceo de `db/domain.py` en paquete no puede mover ni una columna.

Plan prod-16, ``task_prod16_11``: «Dividir el modelo ORM en módulos por agregado
manteniendo `db/domain.py` como fachada de re-export para no romper los imports
existentes. Verificar que `alembic` no detecta diferencias de esquema tras el
refactor (autogenerate vacío)».

Este fichero es la red, y se escribió **antes** de mover la primera tabla —igual
que ``test_sso_router_package.py`` (``task_prod16_10``) y
``test_agents_router_package.py`` (``task_prod16_12``)—. Todo lo que hay abajo
está capturado del monolito de **1840 líneas**.

## Por qué aquí la red no puede ser «el conjunto de nombres»

En los dos troceos anteriores lo que se podía perder era una ruta HTTP. Aquí lo
que se puede perder es **una columna**, y eso no lo delata ningún import roto:

- `db/domain.py` lo importan 233 ficheros de cuatro árboles (api-server, workers,
  orchestrator y los tres de tests). Un `ImportError` sale a la primera y es
  ruidoso; **eso no es el riesgo**.
- El riesgo es que al cortar por rangos se caiga un `mapped_column` de en medio
  de una clase, o que un `__table_args__` pierda un `CheckConstraint` porque el
  enum al que apuntaba quedó en otro módulo. El código **sigue importando**, los
  tests que no tocan esa columna **siguen verdes**, y lo que cambia es el
  `CREATE TABLE` que Alembic compara contra la BD. El síntoma llega semanas
  después, en forma de migración autogenerada que propone un `DROP COLUMN`.

Por eso el contrato de abajo es el **DDL compilado** de las 17 tablas del módulo
—`CREATE TABLE` + sus `CREATE INDEX`, dialecto PostgreSQL, normalizado de
espacios y resumido en un digest— más la lista literal de nombres de columna,
que es la parte legible: si algo se cae, el fallo dice qué tabla y con qué
columnas se quedó. Es el equivalente **offline** de «autogenerate vacío»: corre
sin base de datos, en la suite unitaria, en milisegundos.

## Y una guarda contra el atajo

El troceo del panel enseñó que «mover el bulto no es partir»: `page.tsx` bajó de
1125 líneas mudándolas enteras a un solo `mcp-server-sections.tsx`, y la guarda
de tamaño daba OK. El equivalente aquí sería dejar los 17 modelos dentro de
`domain/__init__.py`. `test_the_facade_defines_no_model_itself` lo prohíbe: la
fachada re-exporta, no define.
"""

from __future__ import annotations

import enum
import hashlib
import importlib
import sys
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DB_DIR = _REPO_ROOT / "apps" / "api-server" / "src" / "api_server" / "db"


#: Los 17 modelos que **define** `db/domain.py`, con su `__tablename__`.
#: Capturado del monolito el 2026-08-19.
MODELS_BEFORE_THE_SPLIT: tuple[tuple[str, str], ...] = (
    ("Agent", "agents"),
    ("AgentSkill", "agent_skills"),
    ("AgentTool", "agent_tools"),
    ("ApprovalPolicyTemplate", "approval_policy_templates"),
    ("ApprovalRequest", "approval_requests"),
    ("Execution", "executions"),
    ("HumanAgentConfig", "human_agent_config"),
    ("HumanTaskAssignment", "human_task_assignments"),
    ("HumanWorkSession", "human_work_sessions"),
    ("Plan", "plans"),
    ("Project", "projects"),
    ("Skill", "skills"),
    ("Task", "tasks"),
    ("TaskDependency", "task_dependencies"),
    ("Team", "teams"),
    ("TeamMember", "team_members"),
    ("Tool", "tools"),
)

#: Los dos modelos que `domain` NO define pero SÍ re-exporta desde
#: `db/conversation.py` (Plan 03), «para que los `from api_server.db import
#: domain as d` existentes sigan encontrando todo el dominio en un sitio».
#: El troceo no puede perder ese re-export.
REEXPORTED_MODELS: tuple[tuple[str, str], ...] = (
    ("Conversation", "conversations"),
    ("Message", "messages"),
)

#: Los 24 enums visibles desde `domain`, con sus valores. Los valores son
#: **contrato de base de datos**: los `CHECK` de varias migraciones (p.ej.
#: `0101_tasks_status_check`) están derivados de ellos, y hay filas históricas
#: con esos strings. Capturado del monolito el 2026-08-19.
ENUM_VALUES_BEFORE_THE_SPLIT: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "AgentRole",
        (
            "project_manager",
            "architect",
            "backend_dev",
            "frontend_dev",
            "qa",
            "reviewer",
            "leader",
            "worker",
            "specialist",
            "researcher",
            "devops",
            "security",
            "technical_writer",
            "custom",
        ),
    ),
    ("AgentScope", ("global_builtin", "global_tenant_template", "project_local")),
    ("AgentSkillProficiency", ("basic", "standard", "expert")),
    ("AgentType", ("ai", "human")),
    ("ApprovalRequestStatus", ("pending", "approved", "rejected", "timed_out", "cancelled")),
    ("AssignmentMode", ("specific_user", "role_queue", "team_pool")),
    ("BudgetPeriod", ("weekly", "monthly", "quarterly", "yearly", "custom")),
    ("ChatMode", ("planning", "discussion", "execution", "custom")),
    ("DocumentStatus", ("pending", "processing", "indexed", "failed")),
    (
        "ExecutionStatus",
        (
            "running",
            "done",
            "aborted",
            "failed",
            "awaiting_human_approval",
            "cancelled",
            "needs_human_review",
        ),
    ),
    (
        "HumanTaskAssignmentStatus",
        ("pending_acceptance", "accepted", "reassigned", "declined", "expired"),
    ),
    ("HumanTaskReviewMode", ("auto_approve", "peer_human_reviewer")),
    ("MemoryScope", ("private", "team_shared", "project_shared", "global")),
    ("MemoryType", ("episodic", "semantic")),
    ("MessageAuthorKind", ("user", "agent", "system")),
    (
        "PlanStatus",
        (
            "pending_approval",
            "pending_second_approval",
            "draft",
            "approved",
            "in_progress",
            "blocked",
            "pending_human_validation",
            "completed",
            "cancelled",
            "rejected",
            "archived",
        ),
    ),
    ("ProjectStatus", ("active", "paused", "archived")),
    ("SkillCategory", ("backend", "frontend", "devops", "qa", "research", "docs", "atlassian")),
    ("TaskComplexity", ("xs", "s", "m", "l", "xl")),
    ("TaskPriority", ("low", "medium", "high", "critical")),
    (
        "TaskStatus",
        (
            "backlog",
            "ready",
            "assigned_to_human",
            "in_progress",
            "awaiting_human_approval",
            "in_review",
            "blocked",
            "done",
            "cancelled",
        ),
    ),
    (
        "ToolCategory",
        (
            "file",
            "runtime",
            "git",
            "network",
            "knowledge",
            "notification",
            "command",
            "mcp",
            "orchestration",
            "custom",
        ),
    ),
    (
        "ToolImplementationType",
        ("builtin", "python_function", "http_endpoint", "mcp_tool", "docker_command"),
    ),
    ("ToolSecurityLevel", ("safe", "sandboxed", "privileged")),
)

#: `__all__` literal del monolito (41 nombres). Es la lista que gobierna
#: `from api_server.db.domain import *`.
PUBLIC_API_BEFORE_THE_SPLIT: tuple[str, ...] = (
    "Agent",
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
)

#: El DDL de cada tabla, capturado del monolito el 2026-08-19: digest del
#: `CREATE TABLE` + sus `CREATE INDEX` compilados contra el dialecto PostgreSQL
#: (espacios normalizados), y la lista literal de columnas.
#:
#: El digest cubre lo que la lista de nombres no ve: tipo, nulabilidad,
#: `server_default`, `ForeignKey`, `CheckConstraint`, `UniqueConstraint`,
#: `PrimaryKeyConstraint` y los índices con sus `postgresql_where`. La lista de
#: nombres está para que el fallo sea legible.
DDL_BEFORE_THE_SPLIT: dict[str, tuple[str, tuple[str, ...]]] = {
    "agents": (
        "de8dfea7867f89f4",
        (
            "agent_type",
            "anchored_version",
            "avatar_url",
            "created_at",
            "deleted_at",
            "description",
            "forked_from_agent_id",
            "forked_from_version",
            "id",
            "is_template",
            "max_concurrent_tasks",
            "memory_scope",
            "model_config",
            "name",
            "project_id",
            "review_capability",
            "role",
            "scope",
            "system_prompt",
            "tenant_id",
            "updated_at",
        ),
    ),
    "agent_skills": (
        "b0f2c46ae7fc2111",
        ("agent_id", "created_at", "proficiency", "skill_id", "tenant_id", "updated_at"),
    ),
    "agent_tools": (
        "4175f33aa6719077",
        ("agent_id", "config_override", "created_at", "tenant_id", "tool_id", "updated_at"),
    ),
    "approval_policy_templates": (
        "93baa1ea86351f70",
        (
            "categories",
            "created_at",
            "deleted_at",
            "description",
            "id",
            "is_builtin",
            "name",
            "tenant_id",
            "updated_at",
        ),
    ),
    "approval_requests": (
        "597be246b24e9b8f",
        (
            "action",
            "category",
            "created_at",
            "execution_id",
            "id",
            "project_id",
            "reason",
            "requested_at",
            "resolved_at",
            "resolved_by",
            "status",
            "task_id",
            "tenant_id",
            "updated_at",
        ),
    ),
    "executions": (
        "e88fb406e3a56984",
        (
            "abort_code",
            "agent_id",
            "cancel_requested_at",
            "celery_task_id",
            "completed_at",
            "container_launched_at",
            "created_at",
            "finish_status",
            "id",
            "iterations",
            "last_model",
            "memorize_skip_reason",
            "model_call_count",
            "output",
            "pending_guidance",
            "price_cached_input_usd",
            "price_input_usd",
            "price_output_usd",
            "price_snapshot_at",
            "price_snapshot_cost_usd",
            "price_snapshot_currency",
            "prompt_version",
            "runtime_image_digest",
            "started_at",
            "status",
            "steps_log",
            "task_id",
            "tenant_id",
            "tokens_in",
            "tokens_out",
            "tool_call_count",
            "total_cost_usd",
            "total_tokens",
            "updated_at",
        ),
    ),
    "human_agent_config": (
        "d396b6154b3e60c4",
        (
            "acceptance_timeout_hours",
            "agent_id",
            "assigned_user_id",
            "assignment_mode",
            "created_at",
            "escalation_target_user_id",
            "expected_execution_time_hours",
            "expected_response_time_hours",
            "hourly_rate",
            "hourly_rate_currency",
            "id",
            "notification_channels",
            "tenant_id",
            "updated_at",
        ),
    ),
    "human_task_assignments": (
        "cccecd2d2fe1b1df",
        (
            "assigned_at",
            "assigned_to_user_id",
            "created_at",
            "human_agent_id",
            "id",
            "status",
            "task_id",
            "tenant_id",
            "updated_at",
        ),
    ),
    "human_work_sessions": (
        "3f796818687c8c8b",
        (
            "comments",
            "created_at",
            "end_at",
            "hours_logged",
            "id",
            "output_files_attached",
            "start_at",
            "task_id",
            "tenant_id",
            "updated_at",
            "user_id",
        ),
    ),
    "plans": (
        "90dd892590c9b168",
        (
            "approved_at",
            "approved_by",
            "conversation_id",
            "created_at",
            "created_by",
            "deleted_at",
            "description",
            "first_approved_at",
            "first_approved_by",
            "id",
            "pr_branch",
            "pr_error",
            "pr_url",
            "project_id",
            "slug",
            "specification",
            "status",
            "tenant_id",
            "title",
            "updated_at",
        ),
    ),
    "projects": (
        "c372f30096c94446",
        (
            "allowed_commands",
            "allowed_domains",
            "budget_amount",
            "budget_currency",
            "budget_includes_human_cost",
            "budget_period",
            "budget_period_length_days",
            "budget_period_start_day",
            "chat_model_config",
            "created_at",
            "default_kb_grants",
            "default_runtime_template",
            "deleted_at",
            "description",
            "execution_budgets",
            "git_config",
            "guardrails_config",
            "human_approval_policy",
            "human_task_review_mode",
            "id",
            "is_template",
            "mcp_servers",
            "mcp_tool_roles",
            "model_config",
            "name",
            "paused_by_budget",
            "rag_knowledge_bases",
            "repository_config",
            "secrets_vault_id",
            "slug",
            "status",
            "team_id",
            "tenant_id",
            "updated_at",
            "worker_config",
        ),
    ),
    "skills": (
        "02fb2f86b2f74201",
        (
            "category",
            "created_at",
            "deleted_at",
            "description",
            "id",
            "is_builtin",
            "name",
            "prompt_fragment",
            "required_tools",
            "source_installation_id",
            "source_listing_id",
            "source_version",
            "tenant_id",
            "updated_at",
        ),
    ),
    "tasks": (
        "399389c271c05b23",
        (
            "acceptance_criteria",
            "assigned_agent_id",
            "completed_at",
            "created_at",
            "description",
            "estimated_complexity",
            "id",
            "inputs",
            "max_retries",
            "plan_id",
            "priority",
            "project_id",
            "retry_count",
            "reviewer_agent_id",
            "started_at",
            "status",
            "tenant_id",
            "title",
            "updated_at",
        ),
    ),
    "task_dependencies": (
        "8a56ef40ef65a783",
        ("depends_on_task_id", "task_id", "tenant_id"),
    ),
    "teams": (
        "8db2c2d5be192c8d",
        (
            "chat_model_config",
            "created_at",
            "default_workflow_template_id",
            "deleted_at",
            "description",
            "forked_from_team_id",
            "forked_from_version",
            "id",
            "is_builtin",
            "memory_scope",
            "model_config",
            "name",
            "tenant_id",
            "updated_at",
        ),
    ),
    "team_members": (
        "eb5a73ff6902e79f",
        (
            "agent_id",
            "assignment_priority",
            "created_at",
            "is_team_leader",
            "role_in_team",
            "team_id",
            "tenant_id",
            "updated_at",
        ),
    ),
    "tools": (
        "5cd4555d02316537",
        (
            "category",
            "created_at",
            "deleted_at",
            "description",
            "id",
            "implementation_ref",
            "implementation_type",
            "input_schema",
            "is_builtin",
            "name",
            "output_schema",
            "rate_limit_per_minute",
            "security_level",
            "source_installation_id",
            "source_listing_id",
            "source_version",
            "tenant_id",
            "timeout_seconds",
            "updated_at",
        ),
    ),
}


def _load_domain() -> object:
    """Importa `db.domain` con TODO `db` cargado.

    Las FK de `approval_requests` apuntan a `users`, que vive en `db/models.py`.
    Compilar el DDL de una tabla con una FK cuyo destino no está registrado en
    `Base.metadata` levanta `NoReferencedTableError` — un fallo que NO habla del
    troceo. Cargar el paquete entero es lo mismo que hace el Alembic env.
    """
    import pkgutil

    import api_server.db as db_pkg

    for module in pkgutil.iter_modules(db_pkg.__path__):
        importlib.import_module(f"api_server.db.{module.name}")
    return importlib.import_module("api_server.db.domain")


def _table_ddl(table: object) -> str:
    dialect = postgresql.dialect()
    ddl = str(CreateTable(table).compile(dialect=dialect))  # type: ignore[arg-type]
    for index in sorted(table.indexes, key=lambda i: i.name or ""):  # type: ignore[attr-defined]
        ddl += "\n" + str(CreateIndex(index).compile(dialect=dialect))
    return ddl


def _digest(ddl: str) -> str:
    return hashlib.sha256(" ".join(ddl.split()).encode()).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# 1. La fachada sigue sirviendo todo lo que servía
# --------------------------------------------------------------------------- #
def test_every_model_is_still_importable_from_domain() -> None:
    """Los 19 modelos (17 propios + 2 re-exportados) siguen colgando de `domain`.

    233 ficheros del repo importan de aquí. Cualquiera que se caiga es un
    `ImportError` en arranque, así que este test es el barato: falla primero y
    dice el nombre.
    """
    domain = _load_domain()
    missing = [
        name
        for name, _ in (*MODELS_BEFORE_THE_SPLIT, *REEXPORTED_MODELS)
        if not hasattr(domain, name)
    ]
    assert not missing, f"la fachada dejó de re-exportar: {missing}"

    wrong_table = [
        (name, getattr(domain, name).__tablename__, expected)
        for name, expected in (*MODELS_BEFORE_THE_SPLIT, *REEXPORTED_MODELS)
        if getattr(domain, name).__tablename__ != expected
    ]
    assert not wrong_table, f"cambió el __tablename__: {wrong_table}"


def test_every_enum_keeps_its_values() -> None:
    """Los valores de los enums son contrato de BD, no detalle de Python.

    Varias migraciones derivan sus `CHECK` de estas listas y hay filas
    históricas persistidas con esos strings exactos. Reordenar `TaskStatus` no
    rompe ningún import y sí rompe el `CHECK` de `0101_tasks_status_check`.
    """
    domain = _load_domain()
    seen = 0
    problems: list[str] = []
    for name, expected in ENUM_VALUES_BEFORE_THE_SPLIT:
        member = getattr(domain, name, None)
        if member is None:
            problems.append(f"{name}: desapareció de la fachada")
            continue
        assert isinstance(member, type) and issubclass(member, enum.Enum)
        actual = tuple(m.value for m in member)
        seen += 1
        if actual != expected:
            problems.append(f"{name}: {expected} -> {actual}")
    assert not problems, "los enums cambiaron: " + "; ".join(problems)
    assert seen == len(ENUM_VALUES_BEFORE_THE_SPLIT), (
        f"la guarda sólo comprobó {seen} enums de {len(ENUM_VALUES_BEFORE_THE_SPLIT)}"
    )


def test_the_public_all_list_is_unchanged() -> None:
    """`__all__` gobierna `from … import *`; el troceo no lo toca."""
    domain = _load_domain()
    assert tuple(domain.__all__) == PUBLIC_API_BEFORE_THE_SPLIT  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# 2. Lo que de verdad se puede perder: una columna
# --------------------------------------------------------------------------- #
def test_no_table_changed_its_ddl() -> None:
    """El `CREATE TABLE` compilado de las 17 tablas es idéntico al del monolito.

    Es el «autogenerate vacío» que pide el enunciado, pero offline: si algo se
    cayó al cortar —una columna, un `CheckConstraint`, un `server_default`, el
    `postgresql_where` de un índice parcial— el digest cambia aquí, en la suite
    unitaria, en vez de aparecer semanas después en una migración autogenerada
    que propone un `DROP COLUMN`.
    """
    domain = _load_domain()
    tables = {table: getattr(domain, model).__table__ for model, table in MODELS_BEFORE_THE_SPLIT}

    problems: list[str] = []
    for name, (expected_digest, expected_columns) in DDL_BEFORE_THE_SPLIT.items():
        table = tables[name]
        actual_columns = tuple(sorted(column.name for column in table.columns))
        if actual_columns != expected_columns:
            lost = sorted(set(expected_columns) - set(actual_columns))
            gained = sorted(set(actual_columns) - set(expected_columns))
            problems.append(f"{name}: columnas perdidas={lost} nuevas={gained}")
            continue
        actual_digest = _digest(_table_ddl(table))
        if actual_digest != expected_digest:
            problems.append(
                f"{name}: mismas columnas pero DDL distinto "
                f"({expected_digest} -> {actual_digest}). El DDL actual es:\n{_table_ddl(table)}"
            )
    assert not problems, "el esquema se movió con el troceo:\n" + "\n".join(problems)
    assert len(DDL_BEFORE_THE_SPLIT) == len(MODELS_BEFORE_THE_SPLIT), (
        "el inventario de DDL dejó de cubrir todos los modelos"
    )


# --------------------------------------------------------------------------- #
# 3. Que sea un troceo de verdad, y no un cambio de nombre de fichero
# --------------------------------------------------------------------------- #
def test_domain_is_a_package_with_one_module_per_aggregate() -> None:
    """`db/domain` es un paquete con un módulo por agregado, no un fichero.

    Éste es el test que estaba ROJO antes de `task_prod16_11` («el paquete no
    existe»), exactamente igual que en los dos troceos anteriores.
    """
    domain = _load_domain()
    assert hasattr(domain, "__path__"), (
        "api_server.db.domain sigue siendo un módulo suelto: task_prod16_11 sin hacer"
    )
    submodules = sorted(
        path.stem
        for path in (_DB_DIR / "domain").glob("*.py")
        if path.stem != "__init__" and not path.stem.startswith("_")
    )
    assert len(submodules) >= 5, f"un paquete de {len(submodules)} módulo(s) no es un troceo"


def test_the_facade_imports_every_submodule() -> None:
    """Un submódulo que la fachada no importa es una tabla fuera de la metadata.

    Es el modo de fallo silencioso de este paquete: los mapeadores se registran
    en `Base.metadata` **al importarse el módulo**. Si alguien añade
    `domain/foo.py` y no lo cuelga del `__init__`, sus tablas desaparecen de la
    metadata; nada se rompe, y el siguiente `alembic revision --autogenerate`
    propone borrarlas.
    """
    _load_domain()
    package_dir = _DB_DIR / "domain"
    if not package_dir.is_dir():  # pragma: no cover - lo cubre el test de arriba
        pytest.fail("api_server.db.domain no es un paquete: task_prod16_11 sin hacer")

    expected = sorted(
        path.stem
        for path in package_dir.glob("*.py")
        if path.stem != "__init__" and not path.stem.startswith("_")
    )
    not_imported = [name for name in expected if f"api_server.db.domain.{name}" not in sys.modules]
    assert not not_imported, (
        f"la fachada no importa {not_imported}: sus tablas no llegan a Base.metadata"
    )
    assert len(expected) >= 5, "la guarda dejó de encontrar submódulos"


def test_the_facade_defines_no_model_itself() -> None:
    """La fachada re-exporta; no define.

    «Mover el bulto no es partir»: el panel ya pagó esa lección con
    `mcp-server-sections.tsx`, 1125 líneas mudadas de fichero con la guarda
    dando OK. Aquí el atajo equivalente es dejar los 17 modelos dentro de
    `domain/__init__.py`.
    """
    domain = _load_domain()
    if not hasattr(domain, "__path__"):  # pragma: no cover - lo cubre el test de arriba
        pytest.fail("api_server.db.domain no es un paquete: task_prod16_11 sin hacer")

    defined_in_facade = [
        name
        for name, _ in MODELS_BEFORE_THE_SPLIT
        if getattr(domain, name).__module__ == "api_server.db.domain"
    ]
    assert not defined_in_facade, (
        f"estos modelos siguen definidos en el __init__: {defined_in_facade}"
    )
