"""Canonical ``/docs`` structure tooling (Plan 07 — Fase A).

This package owns the *single source of truth* for the seven canonical
documentation folders mandated by ``CLAUDE.md`` principle 8 and the
Diátaxis-adapted structure (Plan 07 "Decisiones Clave").

Sub-modules:

  * :mod:`api_server.docs_structure.constants` — the canonical folder
    list (:data:`CANONICAL_DOC_FOLDERS`) and the keep-file name. Later
    Fase-A tasks (07_02 structural validator, 07_03 markdownlint,
    07_04 language detector) MUST import the list from here rather than
    re-declaring it, so the structure can never drift between bootstrap
    and validation.
  * :mod:`api_server.docs_structure.bootstrap` — task_07_01's idempotent
    :func:`bootstrap_docs_structure`, which materialises the folders in a
    repo working tree.
  * :mod:`api_server.docs_structure.validator` — task_07_02's structural
    guardrail: :func:`validate_docs_structure` reports deviations from the
    canonical tree and :func:`check_docs_structure` wraps it as a
    pre-merge / CI gate. The package ``__main__`` exposes the gate as a
    CLI with a 0/1 exit-code contract.
"""

from __future__ import annotations

from api_server.docs_structure.bootstrap import bootstrap_docs_structure
from api_server.docs_structure.constants import (
    CANONICAL_DOC_FOLDER_NAMES,
    CANONICAL_DOC_FOLDERS,
    DOCS_DIRNAME,
    KEEP_FILENAME,
    README_FILENAME,
    CanonicalDocFolder,
)
from api_server.docs_structure.validator import (
    ValidationResult,
    Violation,
    ViolationKind,
    check_docs_structure,
    validate_docs_structure,
)

__all__ = [
    "CANONICAL_DOC_FOLDERS",
    "CANONICAL_DOC_FOLDER_NAMES",
    "DOCS_DIRNAME",
    "KEEP_FILENAME",
    "README_FILENAME",
    "CanonicalDocFolder",
    "ValidationResult",
    "Violation",
    "ViolationKind",
    "bootstrap_docs_structure",
    "check_docs_structure",
    "validate_docs_structure",
]
