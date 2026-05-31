"""Bootstrap the seven canonical ``/docs`` folders in a repo (task_07_01).

When a new project repo is created the platform must seed the canonical
documentation structure so authors (human or the Technical Writer agent)
have a place to write from day one — and so the structural validator
(task_07_02) passes on the very first push.

:func:`bootstrap_docs_structure` operates on a *working-tree path* (the
materialised worktree the worker checks out — see
``apps/workers/src/workers/git_repos.py``). It does NOT require a real
bare repo, which keeps it trivially testable in a tmp dir.

Design choices:

  * **Idempotent.** Re-running on an already-bootstrapped tree creates
    nothing new and raises nothing. The worker may call it on every plan
    run without guarding.
  * **Keep file + stub README.** Each folder gets a ``.gitkeep`` (so the
    empty directory survives ``git add``) and a minimal ``README.md``
    naming the folder's Diátaxis purpose. The README is only written if
    absent, so we never clobber real docs an author has started.
  * **Single source of truth.** The folder list comes from
    :data:`api_server.docs_structure.constants.CANONICAL_DOC_FOLDERS`.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from api_server.docs_structure.constants import (
    CANONICAL_DOC_FOLDERS,
    DOCS_DIRNAME,
    KEEP_FILENAME,
    README_FILENAME,
    CanonicalDocFolder,
)

_log = structlog.get_logger("api_server.docs_structure.bootstrap")


def _render_readme_stub(folder: CanonicalDocFolder) -> str:
    """Minimal Markdown stub for a canonical folder's README.

    Frontmatter + an H1 + the Diátaxis purpose. Kept tiny on purpose —
    it's a placeholder authors are expected to expand, not real content.
    """
    return (
        "---\n"
        f"title: {folder.name}\n"
        "lang: es\n"
        "---\n"
        "\n"
        f"# {folder.name}\n"
        "\n"
        f"{folder.diataxis_purpose}\n"
    )


def bootstrap_docs_structure(repo_path: Path) -> list[str]:
    """Create the seven canonical doc folders under ``repo_path/docs/``.

    For each folder in
    :data:`~api_server.docs_structure.constants.CANONICAL_DOC_FOLDERS`
    this creates ``<repo_path>/docs/<folder>/`` plus a ``.gitkeep`` and a
    stub ``README.md`` (only when absent).

    Args:
        repo_path: Path to a repo *working tree* (need not exist yet; it
            is created with parents). May be relative or absolute.

    Returns:
        The absolute POSIX paths of the folders that were *newly created*
        by this call, in canonical order. An already-bootstrapped tree
        therefore returns an empty list — making the call idempotent and
        the return value a useful "what did I just create" signal.

    Raises:
        NotADirectoryError: if ``repo_path`` (or the ``docs`` dir, or any
            target folder) exists but is a regular file rather than a
            directory.
    """
    repo_path = repo_path.resolve()
    if repo_path.exists() and not repo_path.is_dir():
        raise NotADirectoryError(f"repo_path exists but is not a directory: {repo_path}")

    docs_root = repo_path / DOCS_DIRNAME
    if docs_root.exists() and not docs_root.is_dir():
        raise NotADirectoryError(f"docs path exists but is not a directory: {docs_root}")
    docs_root.mkdir(parents=True, exist_ok=True)

    created: list[str] = []
    for folder in CANONICAL_DOC_FOLDERS:
        target = docs_root / folder.name
        if target.exists() and not target.is_dir():
            raise NotADirectoryError(
                f"canonical doc folder path exists but is not a directory: {target}"
            )
        was_missing = not target.exists()
        target.mkdir(parents=True, exist_ok=True)
        if was_missing:
            created.append(target.as_posix())

        # Keep file: cheap and idempotent — touch only if absent.
        keep = target / KEEP_FILENAME
        if not keep.exists():
            keep.write_text("", encoding="utf-8")

        # Stub README: never clobber an author's real content.
        readme = target / README_FILENAME
        if not readme.exists():
            readme.write_text(_render_readme_stub(folder), encoding="utf-8")

    _log.info(
        "docs_structure.bootstrap",
        repo_path=repo_path.as_posix(),
        created_count=len(created),
        total_folders=len(CANONICAL_DOC_FOLDERS),
    )
    return created


__all__ = ["bootstrap_docs_structure"]
