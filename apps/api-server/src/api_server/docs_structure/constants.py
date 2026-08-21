"""Canonical ``/docs`` folder constants — the single source of truth.

``CLAUDE.md`` principle 8 fixes seven numbered documentation folders.
Plan 07 ("Decisiones Clave") explains the choice: a Diátaxis-adapted
structure with seven *numbered* folders (instead of the four original
Diátaxis quadrants) so it scales to large projects.

Every later Fase-A task — the structural validator (task_07_02), the
markdownlint config (task_07_03) and the language detector (task_07_04)
— imports :data:`CANONICAL_DOC_FOLDERS` from here. There is intentionally
exactly ONE place in the codebase that knows the folder names and their
order; bootstrap and validation can therefore never disagree.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CanonicalDocFolder:
    """One canonical documentation folder.

    ``name`` is the on-disk directory name (e.g. ``"01-overview"``).
    ``diataxis_purpose`` is a one-line description of what kind of
    content belongs there, surfaced in each folder's stub README so an
    author dropped into an empty repo knows where to write.
    """

    name: str
    diataxis_purpose: str


# The seven canonical folders, in declaration order. Order matters: the
# numeric prefixes are part of the contract (sidebars/tools sort by name)
# and the bootstrap creates them in this sequence. Keep this as the ONLY
# definition in the codebase.
CANONICAL_DOC_FOLDERS: tuple[CanonicalDocFolder, ...] = (
    CanonicalDocFolder(
        name="01-overview",
        diataxis_purpose=(
            "Explanation — high-level overview: what the project is, why it "
            "exists, key concepts and the big picture."
        ),
    ),
    CanonicalDocFolder(
        name="02-getting-started",
        diataxis_purpose=(
            "Tutorial — learning-oriented, hands-on first steps to get a "
            "newcomer productive (install, run, first task)."
        ),
    ),
    CanonicalDocFolder(
        name="03-guides",
        diataxis_purpose=(
            "How-to — task-oriented recipes that solve a concrete problem for "
            "someone who already knows the basics."
        ),
    ),
    CanonicalDocFolder(
        name="04-reference",
        diataxis_purpose=(
            "Reference — information-oriented technical description of APIs, "
            "schemas, CLI flags and configuration."
        ),
    ),
    CanonicalDocFolder(
        name="05-architecture-decisions",
        diataxis_purpose=(
            "Explanation — Architecture Decision Records (ADRs) numbered "
            "sequentially, capturing context, options and the chosen path."
        ),
    ),
    CanonicalDocFolder(
        name="06-runbooks",
        diataxis_purpose=(
            "How-to — operational runbooks: step-by-step procedures for "
            "deploys, incidents, backups and recovery."
        ),
    ),
    CanonicalDocFolder(
        name="07-changelog",
        diataxis_purpose=(
            "Reference — one entry per closed plan: summary, tasks, decisions and the PR link."
        ),
    ),
)

# Convenience: the folder names as a frozenset, for membership/parity
# checks in validators. Derived from CANONICAL_DOC_FOLDERS so it cannot
# drift.
CANONICAL_DOC_FOLDER_NAMES: frozenset[str] = frozenset(
    folder.name for folder in CANONICAL_DOC_FOLDERS
)

# The docs root directory name inside a repo working tree.
DOCS_DIRNAME = "docs"

# Empty-directory keep file. Git does not track empty directories, so the
# bootstrap drops one of these in every folder to make the structure
# survive ``git add``.
KEEP_FILENAME = ".gitkeep"

# Per-folder stub README naming the folder's Diátaxis purpose.
README_FILENAME = "README.md"


__all__ = [
    "CANONICAL_DOC_FOLDERS",
    "CANONICAL_DOC_FOLDER_NAMES",
    "DOCS_DIRNAME",
    "KEEP_FILENAME",
    "README_FILENAME",
    "CanonicalDocFolder",
]
