"""Built-in tool catalog (task_01_11).

Eighteen tool definitions covering file ops, git, code runtime, HTTP
and notifications. NONE of these are executable in Plan 01 -- the
platform records the metadata and rejects invocations with 501 Not
Implemented until Plan 02 wires the workers + runtime templates.

`implementation_type` choices reflect the eventual execution path:

  builtin           handled by api-server or orchestrator natively.
  python_function   a Python callable in api_server.tools.* (Plan 02).
  http_endpoint     proxied call; URL travels in inputs.
  docker_command    runs inside a runtime-template container.

`security_level` is conservative: anything that mutates state defaults
to `sandboxed`; pure reads are `safe`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.seeds import PLATFORM_TENANT_ID, TOOL_SEED_NAMESPACE


def _tool_id(slug: str) -> UUID:
    return uuid5(TOOL_SEED_NAMESPACE, f"tool:{slug}")


@dataclass(frozen=True)
class BuiltinTool:
    slug: str
    name: str
    description: str
    category: str
    implementation_type: str
    security_level: str
    timeout_seconds: int
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    implementation_ref: str | None = None
    rate_limit_per_minute: int | None = None

    @property
    def id(self) -> UUID:
        return _tool_id(self.slug)


def _obj(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


# ---------------------------------------------------------------------------
# Catalog -- 18 tools
# ---------------------------------------------------------------------------
BUILTIN_TOOLS: tuple[BuiltinTool, ...] = (
    # ----- File / Project -----
    BuiltinTool(
        "read-file",
        "read_file",
        "Lee un archivo del repo del proyecto y devuelve su contenido.",
        "file",
        "builtin",
        "safe",
        10,
        _obj({"path": {"type": "string", "description": "Ruta relativa al repo root."}}, ["path"]),
        _obj({"content": {"type": "string"}, "size_bytes": {"type": "integer"}}, ["content"]),
    ),
    BuiltinTool(
        "write-file",
        "write_file",
        "Escribe (o sobreescribe) un archivo. Sandboxed: solo bajo el worktree de la tarea.",
        "file",
        "builtin",
        "sandboxed",
        10,
        _obj(
            {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            ["path", "content"],
        ),
        _obj({"bytes_written": {"type": "integer"}}, ["bytes_written"]),
    ),
    BuiltinTool(
        "apply-patch",
        "apply_patch",
        "Aplica un patch en formato unified diff sobre el worktree de la tarea.",
        "file",
        "builtin",
        "sandboxed",
        30,
        _obj({"diff": {"type": "string", "description": "Unified diff."}}, ["diff"]),
        _obj({"applied_files": {"type": "array", "items": {"type": "string"}}}, ["applied_files"]),
    ),
    BuiltinTool(
        "list-files",
        "list_files",
        "Lista archivos por patrón glob bajo una ruta.",
        "file",
        "builtin",
        "safe",
        5,
        _obj(
            {
                "path": {"type": "string", "default": "."},
                "pattern": {"type": "string", "default": "**/*"},
            }
        ),
        _obj({"files": {"type": "array", "items": {"type": "string"}}}, ["files"]),
    ),
    BuiltinTool(
        "search-code",
        "search_code",
        "Busca texto/regex en el código (estilo grep). Devuelve coincidencias con contexto.",
        "file",
        "builtin",
        "safe",
        10,
        _obj(
            {
                "query": {"type": "string"},
                "path": {"type": "string", "default": "."},
                "regex": {"type": "boolean", "default": False},
            },
            ["query"],
        ),
        _obj(
            {
                "matches": {
                    "type": "array",
                    "items": _obj(
                        {
                            "file": {"type": "string"},
                            "line": {"type": "integer"},
                            "text": {"type": "string"},
                        }
                    ),
                }
            },
            ["matches"],
        ),
    ),
    # ----- Code runtime -----
    BuiltinTool(
        "run-pytest",
        "run_pytest",
        "Ejecuta pytest dentro del runtime python-pytest. Devuelve summary + output.",
        "runtime",
        "docker_command",
        "sandboxed",
        600,
        _obj(
            {
                "path": {"type": "string", "default": "tests/"},
                "args": {"type": "array", "items": {"type": "string"}, "default": []},
            }
        ),
        _obj(
            {
                "exit_code": {"type": "integer"},
                "passed": {"type": "integer"},
                "failed": {"type": "integer"},
                "stdout": {"type": "string"},
            },
            ["exit_code"],
        ),
        implementation_ref="python-pytest",
    ),
    BuiltinTool(
        "run-lint",
        "run_lint",
        "Corre el linter del proyecto (ruff/eslint según stack).",
        "runtime",
        "docker_command",
        "sandboxed",
        120,
        _obj({"path": {"type": "string", "default": "."}}),
        _obj({"exit_code": {"type": "integer"}, "issues": {"type": "array"}}, ["exit_code"]),
    ),
    BuiltinTool(
        "run-typecheck",
        "run_typecheck",
        "Ejecuta el type checker (mypy / tsc / pyright según stack).",
        "runtime",
        "docker_command",
        "sandboxed",
        180,
        _obj({"path": {"type": "string", "default": "."}}),
        _obj({"exit_code": {"type": "integer"}, "errors": {"type": "array"}}, ["exit_code"]),
    ),
    BuiltinTool(
        "run-build",
        "run_build",
        "Ejecuta el build del proyecto (npm build / cargo build / ...).",
        "runtime",
        "docker_command",
        "sandboxed",
        600,
        _obj(
            {
                "target": {"type": "string", "default": "default"},
                "release": {"type": "boolean", "default": False},
            }
        ),
        _obj(
            {
                "exit_code": {"type": "integer"},
                "artifacts": {"type": "array", "items": {"type": "string"}},
            },
            ["exit_code"],
        ),
    ),
    # ----- Git -----
    BuiltinTool(
        "git-status",
        "git_status",
        "Muestra el estado del worktree (modified, staged, untracked).",
        "git",
        "builtin",
        "safe",
        10,
        _obj({}),
        _obj({"clean": {"type": "boolean"}, "files": {"type": "array"}}, ["clean", "files"]),
    ),
    BuiltinTool(
        "git-diff",
        "git_diff",
        "Muestra el diff actual (staged + unstaged) en formato unified.",
        "git",
        "builtin",
        "safe",
        30,
        _obj(
            {
                "staged_only": {"type": "boolean", "default": False},
                "path": {"type": "string"},
            }
        ),
        _obj({"diff": {"type": "string"}}, ["diff"]),
    ),
    BuiltinTool(
        "git-commit",
        "git_commit",
        "Stage + commit. Mensaje obligatorio. Trailers Plan-Id/Task-Id/Execution-Id "
        "los inyecta el sistema automáticamente.",
        "git",
        "builtin",
        "sandboxed",
        30,
        _obj(
            {
                "message": {"type": "string"},
                "files": {"type": "array", "items": {"type": "string"}, "default": []},
            },
            ["message"],
        ),
        _obj({"commit_sha": {"type": "string"}}, ["commit_sha"]),
    ),
    BuiltinTool(
        "git-log",
        "git_log",
        "Lee el log reciente (default: últimos 20 commits) del worktree actual.",
        "git",
        "builtin",
        "safe",
        10,
        _obj({"limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 200}}),
        _obj({"commits": {"type": "array"}}, ["commits"]),
    ),
    # ----- HTTP / Web -----
    BuiltinTool(
        "http-get",
        "http_get",
        "GET HTTP genérico. Restringido por allowed_networks del proyecto.",
        "network",
        "http_endpoint",
        "sandboxed",
        30,
        _obj(
            {
                "url": {"type": "string", "format": "uri"},
                "headers": {"type": "object"},
            },
            ["url"],
        ),
        _obj(
            {
                "status_code": {"type": "integer"},
                "body": {"type": "string"},
                "headers": {"type": "object"},
            },
            ["status_code", "body"],
        ),
        rate_limit_per_minute=60,
    ),
    BuiltinTool(
        "http-post",
        "http_post",
        "POST JSON. Sujeto a allowed_networks y human_approval_policy del proyecto.",
        "network",
        "http_endpoint",
        "sandboxed",
        30,
        _obj(
            {
                "url": {"type": "string", "format": "uri"},
                "body": {"type": "object"},
                "headers": {"type": "object"},
            },
            ["url"],
        ),
        _obj(
            {"status_code": {"type": "integer"}, "body": {"type": "string"}},
            ["status_code", "body"],
        ),
        rate_limit_per_minute=30,
    ),
    # ----- Knowledge / LLM -----
    BuiltinTool(
        "semantic-search",
        "semantic_search",
        "Búsqueda semántica (pgvector) en las knowledge bases del proyecto.",
        "knowledge",
        "builtin",
        "safe",
        15,
        _obj(
            {
                "query": {"type": "string"},
                "knowledge_base_id": {"type": "string", "format": "uuid"},
                "top_k": {"type": "integer", "default": 5, "minimum": 1, "maximum": 50},
            },
            ["query", "knowledge_base_id"],
        ),
        _obj(
            {
                "results": {
                    "type": "array",
                    "items": _obj(
                        {
                            "document_id": {"type": "string"},
                            "score": {"type": "number"},
                            "snippet": {"type": "string"},
                        }
                    ),
                }
            },
            ["results"],
        ),
    ),
    BuiltinTool(
        "summarize-text",
        "summarize_text",
        "Resume un texto largo a una longitud objetivo (palabras).",
        "knowledge",
        "builtin",
        "safe",
        60,
        _obj(
            {
                "text": {"type": "string"},
                "target_words": {"type": "integer", "default": 200, "minimum": 50, "maximum": 2000},
            },
            ["text"],
        ),
        _obj({"summary": {"type": "string"}, "word_count": {"type": "integer"}}, ["summary"]),
    ),
    # ----- Notifications -----
    BuiltinTool(
        "send-notification",
        "send_notification",
        "Envía una notificación al asistente personal del usuario o por email.",
        "notification",
        "python_function",
        "sandboxed",
        15,
        _obj(
            {
                "recipient_user_id": {"type": "string", "format": "uuid"},
                "channel": {"type": "string", "enum": ["assistant", "email"]},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            ["recipient_user_id", "channel", "subject", "body"],
        ),
        _obj({"sent": {"type": "boolean"}, "channel": {"type": "string"}}, ["sent", "channel"]),
        implementation_ref="api_server.tools.send_notification",
        rate_limit_per_minute=20,
    ),
)


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------
_UPSERT_SQL = text(
    """
    INSERT INTO tools (
        id, tenant_id, name, description, category,
        input_schema, output_schema, implementation_type,
        implementation_ref, security_level,
        timeout_seconds, rate_limit_per_minute, is_builtin
    )
    VALUES (
        :id, :tenant_id, :name, :description, :category,
        CAST(:input_schema AS jsonb), CAST(:output_schema AS jsonb),
        :implementation_type, :implementation_ref, :security_level,
        :timeout_seconds, :rate_limit_per_minute, true
    )
    ON CONFLICT (id) DO UPDATE SET
        name = EXCLUDED.name,
        description = EXCLUDED.description,
        category = EXCLUDED.category,
        input_schema = EXCLUDED.input_schema,
        output_schema = EXCLUDED.output_schema,
        implementation_type = EXCLUDED.implementation_type,
        implementation_ref = EXCLUDED.implementation_ref,
        security_level = EXCLUDED.security_level,
        timeout_seconds = EXCLUDED.timeout_seconds,
        rate_limit_per_minute = EXCLUDED.rate_limit_per_minute,
        updated_at = now()
    """
)


async def seed_builtin_tools(session: AsyncSession) -> int:
    for tool in BUILTIN_TOOLS:
        await session.execute(
            _UPSERT_SQL,
            {
                "id": str(tool.id),
                "tenant_id": str(PLATFORM_TENANT_ID),
                "name": tool.name,
                "description": tool.description,
                "category": tool.category,
                "input_schema": json.dumps(tool.input_schema),
                "output_schema": json.dumps(tool.output_schema),
                "implementation_type": tool.implementation_type,
                "implementation_ref": tool.implementation_ref,
                "security_level": tool.security_level,
                "timeout_seconds": tool.timeout_seconds,
                "rate_limit_per_minute": tool.rate_limit_per_minute,
            },
        )
    return len(BUILTIN_TOOLS)
