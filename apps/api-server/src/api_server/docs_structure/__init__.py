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
  * :mod:`api_server.docs_structure.language` — task_07_04's language
    validator: :func:`detect_doc_language` classifies a body as es/en via
    a stopword heuristic and :func:`validate_doc_language` flags confident
    mismatches against the frontmatter ``docs_language``.
  * :mod:`api_server.docs_structure.kb_sync` — task_07_09's
    :func:`sync_project_docs`, which mirrors a project's ``/docs`` tree into
    a deterministic per-project internal-docs KB (reusing the KB schema +
    the Plan 06.13 markdown chunker). It is the callable a future
    git-webhook / PR-merge hook (Plan 13) invokes.
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
from api_server.docs_structure.kb_sync import (
    DocSyncResult,
    internal_doc_id,
    internal_docs_kb_id,
    sync_project_docs,
)
from api_server.docs_structure.language import (
    SUPPORTED_LANGUAGES,
    Language,
    LanguageCheckResult,
    LanguageDetection,
    LanguageMismatch,
    check_doc_file,
    detect_doc_language,
    parse_declared_language,
    split_frontmatter,
    validate_doc_language,
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
    "SUPPORTED_LANGUAGES",
    "CanonicalDocFolder",
    "DocSyncResult",
    "Language",
    "LanguageCheckResult",
    "LanguageDetection",
    "LanguageMismatch",
    "ValidationResult",
    "Violation",
    "ViolationKind",
    "bootstrap_docs_structure",
    "check_doc_file",
    "check_docs_structure",
    "detect_doc_language",
    "internal_doc_id",
    "internal_docs_kb_id",
    "parse_declared_language",
    "split_frontmatter",
    "sync_project_docs",
    "validate_doc_language",
    "validate_docs_structure",
]
