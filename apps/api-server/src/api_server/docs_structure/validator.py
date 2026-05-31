"""Structural validator for the canonical ``/docs`` tree (task_07_02).

This module is the **structural guardrail** of Plan 07 Fase A: a
standalone validator that inspects a repo working tree and reports any
deviation from the seven canonical documentation folders mandated by
``CLAUDE.md`` principle 8.

It is *not* the Plan-11 guardrails engine (which does not exist yet). It
is a pure function plus a thin "check" gate suitable for a pre-merge /
CI hook:

  * :func:`validate_docs_structure` — the engine. Given a repo working
    tree path, returns a list of :class:`Violation` describing what is
    wrong (empty list ⇒ the tree is canonical).
  * :func:`check_docs_structure` — the gate. Wraps the validator in a
    :class:`ValidationResult` (``ok`` + ``violations``) for callers that
    want a single yes/no plus detail. The package ``__main__`` exposes
    this as a CLI returning exit code 0 (ok) / 1 (violations).

Design choices mirror :mod:`api_server.docs_structure.bootstrap`:

  * Operates on a *working-tree path* — no real bare repo required, so
    it is trivially testable in a tmp dir.
  * The seven-folder list is **never** re-declared here; it is imported
    from :data:`api_server.docs_structure.constants.CANONICAL_DOC_FOLDERS`
    so bootstrap and validation can never disagree.
  * Read-only: the validator never mutates the tree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import structlog

from api_server.docs_structure.constants import (
    CANONICAL_DOC_FOLDER_NAMES,
    CANONICAL_DOC_FOLDERS,
    DOCS_DIRNAME,
)

_log = structlog.get_logger("api_server.docs_structure.validator")


class ViolationKind(str, Enum):
    """The categories of structural violation the validator can report.

    A ``str`` enum so the value serialises cleanly to JSON/logs while
    still being comparable by identity in code.
    """

    #: ``<repo>/docs/`` is missing entirely.
    MISSING_DOCS_ROOT = "missing_docs_root"
    #: ``<repo>/docs`` exists but is a regular file, not a directory.
    DOCS_ROOT_NOT_A_DIR = "docs_root_not_a_dir"
    #: One of the seven canonical folders is absent (deleted/renamed).
    MISSING_CANONICAL_FOLDER = "missing_canonical_folder"
    #: A canonical folder name exists but as a regular file, not a dir.
    CANONICAL_FOLDER_NOT_A_DIR = "canonical_folder_not_a_dir"
    #: A top-level ``docs/`` entry that is not one of the seven folders.
    STRAY_ENTRY = "stray_entry"


@dataclass(frozen=True)
class Violation:
    """One structural problem found in a repo's ``/docs`` tree.

    ``kind`` is the machine-readable category; ``path`` is the offending
    path as a POSIX string (the ``docs`` root, a canonical folder, or a
    stray entry); ``message`` is a human-readable explanation suitable
    for a PR comment / CI log line.
    """

    kind: ViolationKind
    path: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of :func:`check_docs_structure`: ok flag + violations.

    ``ok`` is ``True`` iff ``violations`` is empty. The redundant flag is
    deliberate — it makes the common gate call site read as
    ``if not result.ok: block_merge()`` without poking at the list.
    """

    violations: tuple[Violation, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.violations

    def summary(self) -> str:
        """A one-line-per-violation human report, or an all-clear line."""
        if self.ok:
            return (
                f"docs structure OK — all {len(CANONICAL_DOC_FOLDERS)} canonical folders present."
            )
        lines = [f"docs structure INVALID — {len(self.violations)} violation(s):"]
        lines.extend(f"  - [{v.kind.value}] {v.path}: {v.message}" for v in self.violations)
        return "\n".join(lines)


def validate_docs_structure(repo_path: Path) -> list[Violation]:
    """Validate the canonical ``/docs`` tree under ``repo_path``.

    Checks, in order:

      1. ``<repo>/docs/`` exists and is a directory. If it is missing,
         that single :class:`ViolationKind.MISSING_DOCS_ROOT` is returned
         (there is nothing else to check). If it exists but is a regular
         file, :class:`ViolationKind.DOCS_ROOT_NOT_A_DIR` is returned.
      2. Each of the seven canonical folders
         (:data:`~api_server.docs_structure.constants.CANONICAL_DOC_FOLDERS`)
         exists as a directory. A missing one yields
         :class:`ViolationKind.MISSING_CANONICAL_FOLDER`; one that exists
         as a regular file yields
         :class:`ViolationKind.CANONICAL_FOLDER_NOT_A_DIR`.
      3. No *stray* top-level entry exists under ``docs/`` — every
         directory directly inside ``docs/`` must be one of the seven.
         A renamed folder therefore surfaces as a pair: a
         ``MISSING_CANONICAL_FOLDER`` (the original) plus a ``STRAY_ENTRY``
         (the new name). Loose top-level files (e.g. an ``index.md``) are
         NOT flagged — only directories are part of the contract; the
         markdown lint (task_07_03) owns file-level rules.

    Args:
        repo_path: Path to a repo *working tree*. May be relative or
            absolute; it is resolved internally. Need not be a git repo.

    Returns:
        A list of :class:`Violation`, in deterministic order (root checks
        first, then canonical folders in canonical order, then strays
        sorted by name). An empty list means the tree is canonical.
    """
    repo_path = repo_path.resolve()
    docs_root = repo_path / DOCS_DIRNAME

    if not docs_root.exists():
        return [
            Violation(
                kind=ViolationKind.MISSING_DOCS_ROOT,
                path=docs_root.as_posix(),
                message=(
                    f"the {DOCS_DIRNAME!r} directory is missing; a repo must carry "
                    f"the {len(CANONICAL_DOC_FOLDERS)} canonical documentation folders"
                ),
            )
        ]
    if not docs_root.is_dir():
        return [
            Violation(
                kind=ViolationKind.DOCS_ROOT_NOT_A_DIR,
                path=docs_root.as_posix(),
                message=f"{DOCS_DIRNAME!r} exists but is a file, not a directory",
            )
        ]

    violations: list[Violation] = []

    # (2) Every canonical folder must be present, as a directory.
    for folder in CANONICAL_DOC_FOLDERS:
        target = docs_root / folder.name
        if not target.exists():
            violations.append(
                Violation(
                    kind=ViolationKind.MISSING_CANONICAL_FOLDER,
                    path=target.as_posix(),
                    message=(
                        f"canonical folder {folder.name!r} is missing "
                        "(deleted or renamed); it is one of the "
                        f"{len(CANONICAL_DOC_FOLDERS)} mandatory folders"
                    ),
                )
            )
        elif not target.is_dir():
            violations.append(
                Violation(
                    kind=ViolationKind.CANONICAL_FOLDER_NOT_A_DIR,
                    path=target.as_posix(),
                    message=(
                        f"canonical folder {folder.name!r} exists but is a file, " "not a directory"
                    ),
                )
            )

    # (3) No stray top-level *directories* under docs/. Renames show up
    # here as the new name (and as a MISSING above for the old name).
    for entry in sorted(docs_root.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            # Loose files are out of scope for the structural guardrail.
            continue
        if entry.name not in CANONICAL_DOC_FOLDER_NAMES:
            violations.append(
                Violation(
                    kind=ViolationKind.STRAY_ENTRY,
                    path=entry.as_posix(),
                    message=(
                        f"unexpected top-level folder {entry.name!r} under "
                        f"{DOCS_DIRNAME!r}; only the "
                        f"{len(CANONICAL_DOC_FOLDERS)} canonical folders are allowed"
                    ),
                )
            )

    return violations


def check_docs_structure(repo_path: Path) -> ValidationResult:
    """Thin pre-merge / CI gate around :func:`validate_docs_structure`.

    Returns a :class:`ValidationResult` so a caller can branch on
    ``result.ok`` and surface ``result.summary()`` (or the structured
    ``result.violations``) as a PR comment / CI annotation. This is the
    standalone gate — NOT the Plan-11 guardrails engine.
    """
    violations = validate_docs_structure(repo_path)
    result = ValidationResult(violations=tuple(violations))
    _log.info(
        "docs_structure.check",
        repo_path=repo_path.resolve().as_posix(),
        ok=result.ok,
        violation_count=len(violations),
        kinds=sorted({v.kind.value for v in violations}),
    )
    return result


__all__ = [
    "ValidationResult",
    "Violation",
    "ViolationKind",
    "check_docs_structure",
    "validate_docs_structure",
]
