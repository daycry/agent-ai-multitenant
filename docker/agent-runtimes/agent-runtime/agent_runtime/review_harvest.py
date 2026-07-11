"""Review harvest — the agent's CUMULATIVE deliverable read from the worktree
(refactor P5).

The per-run write capture only sees files written in the CURRENT run; the
authoritative self-review must judge the TRUE on-disk state (incremental runs,
pre-existing work — case 019f27cc). This module owns the bounded worktree scan
and the task/output path-reference extraction that feeds its ``prefer`` list.

`agent_runtime.graph` re-exports everything here (its historical home).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_WORKSPACE_ROOT_ENV = "AGENT_WORKSPACE_ROOT"
# Never part of the reviewable deliverable: VCS, framework deps, agent scratch,
# build noise. Mirrors what file_tools/list_files already hide from the agent.
_REVIEW_EXCLUDE_DIRS = frozenset(
    {".git", "vendor", "node_modules", "__pycache__", ".venv", "venv", ".claude"}
)
_REVIEW_EXCLUDE_NAMES = frozenset({"agent_task.json", ".claude.json"})
_REVIEW_EXCLUDE_SUFFIXES = (".pyc", ".lock", ".log", ".map")
# Bound the worktree scan (the review prompt caps further to _REVIEW_MAX_FILES).
_WORKTREE_SCAN_MAX_FILES = 40
_WORKTREE_SKIP_FILE_BYTES = 200_000


def _workspace_root() -> Path:
    """The worktree root the agent's file tools resolve against (``/workspace``,
    or ``AGENT_WORKSPACE_ROOT`` for tests). Mirrors ``builtin_families``."""
    return Path(os.environ.get(_WORKSPACE_ROOT_ENV) or "/workspace")


def _collect_rel_paths(root: Path) -> list[str]:
    """Relative worktree paths, minus VCS/deps/scratch (shared filter)."""
    if not root.is_dir():
        return []
    try:
        candidates = [p for p in root.rglob("*") if p.is_file()]
    except OSError:  # pragma: no cover - defensive (permission / race)
        return []
    rels: list[str] = []
    for path in candidates:
        try:
            rel_path = path.relative_to(root)
        except ValueError:  # pragma: no cover - defensive
            continue
        if set(rel_path.parts) & _REVIEW_EXCLUDE_DIRS:
            continue
        if rel_path.name in _REVIEW_EXCLUDE_NAMES or rel_path.suffix in _REVIEW_EXCLUDE_SUFFIXES:
            continue
        rels.append(rel_path.as_posix())
    return rels


# P0-6 (investigación 2026-07-11): overview inicial del worktree para el
# IMPLEMENTADOR — solo paths (sin contenidos), acotado. Un re-dispatch arranca
# sobre trabajo acumulado y antes lo re-descubría a base de list_files/read_file
# (read-churn). Techo mayor que el harvest de review (60 vs 40) porque son
# rutas, no contenidos.
_OVERVIEW_MAX_FILES = 60


def worktree_file_list(
    root: Path | None = None, *, max_files: int = _OVERVIEW_MAX_FILES
) -> list[str]:
    """Bounded, sorted list of the worktree's existing files (paths only, P0-6).

    Empty worktree / missing root → ``[]`` (a first attempt stays noise-free).
    Same exclusion rules as the reviewer's harvest."""
    return sorted(_collect_rel_paths(root if root is not None else _workspace_root()))[:max_files]


def _harvest_worktree_files(root: Path, prefer: list[str]) -> list[dict[str, str]]:
    """Read the agent's CUMULATIVE deliverable from the worktree on disk.

    The per-run write capture (``_AgentLoop.written_files``) only sees files
    written in the CURRENT run; an incremental run that builds on a prior committed
    run leaves earlier files untouched, so the self-review would judge an INCOMPLETE
    picture and reject a whole deliverable as "missing files" (observed live on a
    re-run of an escalated JWT task). Reading the worktree gives the reviewer the
    TRUE current state, and the on-disk content is the FINAL content (after every
    edit), not the write-time argument. VCS/framework dirs are excluded and the scan
    is bounded; ``prefer`` (this run's written paths) are ordered FIRST so the
    current work is always shown even when the cap truncates. Returns ``[]`` when
    there is no worktree (analysis/design runs, tests) → the caller falls back to the
    per-run capture and prose-only review is unchanged.
    """
    rels = _collect_rel_paths(root)
    if not rels:
        return []
    preferred = [r for r in prefer if r in rels]
    ordered = preferred + sorted(r for r in rels if r not in preferred)
    harvested: list[dict[str, str]] = []
    for rel in ordered[:_WORKTREE_SCAN_MAX_FILES]:
        file_path = root / rel
        try:
            if file_path.stat().st_size > _WORKTREE_SKIP_FILE_BYTES:
                continue
            harvested.append(
                {"path": rel, "content": file_path.read_text(encoding="utf-8", errors="replace")}
            )
        except OSError:  # pragma: no cover - defensive (binary / permission)
            continue
    return harvested


# Paths referenciados por la task/output — máx. entradas que se añaden a
# `prefer` del harvest (caso 019f27cc: el entregable pre-existente quedaba
# fuera del cap de 40 y el self-review no podía verlo).
_REFERENCED_PATHS_MAX = 10
# Path con directorio (docs/x.md) O nombre de fichero suelto en la raíz
# (phpunit.xml — caso 019f27ed). La extensión debe EMPEZAR por letra para no
# capturar números de versión («1.0.0»); las entradas que no existan en el
# worktree las descarta el harvest (prefer ∩ rels), así que el regex puede ser
# generoso sin riesgo.
_PATH_TOKEN_RE = re.compile(r"[\w][\w./\\-]*\.[A-Za-z]\w{0,7}")


def _referenced_paths(state: Mapping[str, Any]) -> list[str]:
    """Paths tipo-fichero mencionados en la task (descripción + criterios) y en
    el output final del agente, en orden de aparición y sin duplicados.

    Alimenta el ``prefer`` del harvest del self-review: el entregable que los
    criterios NOMBRAN debe estar siempre en el prompt del reviewer, aunque este
    run no lo haya escrito (trabajo pre-existente de un run anterior — caso
    019f27cc) y aunque el worktree tenga más ficheros que el cap del harvest."""
    task = state.get("task") or {}
    chunks: list[str] = [str(task.get("description") or "")]
    for criterion in task.get("acceptance_criteria") or []:
        if isinstance(criterion, dict):
            chunks.append(" ".join(str(v) for v in criterion.values()))
        else:
            chunks.append(str(criterion))
    chunks.append(str(state.get("output") or ""))
    seen: list[str] = []
    for chunk in chunks:
        for match in _PATH_TOKEN_RE.findall(chunk):
            normalized = match.replace("\\", "/").strip("/")
            if normalized not in seen:
                seen.append(normalized)
            if len(seen) >= _REFERENCED_PATHS_MAX:
                return seen
    return seen
