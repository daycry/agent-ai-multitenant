"""Diff de CÓDIGO de la rama de un plan (ADR 0099, opción A — read-only).

Hermano del diff de docs (``docs_viewer.diff_doc``) pero sobre el BARE real del
proyecto y sin el candado ``.md``: muestra qué cambió la rama ``plan/*``
respecto a su merge-base con la rama por defecto. Reutiliza el runner git
auditado (``workers.git_repos._run_git``), la validación de refs
(``_safe_git_ref``) y el clasificador de líneas del visor de docs. Acotado:
el cuerpo se trunca a ``MAX_DIFF_CHARS`` (el resumen por fichero SIEMPRE llega
completo vía ``--numstat``). Nunca filtra stderr de git al cliente.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from api_server.docs_viewer.service import DocDiffLine, _classify_diff_line, _safe_git_ref

logger = structlog.get_logger("api_server.code_diff")

# Tope del CUERPO del diff (chars) — un plan grande no puede volcar MB al
# navegador; el resumen numstat viaja completo y la vista avisa del corte.
MAX_DIFF_CHARS = 400_000
# Tope de ficheros listados en el resumen (defensivo).
MAX_FILES = 500


class PlanCodeDiffError(Exception):
    """El diff no pudo calcularse (rama inexistente, bare ausente, git falló)."""


@dataclass(frozen=True)
class PlanCodeDiff:
    """El diff de la rama del plan contra su merge-base con la default."""

    plan_branch: str
    default_branch: str
    base_sha: str
    head_sha: str
    unchanged: bool
    truncated: bool
    files: list[dict[str, Any]] = field(default_factory=list)
    lines: list[DocDiffLine] = field(default_factory=list)


def _parse_numstat(raw: str) -> list[dict[str, Any]]:
    """``git diff --numstat`` → [{path, additions, deletions}] (binarios = '-')."""
    files: list[dict[str, Any]] = []
    for row in raw.splitlines():
        parts = row.split("\t")
        if len(parts) != 3:
            continue
        adds, dels, path = parts
        files.append(
            {
                "path": path,
                "additions": int(adds) if adds.isdigit() else None,
                "deletions": int(dels) if dels.isdigit() else None,
            }
        )
        if len(files) >= MAX_FILES:
            break
    return files


def plan_code_diff(
    data_root: str | Path,
    *,
    tenant_slug: str,
    project_slug: str,
    plan_id: str,
    plan_slug: str,
    max_diff_chars: int = MAX_DIFF_CHARS,
) -> PlanCodeDiff:
    """Diff read-only de la rama del plan en el bare real del proyecto.

    Las coordenadas (bare + nombre de rama) salen de
    :func:`workers.plan_git.worktree_coordinates` — la MISMA primitiva que usan
    provisión/commit/review (hallazgo #10a: nunca reconstruir a mano). El diff
    es ``merge-base(default, branch)..branch`` — exactamente lo que el plan
    aporta sobre la base, aunque la default haya avanzado después.

    Raises:
        PlanCodeDiffError: bare/rama inexistentes o git falló (mensaje neutro).
    """
    # Lazy import: el grafo de módulos del api-server no paga workers hasta
    # que alguien pide un diff (mismo patrón que docs_viewer/kb_sync).
    from workers.git_repos import GitCommandError, _run_git
    from workers.plan_git import worktree_coordinates

    layout, branch = worktree_coordinates(
        data_root=data_root,
        tenant_slug=tenant_slug,
        project_slug=project_slug,
        plan_id=plan_id,
        plan_slug=plan_slug,
    )
    bare = layout.bare_repo_path(project_slug)
    try:
        # `_safe_git_ref` (ValueError on a malformed ref) y `_run_git` (cwd
        # inexistente → OSError/FileNotFoundError; git rc!=0 → GitCommandError)
        # entran TODOS al mismo saco: un bare no materializado o una rama que aún
        # no existe deben ser un 404 neutro, NUNCA un 500. Antes `_safe_git_ref`
        # y el cwd ausente caían fuera del except → 500 (bug: el endpoint corría
        # en la api-server, que no monta el volumen agent-data → cwd inexistente).
        safe_branch = _safe_git_ref(branch)
        default_branch = _safe_git_ref(
            _run_git("symbolic-ref", "--short", "HEAD", cwd=bare).strip()
        )
        base_sha = _run_git("merge-base", default_branch, safe_branch, cwd=bare).strip()
        head_sha = _run_git("rev-parse", safe_branch, cwd=bare).strip()
        numstat = _run_git("diff", "--numstat", f"{base_sha}..{safe_branch}", cwd=bare)
        raw = _run_git("diff", f"{base_sha}..{safe_branch}", cwd=bare)
    except (GitCommandError, OSError, ValueError) as exc:
        # Nunca filtrar stderr crudo de git (puede llevar paths internos).
        logger.info(
            "code_diff.git_failed",
            plan_id=plan_id,
            branch=branch,
            error=str(exc),
        )
        raise PlanCodeDiffError("could not diff the plan branch") from exc

    truncated = len(raw) > max_diff_chars
    if truncated:
        raw = raw[:max_diff_chars]
    lines = [parsed for line in raw.splitlines() if (parsed := _classify_diff_line(line))]
    files = _parse_numstat(numstat)
    return PlanCodeDiff(
        plan_branch=branch,
        default_branch=default_branch,
        base_sha=base_sha,
        head_sha=head_sha,
        unchanged=not files,
        truncated=truncated,
        files=files,
        lines=lines,
    )


__all__ = ["MAX_DIFF_CHARS", "PlanCodeDiff", "PlanCodeDiffError", "plan_code_diff"]
