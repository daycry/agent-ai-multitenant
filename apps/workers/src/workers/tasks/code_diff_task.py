"""Compute a plan's code diff IN THE WORKER (fix del 500 del visor de diff, ADR 0099).

El endpoint ``GET /projects/{pid}/plans/{planId}/code-diff`` calculaba el diff
corriendo git EN EL PROCESO de la api-server. Pero la api-server NO monta el
volumen ``agent-data`` y su ``data_root`` es el default ``/data/agent-platform``
(inexistente en su contenedor) → ``subprocess.run(cwd=bare)`` lanzaba
``FileNotFoundError`` → 500 SIEMPRE, para todos los proyectos.

El worker SÍ posee el data_root real (``WORKERS_DATA_ROOT`` = el volumen
montado) y corre como el owner de los bares (``app``), así que el diff se
calcula aquí y la api-server relaya el resultado (mismo patrón síncrono que
``workers.run_stack_command`` ↔ ``run_stack_command_and_wait``).
"""

from __future__ import annotations

from typing import Any

import structlog

from workers.celery_app import app
from workers.config import get_settings

_log = structlog.get_logger("workers.tasks")


@app.task(name="workers.compute_plan_code_diff")  # type: ignore[untyped-decorator]
def compute_plan_code_diff(request: dict[str, Any]) -> dict[str, Any]:
    """Diff read-only de la rama del plan contra su merge-base con la default.

    ``request``: ``{tenant_slug, project_slug, plan_id, plan_slug}``. Devuelve
    ``{ok: True, ...diff serializado...}`` o ``{ok: False, error}`` (rama/bare no
    materializados) — la api-server mapea ``ok=False`` a un 404 neutro. Nunca
    propaga una excepción cruda (que el caller vería como un 500)."""
    from api_server.code_diff import PlanCodeDiffError, plan_code_diff

    settings = get_settings()
    try:
        diff = plan_code_diff(
            settings.data_root,
            tenant_slug=str(request["tenant_slug"]),
            project_slug=str(request["project_slug"]),
            plan_id=str(request["plan_id"]),
            plan_slug=str(request["plan_slug"]),
        )
    except PlanCodeDiffError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # defensivo: jamás devolver un 500 al visor
        _log.warning(
            "code_diff.compute_failed",
            plan_id=str(request.get("plan_id", "")),
            error=str(exc)[:300],
        )
        return {"ok": False, "error": "could not compute the plan diff"}

    return {
        "ok": True,
        "plan_id": str(request["plan_id"]),
        "plan_branch": diff.plan_branch,
        "default_branch": diff.default_branch,
        "base_sha": diff.base_sha,
        "head_sha": diff.head_sha,
        "unchanged": diff.unchanged,
        "truncated": diff.truncated,
        "files": diff.files,
        "lines": [{"kind": ln.kind, "content": ln.content} for ln in diff.lines],
    }
