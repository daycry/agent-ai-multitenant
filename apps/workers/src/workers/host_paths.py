"""Rutas de host que un request puede traer, acotadas a `data_root` (`task_cv_45`, B-10).

Auditoría 2026-09-01: `run_cycle`, `test_runtime_task` y `review_runtime_task`
aceptaban cualquier `worktree_host_path` / `workspace` y lo montaban en el
sandbox. El docker-socket-proxy filtra ENDPOINTS, no payloads: `VOLUMES=0` no
impide un bind en `HostConfig`. El ADR 0060 sobreprometía. Aquí se exige que
la ruta, ya resuelta (sin `..` ni symlinks), viva DENTRO de `data_root` y no
sea `data_root` mismo.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath


class HostPathError(ValueError):
    """La ruta no vive bajo `data_root`."""


def ensure_under_data_root(path: str, *, data_root: str) -> str:
    """Devuelve ``path`` normalizada si está estrictamente dentro de ``data_root``;
    si no, :class:`HostPathError`. No exige que exista (el worktree puede estar
    a punto de crearse): comprueba contención, no presencia."""
    if not path or not str(path).strip():
        raise HostPathError("empty host path")
    root = Path(data_root).resolve()
    candidate = Path(path)
    # Las rutas de host son POSIX (`/data/...`); en un dev Windows `Path` no
    # las ve absolutas, pero `resolve()` las ancla igual bajo la misma unidad.
    if not (candidate.is_absolute() or PurePosixPath(str(path)).is_absolute()):
        raise HostPathError(f"host path must be absolute: {path!r}")
    resolved = candidate.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise HostPathError(f"host path {path!r} is outside data_root {str(root)!r}")
    return str(resolved)


__all__ = ["HostPathError", "ensure_under_data_root"]
