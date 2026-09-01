"""Built-in tool catalog (task_01_11; shell_exec added task_06_16_02;
git family retired task_06_18_06; delete_file added R6/ADR 0089;
stack_exec added ADR 0093; move_file added 2026-08-31).

Tool definitions covering file ops, HTTP, knowledge, notifications and
two stack commands (shell_exec + stack_exec). The ``git`` family was
removed (ADR 0049): it had no runtime executor, so it could never run.
Each row's ``is_runtime_wired`` (derived in ``ToolResponse``) tells the
operator which of these the agent-runtime can actually execute today.

Este encabezado ya NO dice cuántas son, y es a propósito: decía
«Seventeen» mientras la tupla tenía trece — se quedó atrás cuando los
cuatro ``run_*`` se retiraron (F5, 2026-07-28). Un número en prosa que
ningún test comprueba no envejece con un aviso: envejece mintiendo, y
quien lo lee para orientarse acaba buscando cuatro filas que no existen.
``len(BUILTIN_TOOLS)`` es la respuesta, y los tests de siembra la usan.

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
# Catalog (git family retired task_06_18_06; delete_file added R6;
# stack_exec added ADR 0093; move_file added 2026-08-31)
# ---------------------------------------------------------------------------
BUILTIN_TOOLS: tuple[BuiltinTool, ...] = (
    # ----- File / Project -----
    BuiltinTool(
        "read-file",
        "read_file",
        "Read a file from the project repo and return its contents.",
        "file",
        "builtin",
        "safe",
        10,
        _obj(
            {"path": {"type": "string", "description": "Path relative to the repo root."}},
            ["path"],
        ),
        _obj({"content": {"type": "string"}, "size_bytes": {"type": "integer"}}, ["content"]),
    ),
    BuiltinTool(
        "write-file",
        "write_file",
        "Write (or overwrite) a file. Sandboxed: only under the task's worktree.",
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
        "delete-file",
        "delete_file",
        "Delete a file — or a whole directory with recursive=true — from the task's "
        "worktree. Sandboxed: only under the worktree. Use it to remove stale or "
        "duplicate files from previous attempts, and to drop a dependency directory "
        "(vendor/, node_modules/) or a mis-scaffolded module before redoing it.",
        "file",
        "builtin",
        "sandboxed",
        10,
        _obj(
            {
                "path": {"type": "string", "description": "Path relative to the worktree."},
                "recursive": {
                    "type": "boolean",
                    "description": (
                        "Required to delete a DIRECTORY: removes it with everything "
                        "inside. Refused on the worktree root itself."
                    ),
                },
            },
            ["path"],
        ),
        _obj(
            {
                "deleted": {"type": "boolean"},
                "entries": {
                    "type": "integer",
                    "description": "How many entries the recursive delete removed.",
                },
            },
            ["deleted"],
        ),
    ),
    # ----- move_file (2026-08-31) -----
    # POR QUÉ EXISTE, medido en vivo el mismo día (proyecto «Hello World CI4 v3»,
    # tenant mediapro, modelo gpt-oss:120b): `composer create-project .` exige un
    # directorio COMPLETAMENTE vacío, y en el paso 31 del segundo run el agente
    # llegó SOLO a la solución correcta — instalar en `tmpci/` y mover el
    # resultado a su sitio. No pudo terminarla: la familia `file` era exactamente
    # read/write/delete/list, así que de los TRES pasos de su plan el único
    # ejecutable era el destructivo. Cuatro pasos después borró `app/` entera —
    # 85 ficheros que eran el deliverable ya commiteado de la tarea anterior.
    #
    # El ADR 0163 (esconder el `.git` mientras corre el agente) cubre el PRIMER
    # andamiaje sobre un worktree vacío, y por eso el primer run sí instaló. No
    # cubre un reintento ni un proyecto que ya tiene código: ahí el directorio
    # nunca está vacío, y sin esta tool la única salida que le queda al agente es
    # vaciarlo. Las guardas de `delete_file` le cierran esa puerta —correctamente—
    # y sus propios mensajes de error ya le dicen «run it in a subdirectory and
    # move the result in»: esta fila es lo que convierte ese consejo en algo que
    # se puede ejecutar.
    BuiltinTool(
        "move-file",
        "move_file",
        "Move or rename a file — or a whole directory with everything inside — within the "
        "task's worktree. Sandboxed: source AND destination must stay under the worktree. "
        "Use it when a scaffolder demands an EMPTY directory: run it in a subdirectory "
        "(e.g. 'composer create-project ... ci4tmp'), then move its entries into place ONE "
        "AT A TIME (list_files on that directory gives you the list) and delete the empty "
        "leftover. Also renames a mis-named file or module without rewriting it. Missing "
        "parent directories of the destination are created. Refused: moving onto the "
        "worktree root (that is merging two trees, not one move), and moving or "
        "overwriting a whole top-level directory that is tracked in this branch (it holds "
        "an earlier task's committed work).",
        "file",
        "builtin",
        "sandboxed",
        10,
        _obj(
            {
                "source": {
                    "type": "string",
                    "description": "Path to move, relative to the worktree.",
                },
                "destination": {
                    "type": "string",
                    "description": (
                        "The FULL final path, relative to the worktree — not the folder "
                        "to drop the source into. Missing parent directories are created."
                    ),
                },
                "overwrite": {
                    "type": "boolean",
                    "description": (
                        "Required to replace a destination that ALREADY exists; without "
                        "it the move is refused, so a rename cannot silently bury work."
                    ),
                },
            },
            ["source", "destination"],
        ),
        _obj(
            {
                "moved": {"type": "boolean"},
                "source": {"type": "string", "description": "Resolved source path."},
                "destination": {"type": "string", "description": "Resolved final path."},
                "entries": {
                    "type": "integer",
                    "description": "How many entries the moved directory held.",
                },
                "replaced": {
                    "type": "boolean",
                    "description": "Whether an existing destination was replaced.",
                },
                "replaced_entries": {
                    "type": "integer",
                    "description": "How many entries the replaced directory held.",
                },
            },
            ["moved"],
        ),
    ),
    BuiltinTool(
        "apply-patch",
        "apply_patch",
        "Apply a patch in unified diff format to the task's worktree.",
        "file",
        "builtin",
        "sandboxed",
        30,
        _obj({"diff": {"type": "string", "description": "Unified diff."}}, ["diff"]),
        _obj({"applied_files": {"type": "array", "items": {"type": "string"}}}, ["applied_files"]),
    ),
    # `pattern` y su default se reescribieron el 2026-09-01 y el motivo hay que
    # dejarlo aquí, no sólo en el runtime: esta fila ES lo que el modelo ve, y
    # decía dos cosas falsas a la vez. Anunciaba un filtro que `file_list` nunca
    # leía (hacía `iterdir()`, plano y sin filtrar: 15 llamadas con patrón no
    # trivial en un run real devolvieron todas el mismo listado, sin avisar), y
    # anunciaba `"default": "**/*"`, que CUMPLIDO habría sido peor — la raíz de
    # un CodeIgniter son ~5.000 ficheros de `vendor/`, y la rama de aquel plan
    # llegó a 10.318. Por eso el default efectivo es `*` y la descripción explica
    # la semántica entera: el modelo no tiene otro sitio donde leerla.
    BuiltinTool(
        "list-files",
        "list_files",
        (
            "List entries under a path, filtered by a glob. 'pattern' is relative "
            "to 'path' and is matched against each entry's RELATIVE PATH, so "
            "'app/Config/Routes.php' and 'tests/**/*.php' both work. '*', '?' and "
            "'[...]' never cross '/': only '**' descends, so the default '*' lists "
            "just that one directory - use '**/*.php' to search the whole tree. "
            "Braces list alternatives ('composer.{json,lock}'). Matching is "
            "case-sensitive. At most 150 entries come back; when more match, "
            "'truncated' is true and 'total_matches' says how many there were, so "
            "you can narrow the pattern instead of assuming that is all. A pattern "
            "that cannot be applied is rejected with an error, never ignored."
        ),
        "file",
        "builtin",
        "safe",
        5,
        _obj(
            {
                "path": {
                    "type": "string",
                    "default": ".",
                    "description": "Directory to list, relative to the workspace root.",
                },
                "pattern": {
                    "type": "string",
                    "default": "*",
                    "description": (
                        "Glob relative to 'path', matched against each entry's "
                        "relative path. Only '**' descends into subdirectories."
                    ),
                },
            }
        ),
        _obj(
            {
                "path": {"type": "string", "description": "Directory that was listed."},
                "pattern": {"type": "string", "description": "Glob that was applied."},
                "entries": {
                    "type": "array",
                    "items": _obj(
                        {
                            "name": {
                                "type": "string",
                                "description": "Path of the entry, relative to 'path'.",
                            },
                            "type": {"type": "string", "enum": ["file", "dir"]},
                            "size": {
                                "type": ["integer", "null"],
                                "description": "Size in bytes for files; null for directories.",
                            },
                        },
                        ["name", "type"],
                    ),
                },
                "truncated": {
                    "type": "boolean",
                    "description": (
                        "True when more entries matched than were returned. Always "
                        "present: false is the positive promise that this is all of them."
                    ),
                },
                "total_matches": {
                    "type": "integer",
                    "description": "How many entries matched, counting the ones not listed.",
                },
                "note": {
                    "type": "string",
                    "description": (
                        "Only when the plain result would mislead: the listing was "
                        "truncated, nothing matched, or a directory could not be read."
                    ),
                },
            },
            ["path", "pattern", "entries", "truncated", "total_matches"],
        ),
    ),
    BuiltinTool(
        "search-code",
        "search_code",
        "Search the code for text/regex (grep-style). Returns matches with context.",
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
    # RETIRADAS (F5 de registry-egress-followups, 2026-07-28; ADR 0093 D3): las
    # cuatro `run_*` (run-pytest / run-lint / run-typecheck / run-build) eran
    # `docker_command`, y `DockerCommandTool` dentro del sandbox falla SIEMPRE por
    # diseño: la imagen del agent-runtime «carries NO Docker client» y no recibe
    # socket (Dockerfile + `test_docker_command_tool_retired`). Ofrecerlas era
    # prometer al operador —y anunciarle al modelo— cuatro tools que no pueden
    # ejecutarse: el mismo fallo B-04 de `send_notification`, con 62 grants vivos
    # detrás el día de la retirada y un turno quemado por invocación.
    #
    # La vía que SÍ ejecuta el toolchain es `stack-exec` (ADR 0093): el worker lo
    # corre en el runtime-template del proyecto, con su egress y su caché de
    # dependencias. Los defaults de rol ya la conceden en su lugar.
    #
    # Sus NOMBRES siguen en `_CATALOG_TOOL_NAMES` a propósito —a diferencia de la
    # familia `git_*` de abajo, que salió del todo—: si dejaran de ser canónicos,
    # `tool_is_runtime_wired` caería al atajo por `implementation_type` (que dice
    # True para `docker_command`) y una fila superviviente en una BD sin migrar
    # volvería a ser asignable. Ver `tests/unit/test_runtime_wired_contract.py`.
    # ----- Git -----
    # RETIRED (task_06_18_06, ADR 0049): the four git tools (git_status /
    # git_diff / git_commit / git_log) carried a UI category but NO runtime
    # executor (`register_git_tools` does not exist), so any assignment died as
    # a silent `unknown tool`. They are removed from the seed until a real
    # executor lands; offering them as assignable would lie about availability.
    # ----- HTTP / Web -----
    BuiltinTool(
        "http-get",
        "http_get",
        "Generic HTTP GET. Restricted by the project's allowed_networks.",
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
        "JSON POST. Subject to the project's allowed_networks and human_approval_policy.",
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
        "Semantic search (pgvector) over the project's knowledge bases.",
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
        "Summarize a long text to a target length (in words).",
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
        "Send a notification to the user's personal assistant or by email.",
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
    # ----- Stack command (task_06_16_02) -----
    # `shell_exec` is a BASIC builtin (is_builtin=true) but `privileged`:
    # the security level is an orthogonal axis (ADR 0044). It runs ONLY
    # binaries in the project's `allowed_commands` allowlist (deny-by-
    # default; empty list = nothing runs). The runtime instantiates a
    # per-project `ShellExecTool(allowed_commands=…)` (Plan 06.16 wiring).
    # F1.6b (auditoría 2026-07-02): la descripción sugería git ("Úsalo para git…",
    # ejemplos 'git status'/'git add -A') mientras el system prompt del runtime
    # dice "never invoke git" — contradicción directa que quemaba turnos (git ni
    # siquiera está en la allowlist y devuelve exit 128 en el sandbox: la
    # plataforma comitea por el agente al terminar).
    BuiltinTool(
        "shell-exec",
        "shell_exec",
        "Run a command INSIDE the agent's sandbox (thin utility image), restricted to the "
        "project's allowlist (deny-by-default). The command is parsed as argv (shlex) and "
        "runs with a timeout, without a shell. Use it for file utilities inside the sandbox "
        "itself (ls, cat, grep, mv, ...). NEVER for git — the platform versions your changes "
        "automatically when you finish — and it does NOT run the stack toolchain "
        "(php/composer/phpunit/npm): those binaries are not in the sandbox; use stack_exec "
        "for them.",
        "command",
        "builtin",
        "privileged",
        120,
        _obj(
            {
                "command": {
                    "type": "string",
                    "description": (
                        "Full command to run in the sandbox; its first token (basename) must "
                        "be in the project's allowlist. E.g. 'ls -la' or 'grep -r TODO src'. "
                        "For composer/phpunit/php spark use stack_exec (the stack runtime); "
                        "git is NOT allowed (versioning is automatic)."
                    ),
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory relative to the workspace (optional).",
                },
            },
            ["command"],
        ),
        _obj(
            {
                "exit_code": {"type": "integer"},
                "stdout": {"type": "string"},
                "stderr": {"type": "string"},
            },
            ["exit_code"],
        ),
    ),
    # ----- Stack exec (ADR 0093) -----
    # `stack_exec` runs a command in the project's RUNTIME-TEMPLATE (php-phpunit,
    # node-jest, …) — where the toolchain (composer/php/phpunit, npm) actually
    # exists — by asking the worker, which has Docker. The sandbox itself is a
    # thin python+git image, so `shell_exec` (which runs IN the sandbox) cannot
    # run `composer install`; `stack_exec` can. Same allowlist gate as
    # `shell_exec` (deny-by-default), enforced worker-side. `privileged` like
    # `shell_exec` (orthogonal security axis, ADR 0044).
    BuiltinTool(
        "stack-exec",
        "stack_exec",
        "Run a command from the project's toolchain (composer/phpunit/php spark, npm, ...) in "
        "the stack's runtime-template, on the task's worktree. The worker launches it (the "
        "sandbox has neither Docker nor the toolchain). Restricted to the project's allowlist "
        "(deny-by-default). Use it to install dependencies and run the stack's tests/build; "
        "shell_exec CANNOT (it runs in the thin sandbox, which lacks the toolchain). The "
        "command runs from the worktree ROOT by default; if the project lives in a "
        "SUBDIRECTORY (e.g. it was scaffolded under 'ci4build/'), pass 'cwd' so the toolchain "
        "bootstraps with the right relative paths — do NOT use 'cd' or shell chaining "
        "(unsupported: one program per call).",
        "command",
        "builtin",
        "privileged",
        600,
        _obj(
            {
                "command": {
                    "type": "string",
                    "description": (
                        "Full command to run in the stack runtime; its first token must be "
                        "in the project's allowlist. E.g. 'composer install', "
                        "'vendor/bin/phpunit' or 'php spark migrate'."
                    ),
                },
                "cwd": {
                    "type": "string",
                    "description": (
                        "Optional working directory RELATIVE to the worktree root (e.g. "
                        "'ci4build'). Use it when the project is in a subdirectory so commands "
                        "like 'vendor/bin/phpunit' resolve. Must stay inside the worktree (no "
                        "absolute path, no '..'). Omit when the project is at the worktree root."
                    ),
                },
                "timeout_s": {
                    "type": "integer",
                    "description": "Budget in seconds (optional, default 600).",
                },
            },
            ["command"],
        ),
        _obj(
            {
                "exit_code": {"type": "integer"},
                "logs": {"type": "string"},
                "timed_out": {"type": "boolean"},
            },
            ["exit_code"],
        ),
    ),
)


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------
_UPSERT_SQL = text("""
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
    """)


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
