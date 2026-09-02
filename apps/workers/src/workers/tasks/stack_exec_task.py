"""stack_exec — el agente pide al worker correr su toolchain (ADR 0093).

El agent-runtime no puede lanzar contenedores (sin socket, principio 2):
POSTea a /internal/agent/run-stack, que encola esta task. El comando se gatea
contra el ``allowed_commands`` del proyecto (deny-by-default, ADR 0045) y
corre en el runtime template del stack sobre el worktree de la tarea (RW),
con egress proxied a los registries (ADR 0094).

NO usa ``workers.docker_client``: distingue import-fail de daemon-fail en su
mensaje al agente y necesita ``docker.errors.APIError`` en un ``except``.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from workers.celery_app import app
from workers.config import Settings, get_settings
from workers.db import worker_engine

# P1-4 (investigación 2026-07-11): destilado de logs. El tail crudo de 8000
# chars podía CORTAR la traza útil (el assert que falló al principio, ruido
# después). Se anteponen las líneas-señal de fallo, acotadas, al tail.
_LOG_TAIL_CHARS = 8000
_SIGNAL_MAX_LINES = 20
_SIGNAL_MAX_CHARS = 2500
_SIGNAL_RE = re.compile(r"(?i)\b(error|exception|fail(ed|ure|ures)?|fatal|assert)\b")


def distill_stack_logs(logs: str, *, tail_chars: int = _LOG_TAIL_CHARS) -> str:
    """Logs de stack_exec destilados: señales de fallo + tail (puro).

    Logs cortos pasan VERBATIM. Largos: se extraen las primeras líneas que
    parecen señal de fallo (error/exception/fail/fatal/assert, cap de líneas y
    chars) y se anteponen etiquetadas al tail — el agente siempre ve QUÉ falló
    aunque el final sea puro ruido. Sin señales → tail de siempre."""
    if len(logs) <= tail_chars:
        return logs
    signals: list[str] = []
    used = 0
    for line in logs.splitlines():
        if not _SIGNAL_RE.search(line):
            continue
        chunk = line.strip()[:400]
        if used + len(chunk) > _SIGNAL_MAX_CHARS:
            break
        signals.append(chunk)
        used += len(chunk)
        if len(signals) >= _SIGNAL_MAX_LINES:
            break
    tail = logs[-tail_chars:]
    if not signals:
        return tail
    header = "[señales de fallo detectadas antes del tail]"
    return header + "\n" + "\n".join(signals) + "\n[...tail...]\n" + tail


_log = structlog.get_logger("workers.tasks")

_STACK_EXEC_DEFAULT_TIMEOUT_S = 600


# Utilidades de TEXTO cuya denegación tiene una alternativa mejor que pedirle al
# operador que las autorice: `read_file` con offset/limit no pasa por la
# allowlist y hace el mismo trabajo para el caso que importa (mirar un trozo de
# fichero).
_TEXT_READ_PROGRAMS = frozenset(
    {"sed", "awk", "head", "tail", "grep", "cat", "less", "more", "cut", "tr", "wc"}
)


def _stack_command_allowed(command: str, allowed: list[str]) -> str | None:
    """Deny-by-default gate (ADR 0045), identical to ``shell_exec``: the first
    token's basename must be in ``allowed``. Returns an error string, or ``None``
    when the command is allowed. An empty allowlist denies everything."""
    import shlex
    from pathlib import Path

    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return f"could not parse command: {exc}"
    if not argv:
        return "empty command"
    allowed_set = set(allowed)
    program = Path(argv[0]).name
    # Accept either the basename (`php`, `composer`) or the full relative token
    # (`vendor/bin/phpunit`) — the project commands UI offers both shapes.
    if program not in allowed_set and argv[0] not in allowed_set:
        # Make the denial ACTIONABLE so the model self-corrects to a single allowed
        # command instead of falling back to re-reading files (the read-churn that
        # blocked "Auditar dependencias"). Keep the "command not allowed:" prefix so
        # existing log asserts still match (use .startswith).
        allowed_display = sorted(allowed_set) or ["(none configured)"]
        hint = ""
        if program in {"bash", "sh", "zsh", "dash"} or any(
            op in command for op in ("&&", "||", ";", "|")
        ):
            hint = (
                " stack_exec runs ONE allowed program per call; shell chaining "
                "(&&, ||, ;, |) is NOT supported — issue each command in a separate call."
            )
        elif program in _TEXT_READ_PROGRAMS:
            # G6b (plan guardas-research): el agente que quería mirar un trozo de
            # fichero se topaba con esto y caía en releer el fichero ENTERO una y
            # otra vez — la read-churn que disparaba las guardas de esterilidad.
            #
            # Se le ofrece `read_file`, que no pasa por la allowlist, en vez de la
            # alternativa "obvia" `head -n N | tail`: esa lleva TUBERÍA, que
            # stack_exec tampoco admite, así que sugerirla sería mandarle a un
            # segundo fallo.
            hint = (
                " To read part of a file you do NOT need a shell command: call "
                "`read_file` with `offset`/`limit` — it is always available and "
                "needs no allowlist."
            )
        return f"command not allowed: {program}.{hint} Allowed: {allowed_display}."
    return None


def _resolve_stack_dep_cache(
    template: Any,
    worktree_host_path: str,
    data_root: str,
    project_root: str | None = None,
    *,
    tenant_slug: str | None = None,
) -> str | None:
    """Resolve the warm dep-cache host path for a stack command, or None.

    Best-effort (ADR 0045/0093): a missing/cold lock file or cache layout must
    never block the command — the install just runs cold (and resolves its
    registries via the proxy, ADR 0094).

    ``project_root`` (ADR 0162) es el directorio DONDE VA A CORRER el comando, no
    la raíz del worktree: es ahí donde está el lockfile. Con el worktree a secas,
    todo proyecto anidado se quedaba sin caché en silencio."""
    from pathlib import Path

    from shared_test_runtimes.dep_cache import DepCacheManager, compute_lock_hash

    try:
        lock = compute_lock_hash(Path(worktree_host_path), template.id, project_root=project_root)
        if not lock.hash or not tenant_slug:
            # `task_cv_24`: sin tenant no hay caché — nunca una compartida.
            return None
        entry = DepCacheManager(Path(data_root) / "dep-cache").mount_for(
            template, lock.hash, tenant_slug=tenant_slug
        )
        return str(entry.host_path) if entry is not None else None
    except Exception:  # pragma: no cover - dep-cache is a best-effort optimisation
        return None


@app.task(name="workers.run_stack_command")  # type: ignore[untyped-decorator]
def run_stack_command(request: dict[str, Any]) -> dict[str, Any]:
    """Run one stack command for a task in its runtime template (ADR 0093).

    ``request``: ``{tenant_id, task_id, command, timeout_s?}``. Returns
    ``{exit_code, logs, timed_out}``. The command is gated by the project's
    ``allowed_commands`` (deny-by-default) BEFORE it runs.
    """
    settings = get_settings()
    return asyncio.run(_run_stack_command(request, settings))


# justified: guard-clause style — each early return is a distinct, named
# failure mode with an actionable message for the agent (F0.3).
async def _run_stack_command(  # noqa: PLR0911, PLR0915
    request: dict[str, Any], settings: Settings
) -> dict[str, Any]:
    """Async core: resolve task→project (slug/runtime/allowlist) + the existing
    worktree path, gate the command against the allowlist, run it in the stack
    runtime over the worktree (RW), return rc+logs."""
    from pathlib import Path

    from api_server.db.domain import Project, Task
    from api_server.db.models import Organization
    from sqlalchemy import select

    tenant_id = UUID(str(request["tenant_id"]))
    task_id = UUID(str(request["task_id"]))
    command = str(request.get("command") or "")
    timeout_s = int(request.get("timeout_s") or _STACK_EXEC_DEFAULT_TIMEOUT_S)
    # Optional working directory (ADR 0093, 2026-07-24): a path relative to the
    # worktree root so a project scaffolded under a subdir (e.g. ``ci4build/``)
    # runs its toolchain there. Validated in ``_apply_cwd`` (test_runtime).
    cwd_raw = request.get("cwd")
    cwd = str(cwd_raw) if cwd_raw not in (None, "") else None

    engine = worker_engine(settings)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as session:
            task = (
                await session.execute(
                    select(Task).where(Task.id == task_id, Task.tenant_id == tenant_id)
                )
            ).scalar_one_or_none()
            if task is None:
                return {"exit_code": -1, "logs": "task not found", "timed_out": False}
            project = (
                await session.execute(select(Project).where(Project.id == task.project_id))
            ).scalar_one_or_none()
            org = await session.get(Organization, tenant_id)
            if project is None or org is None or not project.slug or not org.slug:
                return {"exit_code": -1, "logs": "project/org not resolvable", "timed_out": False}
            allowed = [str(c) for c in (project.allowed_commands or [])]
            runtime_id = project.default_runtime_template
            org_slug, project_slug = org.slug, project.slug
            repo_cfg = dict(project.repository_config or {})
    finally:
        await engine.dispose()

    # ADR 0162 (decisión 1): la raíz del proyecto DENTRO del worktree, declarada
    # por el operador en `repository_config`. Ausente o vacía = vive en la raíz,
    # que es el comportamiento de siempre.
    root_raw = repo_cfg.get("project_root")
    project_root = str(root_raw).strip() or None if isinstance(root_raw, str) else None

    deny = _stack_command_allowed(command, allowed)
    if deny is not None:
        return {"exit_code": -1, "logs": deny, "timed_out": False, "allowed": sorted(allowed)}

    try:
        import dataclasses

        import docker
        from workers.git_repos import BareRepoLayout
        from workers.runtime_services import (
            RuntimeServicesConfigError,
            build_project_runtime_services,
        )
        from workers.test_runtime import (
            RuntimePlan,
            TestRuntimeRunner,
            TestRuntimeSpec,
            effective_cwd,
            resolve_run_runtime,
        )
    except ImportError:
        return {"exit_code": -1, "logs": "docker/runtime libs unavailable", "timed_out": False}
    try:
        docker.from_env().ping()
    except Exception:  # docker.errors.DockerException — daemon unavailable
        return {"exit_code": -1, "logs": "docker daemon unavailable", "timed_out": False}

    template = resolve_run_runtime(project_default_runtime=runtime_id, tool_default_runtime=None)
    # ADR 0129: project-declared runtime services (sidecars + connection env) +
    # optional custom runtime image. Invalid config → actionable error, not crash.
    try:
        services = build_project_runtime_services(repo_cfg)
    except RuntimeServicesConfigError as exc:
        return {
            "exit_code": -1,
            "logs": f"runtime services config invalid: {exc}",
            "timed_out": False,
        }
    if services.runtime_image:
        template = dataclasses.replace(template, docker_image=services.runtime_image)
    layout = BareRepoLayout(
        data_root=Path(settings.data_root), tenant_slug=org_slug, project_slug=project_slug
    )
    worktree_host_path = str(layout.worktree_path(str(task_id)))
    # F0.3 (auditoría 2026-07-02): el bind-source debe existir ANTES de
    # containers/create. Sin esta guarda, el daemon devolvía 400 «bind source
    # path does not exist», la task Celery moría y el agente recibía un 502
    # genérico y engañoso que alimentaba reintentos inútiles.
    if not Path(worktree_host_path).is_dir():
        return {
            "exit_code": -1,
            "logs": (
                "workspace no provisionado: el worktree de la tarea no existe en el host "
                f"({worktree_host_path}). No reintentes stack_exec — la tarea no tiene "
                "workspace; hay que re-provisionarla (revisa /data/agent-platform)."
            ),
            "timed_out": False,
        }
    exec_cwd = effective_cwd(cwd, project_root)
    # El lockfile vive en la RAÍZ DEL PROYECTO, no donde corra este comando en
    # concreto. Aquí había un `project_root=exec_cwd` que parecía más coherente y
    # era una regresión: con un `cwd` explícito del agente (`tests`, `src/…`) la
    # caché se ponía a buscar `composer.lock` ahí, no lo encontraba y se
    # desactivaba — en proyectos EN LA RAÍZ, que antes acertaban siempre.
    #
    # La precedencia del ADR 0162 gobierna DÓNDE SE EJECUTA (`exec_cwd`, abajo);
    # la caché es otra pregunta y tiene otra respuesta.
    dep_cache_host_path = _resolve_stack_dep_cache(
        template,
        worktree_host_path,
        settings.data_root,
        project_root=project_root,
        tenant_slug=org_slug,
    )

    spec = TestRuntimeSpec(
        plan=RuntimePlan(template=template, checks=()),
        worktree_host_path=worktree_host_path,
        dep_cache_host_path=dep_cache_host_path,
        # ADR 0162: el runner la aplica cuando el agente no pide `cwd` — el 46 %
        # de las llamadas medidas, y las que peor salían.
        project_root=project_root,
        # ADR 0094: stack_exec IS the install (composer install / npm ci / …) —
        # it needs proxied egress to the registries for the whole command.
        dep_egress=True,
        # ADR 0129: bring up the project's declared services on the task bridge
        # and inject their connection env so the command sees the DB/cache/queue.
        aux_services=services.aux_services,
        main_env=services.main_env,
    )
    # Audit: a stack_exec launch with registry egress (prod-12 requirement).
    _log.info(
        "stack_exec_egress",
        tenant_id=str(tenant_id),
        task_id=str(task_id),
        runtime=template.id,
        command=command[:120],
        # ADR 0162: sin esto, la única forma de saber desde dónde corrió un
        # comando era deducirlo del error que devolvía.
        cwd=exec_cwd,
    )
    runner = TestRuntimeRunner(settings)
    try:
        rc, logs = await asyncio.to_thread(
            runner.run_command, spec, command, timeout_s=timeout_s, cwd=cwd
        )
    except ValueError as exc:
        # Invalid cwd (escape / unsafe chars) — actionable, not a crash.
        return {"exit_code": -1, "logs": f"stack_exec: {exc}", "timed_out": False}
    except docker.errors.APIError as exc:
        # F0.3: un fallo del daemon al crear/lanzar el runtime NO debe matar la
        # task Celery (el agente veía un 502 «failed to reach the worker» cuando
        # el worker SÍ respondió). Se devuelve estructurado y accionable.
        detail = exc.explanation or str(exc)
        _log.warning(
            "stack_exec_docker_error",
            tenant_id=str(tenant_id),
            task_id=str(task_id),
            error=str(detail)[:300],
        )
        return {
            "exit_code": -1,
            "logs": f"docker API error al lanzar el runtime del stack: {detail}",
            "timed_out": False,
        }
    return {"exit_code": rc, "logs": distill_stack_logs(logs), "timed_out": rc == 124}
