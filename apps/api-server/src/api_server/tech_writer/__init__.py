"""Technical Writer doc-generation tooling (Plan 07 — Fase B).

This package owns the **deterministic** renderers the post-plan workflow
(task_07_06) uses to materialise plan deliverables under ``/docs``:

  * :mod:`api_server.tech_writer.changelog` — task_07_07's canonical
    per-plan changelog template + :func:`render_changelog`, which fills
    the ``docs/07-changelog/{plan_id}.md`` template from plan metadata
    (frontmatter + tasks + optional decisions + PR link). Pure functions,
    no I/O, no clock — same input ⇒ byte-identical output.

Generation is deliberately a deterministic template fill, *not* a live LLM
call: the Technical Writer agent (task_07_05) curates wording, but the
file structure is guaranteed by code so the structural / language / lint
gates of Fase A always pass.
"""

from __future__ import annotations

from api_server.tech_writer.changelog import (
    DEFAULT_DOCS_LANGUAGE,
    SECTION_LABELS,
    ChangelogDecision,
    ChangelogTask,
    PlanMeta,
    render_changelog,
)

__all__ = [
    "DEFAULT_DOCS_LANGUAGE",
    "SECTION_LABELS",
    "ChangelogDecision",
    "ChangelogTask",
    "PlanMeta",
    "render_changelog",
]
