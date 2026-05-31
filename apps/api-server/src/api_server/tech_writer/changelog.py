"""Canonical per-plan changelog template + renderer (task_07_07).

Plan 07 Fase B institutionalises documentation as a *plan deliverable*: at
plan close the Technical Writer drops one entry under
``docs/07-changelog/{plan_id}.md``. This module owns the **deterministic**
renderer that fills the canonical template from plan metadata — the
post-plan generation workflow (task_07_06) calls :func:`render_changelog`
rather than asking an LLM to free-form the file, so the structure is
guaranteed and the output is reproducible (no clock, no randomness, no
I/O).

The template mirrors the *existing* hand-written entries in
``docs/07-changelog/`` (e.g. ``06.14``, ``06.12``):

  * YAML frontmatter with ``plan_id``, ``title``, ``completed_at`` and
    ``docs_language`` (the four keys every current entry carries);
  * an ``# Plan {short} — {title}`` H1, where ``{short}`` is the plan id
    with its trailing ``-slug`` stripped (``06.14-hardening-auditoria`` →
    ``06.14``), matching the corpus;
  * ``## Resumen`` — the free-text summary;
  * ``## Cambios`` — one bullet per plan task (``task_key`` + title);
  * ``## Decisiones`` — one bullet per recorded decision, **omitted
    entirely** when there are none (a near-universal case in the corpus);
  * ``## PR`` — a pull-request link, rendered as a ``_pendiente_``
    placeholder when the URL is not yet known at generation time.

Determinism contract: same ``PlanMeta`` in ⇒ byte-identical Markdown out.
Tasks and decisions are rendered in the order given (the caller — the
roadmap parser — already yields them in plan order); we do not sort,
dedupe or inject timestamps.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from api_server.docs_structure.language import SUPPORTED_LANGUAGES, Language

# --- section headings (named, not magic) -----------------------------------
# Bilingual labels keyed by docs_language so a project configured ``en``
# (CLAUDE.md principle 12: es + en only) gets an English changelog. The
# Spanish wording matches the existing corpus verbatim.

#: The default document language when a plan declares none / an unsupported
#: one. ``es`` is the corpus-wide convention (every current entry is ``es``).
DEFAULT_DOCS_LANGUAGE: str = Language.ES.value

#: Per-language section labels. The keys are the canonical section ids; the
#: values are the on-page headings. Kept in ONE place so the renderer and
#: any future consumer (e.g. a parity check) cannot disagree.
SECTION_LABELS: dict[str, dict[str, str]] = {
    Language.ES.value: {
        "resumen": "Resumen",
        "cambios": "Cambios",
        "decisiones": "Decisiones",
        "pr": "PR",
        "plan": "Plan",
        "pending": "_pendiente_",
        "no_tasks": "_(sin tareas registradas)_",
    },
    Language.EN.value: {
        "resumen": "Summary",
        "cambios": "Changes",
        "decisiones": "Decisions",
        "pr": "PR",
        "plan": "Plan",
        "pending": "_pending_",
        "no_tasks": "_(no tasks recorded)_",
    },
}


@dataclass(frozen=True)
class ChangelogTask:
    """One plan task as rendered in the ``## Cambios`` section.

    ``task_key`` is the stable id (``task_07_07``); ``title`` is the
    human-readable one-liner from the roadmap checkbox. ``done`` records
    whether the roadmap checkbox was ticked — surfaced as a check/cross
    glyph so a partial close is legible, defaulting to ``True`` because a
    changelog is normally generated for a finished plan.
    """

    task_key: str
    title: str
    done: bool = True


@dataclass(frozen=True)
class ChangelogDecision:
    """One decision recorded for the plan, rendered in ``## Decisiones``.

    ``title`` is the decision in one line; ``adr`` optionally references
    the ADR that captures it (e.g. ``"ADR 0031"``) so the changelog links
    back to the full record produced by task_07_08.
    """

    title: str
    adr: str | None = None


@dataclass(frozen=True)
class PlanMeta:
    """Everything :func:`render_changelog` needs about a closed plan.

    This is the deterministic *input contract*: the post-plan workflow
    (task_07_06) builds it from the roadmap frontmatter + task checkboxes
    and the merged PR url. No method here touches the clock, the network
    or the disk.

    Attributes:
        plan_id: The plan identifier, e.g. ``"07-documentacion-visor"``.
        title: The plan's human-readable title.
        summary: Free-text paragraph(s) for ``## Resumen`` (Markdown ok).
        tasks: Plan tasks in plan order; rendered under ``## Cambios``.
        decisions: Recorded decisions; ``## Decisiones`` is omitted when
            this is empty.
        pr_url: URL of the merged plan PR, or ``None`` when not yet known
            (rendered as a pending placeholder).
        completed_at: ISO date string or ``None`` (frontmatter mirrors the
            corpus, where in-flight entries carry ``completed_at: null``).
        docs_language: ``"es"`` or ``"en"``; anything else falls back to
            :data:`DEFAULT_DOCS_LANGUAGE`.
    """

    plan_id: str
    title: str
    summary: str
    tasks: tuple[ChangelogTask, ...] = field(default_factory=tuple)
    decisions: tuple[ChangelogDecision, ...] = field(default_factory=tuple)
    pr_url: str | None = None
    completed_at: str | None = None
    docs_language: str = DEFAULT_DOCS_LANGUAGE


def _short_plan_id(plan_id: str) -> str:
    """Derive the H1's short id from a plan id.

    The corpus titles each entry ``# Plan {short} — …`` where ``{short}``
    is the leading numeric id with the trailing ``-slug`` removed
    (``06.14-hardening-auditoria`` → ``06.14``, ``07-documentacion-visor``
    → ``07``). A plan id with no slug is returned unchanged.
    """
    return plan_id.split("-", 1)[0]


def _resolve_language(docs_language: str | None) -> str:
    """Normalise ``docs_language`` to a supported value with a safe default.

    Lower-cases and strips the input; returns it when it is one of
    :data:`~api_server.docs_structure.language.SUPPORTED_LANGUAGES`,
    otherwise :data:`DEFAULT_DOCS_LANGUAGE`. Never raises — an odd value
    degrades to the corpus default rather than breaking generation.
    """
    if docs_language is None:
        return DEFAULT_DOCS_LANGUAGE
    normalised = docs_language.strip().lower()
    if normalised in SUPPORTED_LANGUAGES:
        return normalised
    return DEFAULT_DOCS_LANGUAGE


def _frontmatter(meta: PlanMeta, language: str) -> str:
    """Render the YAML frontmatter block.

    Four keys, in the corpus order: ``plan_id``, ``title``,
    ``completed_at`` (``null`` when absent, exactly as the corpus writes
    in-flight entries) and ``docs_language``.
    """
    completed = meta.completed_at if meta.completed_at is not None else "null"
    return (
        "---\n"
        f"plan_id: {meta.plan_id}\n"
        f"title: {meta.title}\n"
        f"completed_at: {completed}\n"
        f"docs_language: {language}\n"
        "---\n"
    )


def _render_task_line(task: ChangelogTask) -> str:
    """One ``## Cambios`` bullet: status glyph + ``task_key`` + title."""
    glyph = "✅" if task.done else "❌"
    return f"- {glyph} **`{task.task_key}`** — {task.title}"


def _render_decision_line(decision: ChangelogDecision) -> str:
    """One ``## Decisiones`` bullet: title, optionally tagged with the ADR."""
    if decision.adr:
        return f"- **{decision.adr}** — {decision.title}"
    return f"- {decision.title}"


def render_changelog(plan_meta: PlanMeta) -> str:
    """Render a canonical per-plan changelog Markdown document.

    Deterministic: the same :class:`PlanMeta` always yields byte-identical
    output. The structure matches the existing ``docs/07-changelog/``
    entries — frontmatter, ``# Plan {short} — {title}`` H1, then
    ``## Resumen``, ``## Cambios`` (one bullet per task), an optional
    ``## Decisiones`` (omitted when there are no decisions) and a ``## PR``
    section with the merged-PR link or a pending placeholder.

    Args:
        plan_meta: The plan metadata (see :class:`PlanMeta`).

    Returns:
        The full Markdown document as a single string, ending in a trailing
        newline.
    """
    language = _resolve_language(plan_meta.docs_language)
    labels = SECTION_LABELS[language]
    short = _short_plan_id(plan_meta.plan_id)

    parts: list[str] = [
        _frontmatter(plan_meta, language),
        "",
        f"# {labels['plan']} {short} — {plan_meta.title}",
        "",
        f"## {labels['resumen']}",
        "",
        plan_meta.summary.strip(),
        "",
        f"## {labels['cambios']}",
        "",
    ]

    if plan_meta.tasks:
        parts.extend(_render_task_line(task) for task in plan_meta.tasks)
    else:
        parts.append(labels["no_tasks"])
    parts.append("")

    # ``## Decisiones`` is omitted entirely when there are no decisions —
    # this is the corpus norm (most entries record none).
    if plan_meta.decisions:
        parts.append(f"## {labels['decisiones']}")
        parts.append("")
        parts.extend(_render_decision_line(d) for d in plan_meta.decisions)
        parts.append("")

    parts.append(f"## {labels['pr']}")
    parts.append("")
    if plan_meta.pr_url:
        parts.append(f"- {plan_meta.pr_url}")
    else:
        parts.append(f"- {labels['pending']}")
    parts.append("")

    return "\n".join(parts)


__all__ = [
    "DEFAULT_DOCS_LANGUAGE",
    "SECTION_LABELS",
    "ChangelogDecision",
    "ChangelogTask",
    "PlanMeta",
    "render_changelog",
]
