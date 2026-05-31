"""Technical Writer doc-generation tooling (Plan 07 — Fase B).

This package owns the **deterministic** renderers the post-plan workflow
(task_07_06) uses to materialise plan deliverables under ``/docs``:

  * :mod:`api_server.tech_writer.changelog` — task_07_07's canonical
    per-plan changelog template + :func:`render_changelog`, which fills
    the ``docs/07-changelog/{plan_id}.md`` template from plan metadata
    (frontmatter + tasks + optional decisions + PR link). Pure functions,
    no I/O, no clock — same input ⇒ byte-identical output.
  * :mod:`api_server.tech_writer.adr` — task_07_08's canonical ADR
    template + :func:`render_adr`, plus :func:`next_adr_number` which
    allocates the next free zero-padded number under
    ``docs/05-architecture-decisions/`` so ADRs never collide.
  * :mod:`api_server.tech_writer.generation` — task_07_06's deterministic
    :func:`generate_plan_docs`, the post-plan workflow core the Technical
    Writer agent invokes: it writes the changelog + one ADR per declared
    decision, idempotently (skip-if-exists, never clobber a human edit),
    and returns a :class:`GenerationManifest`.

Generation is deliberately a deterministic template fill, *not* a live LLM
call: the Technical Writer agent (task_07_05) curates wording, but the
file structure is guaranteed by code so the structural / language / lint
gates of Fase A always pass.
"""

from __future__ import annotations

from api_server.tech_writer.adr import (
    ADR_FIRST_NUMBER,
    ADR_NUMBER_WIDTH,
    DEFAULT_ADR_STATUS,
    AdrMeta,
    adr_filename_stem,
    format_adr_number,
    next_adr_number,
    render_adr,
)
from api_server.tech_writer.changelog import (
    DEFAULT_DOCS_LANGUAGE,
    SECTION_LABELS,
    ChangelogDecision,
    ChangelogTask,
    PlanMeta,
    render_changelog,
)
from api_server.tech_writer.generation import (
    ADR_FOLDER,
    CHANGELOG_FOLDER,
    REFERENCE_FOLDER,
    REFERENCE_UPDATE_NOTE,
    DecisionSpec,
    GenerationManifest,
    SkippedFile,
    WrittenFile,
    generate_plan_docs,
)

__all__ = [
    "ADR_FIRST_NUMBER",
    "ADR_FOLDER",
    "ADR_NUMBER_WIDTH",
    "CHANGELOG_FOLDER",
    "DEFAULT_ADR_STATUS",
    "DEFAULT_DOCS_LANGUAGE",
    "REFERENCE_FOLDER",
    "REFERENCE_UPDATE_NOTE",
    "SECTION_LABELS",
    "AdrMeta",
    "ChangelogDecision",
    "ChangelogTask",
    "DecisionSpec",
    "GenerationManifest",
    "PlanMeta",
    "SkippedFile",
    "WrittenFile",
    "adr_filename_stem",
    "format_adr_number",
    "generate_plan_docs",
    "next_adr_number",
    "render_adr",
    "render_changelog",
]
