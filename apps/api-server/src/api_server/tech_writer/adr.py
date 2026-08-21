"""Canonical ADR template + sequential numbering (task_07_08).

Architecture Decision Records live under ``docs/05-architecture-decisions/``
and are numbered sequentially with a zero-padded ``NNNN-`` filename prefix
(``0030-catalog-ingestion-build-time-seed.md``). Plan 07 Fase B institutes
ADRs as a *plan deliverable*: when a plan records a decision, the Technical
Writer drops a new ADR. This module owns the **deterministic** renderer that
fills the canonical template — :func:`render_adr` — plus the helper that
allocates the next free number — :func:`next_adr_number` — so two ADRs never
collide on a number.

The template mirrors the *existing* hand-written records (e.g. ``0029`` /
``0030``):

  * YAML frontmatter with the seven keys every current record carries —
    ``adr_id`` (a **quoted, zero-padded** string so PyYAML keeps the
    leading zeros), ``title``, ``status``, ``date``, ``authors`` (a YAML
    flow list), ``plan_referenced`` and ``docs_language``;
  * an ``# ADR NNNN — {title}`` H1;
  * the three canonical sections ``## Contexto`` / ``## Decisión`` /
    ``## Consecuencias`` (bilingual per ``docs_language``);
  * an optional ``## Alternativas consideradas`` and an optional
    ``## Referencias``, each omitted entirely when empty — matching the
    corpus, where these tail sections appear only when there is content.

Determinism contract: same :class:`AdrMeta` in ⇒ byte-identical Markdown
out. No clock, no randomness, no I/O in :func:`render_adr`. The sole I/O is
:func:`next_adr_number`, which only *reads* a directory listing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from api_server.docs_structure.language import SUPPORTED_LANGUAGES, Language

# --- numbering (named, not magic) ------------------------------------------

#: Width of the zero-padded ADR number prefix (``0001``, …, ``0030``). The
#: corpus is uniformly four digits; keep this as the ONE definition so the
#: renderer's stem and the next-number allocator agree.
ADR_NUMBER_WIDTH: int = 4

#: The first ADR number when a directory holds none yet. ADRs are 1-based
#: (the corpus starts at ``0001``); an empty dir therefore allocates this.
ADR_FIRST_NUMBER: int = 1

#: Matches a canonical ADR filename and captures its leading number, e.g.
#: ``0030-catalog-ingestion-build-time-seed.md`` → ``0030``. The number must
#: be followed by a ``-`` (the slug separator) so a stray ``README.md`` or a
#: non-conforming name is ignored by :func:`next_adr_number`.
_ADR_FILENAME_RE = re.compile(rf"^(\d{{{ADR_NUMBER_WIDTH},}})-.+\.md$")

# --- section headings (named, not magic) -----------------------------------

#: The default document language when an ADR declares none / an unsupported
#: one. ``es`` is the corpus-wide convention (every current record is ``es``).
DEFAULT_DOCS_LANGUAGE: str = Language.ES.value

#: Per-language section labels, keyed by the canonical section id. Kept in
#: ONE place so the renderer and any future parity check cannot disagree.
#: The Spanish wording matches the existing corpus verbatim.
SECTION_LABELS: dict[str, dict[str, str]] = {
    Language.ES.value: {
        "contexto": "Contexto",
        "decision": "Decisión",
        "consecuencias": "Consecuencias",
        "alternativas": "Alternativas consideradas",
        "referencias": "Referencias",
    },
    Language.EN.value: {
        "contexto": "Context",
        "decision": "Decision",
        "consecuencias": "Consequences",
        "alternativas": "Alternatives considered",
        "referencias": "References",
    },
}

#: The default ADR ``status`` for a freshly minted record. The corpus marks
#: agreed decisions ``accepted``; the workflow may override (e.g. ``proposed``).
DEFAULT_ADR_STATUS: str = "accepted"


@dataclass(frozen=True)
class AdrMeta:
    """Everything :func:`render_adr` needs to materialise an ADR.

    This is the deterministic *input contract*. The post-plan workflow
    (task_07_06) builds it from the decision metadata + the number returned
    by :func:`next_adr_number`. No field is read from the clock, the network
    or the disk by the renderer.

    Attributes:
        number: The sequential ADR number (an ``int``; zero-padded on
            render). Allocate it with :func:`next_adr_number`.
        title: The decision title, used in the H1 and the frontmatter.
        slug: The filename slug (no number, no extension), e.g.
            ``"adr-template-sequential-numbering"``. Combined with the
            zero-padded number it yields the filename stem.
        context: Free text for ``## Contexto`` — what problem / forces.
        decision: Free text for ``## Decisión`` — the chosen path.
        consequences: Free text for ``## Consecuencias`` — trade-offs.
        alternatives: Optional bullets for ``## Alternativas consideradas``;
            the section is omitted entirely when empty.
        references: Optional bullets for ``## Referencias``; the section is
            omitted entirely when empty.
        status: ADR lifecycle status (defaults to :data:`DEFAULT_ADR_STATUS`).
        date: ISO date string for the frontmatter, or ``None`` (rendered as
            YAML ``null`` — the caller supplies the close date deterministically).
        authors: Frontmatter authors (a tuple), defaulting to the corpus's
            ``system_architect``.
        plan_referenced: The plan id the decision belongs to, or ``None``.
        rejects: Roadmap ``plan_id`` / ``task_id`` values whose checkboxes this
            decision INVALIDATES (``task_gov_01``). Optional and omitted from
            the frontmatter when empty — most ADRs reject nothing, and
            ``rejects: []`` across the corpus would be noise nobody reads. When
            present, ``tests/docs/test_adr_precedence.py`` demands the id
            exists, that the checkbox is closed ``[x]``, and that the rejected
            document cites this ADR back.
        docs_language: ``"es"`` or ``"en"``; anything else falls back to
            :data:`DEFAULT_DOCS_LANGUAGE`.
    """

    number: int
    title: str
    slug: str
    context: str
    decision: str
    consequences: str
    alternatives: tuple[str, ...] = field(default_factory=tuple)
    references: tuple[str, ...] = field(default_factory=tuple)
    status: str = DEFAULT_ADR_STATUS
    date: str | None = None
    authors: tuple[str, ...] = ("system_architect",)
    plan_referenced: str | None = None
    rejects: tuple[str, ...] = field(default_factory=tuple)
    docs_language: str = DEFAULT_DOCS_LANGUAGE


def format_adr_number(number: int) -> str:
    """Zero-pad an ADR number to the canonical width (``31`` → ``"0031"``).

    Numbers wider than :data:`ADR_NUMBER_WIDTH` are returned as-is (no
    truncation) so the scheme keeps working past ``9999`` without silently
    corrupting an id.
    """
    return str(number).zfill(ADR_NUMBER_WIDTH)


def adr_filename_stem(meta: AdrMeta) -> str:
    """The filename stem (no extension): ``"0031-{slug}"``."""
    return f"{format_adr_number(meta.number)}-{meta.slug}"


def next_adr_number(adr_dir: Path) -> int:
    """Return the next free ADR number for ``adr_dir``.

    Scans the directory for files matching the canonical
    ``NNNN-<slug>.md`` pattern, takes the highest leading number and returns
    it ``+ 1``. A directory that does not exist or holds no conforming ADR
    (only a ``README.md``, say) starts at :data:`ADR_FIRST_NUMBER` (``1``),
    so the first ADR ever created is ``0001``.

    This is the sole I/O in the module and it is read-only — it lists the
    directory but never writes. It does not parse file *contents*: the
    number lives in the filename by convention, so allocation is cheap and
    cannot be fooled by a malformed frontmatter.

    Args:
        adr_dir: The ADR directory (``docs/05-architecture-decisions``).

    Returns:
        The next sequential number as an ``int`` (zero-pad with
        :func:`format_adr_number` for display).
    """
    highest = ADR_FIRST_NUMBER - 1
    if adr_dir.is_dir():
        for entry in adr_dir.iterdir():
            if not entry.is_file():
                continue
            match = _ADR_FILENAME_RE.match(entry.name)
            if match is None:
                continue
            highest = max(highest, int(match.group(1)))
    return highest + 1


def _resolve_language(docs_language: str | None) -> str:
    """Normalise ``docs_language`` to a supported value with a safe default.

    Lower-cases and strips the input; returns it when supported, otherwise
    :data:`DEFAULT_DOCS_LANGUAGE`. Never raises — an odd value degrades to
    the corpus default rather than breaking generation.
    """
    if docs_language is None:
        return DEFAULT_DOCS_LANGUAGE
    normalised = docs_language.strip().lower()
    if normalised in SUPPORTED_LANGUAGES:
        return normalised
    return DEFAULT_DOCS_LANGUAGE


def _frontmatter(meta: AdrMeta, language: str, padded: str) -> str:
    """Render the YAML frontmatter block in the corpus key order.

    ``adr_id`` is **quoted** so PyYAML preserves the leading zeros (an
    unquoted ``0031`` would round-trip as the integer ``31``). ``authors``
    is a YAML flow list. ``date`` and ``plan_referenced`` render as YAML
    ``null`` when absent.

    ``rejects`` (``task_gov_01``) es la única clave que se **omite entera** en
    vez de renderizarse ``null`` cuando está vacía — el mismo criterio que
    gobierna las secciones de cola opcionales. Va justo detrás de
    ``plan_referenced``, que es donde el lector ya tiene delante la relación
    con el roadmap.
    """
    authors = ", ".join(meta.authors)
    date = meta.date if meta.date is not None else "null"
    plan = meta.plan_referenced if meta.plan_referenced is not None else "null"
    rejects = f"rejects: [{', '.join(meta.rejects)}]\n" if meta.rejects else ""
    return (
        "---\n"
        f'adr_id: "{padded}"\n'
        f"title: {meta.title}\n"
        f"status: {meta.status}\n"
        f"date: {date}\n"
        f"authors: [{authors}]\n"
        f"plan_referenced: {plan}\n"
        f"{rejects}"
        f"docs_language: {language}\n"
        "---\n"
    )


def _bullets(items: tuple[str, ...]) -> list[str]:
    """Render ``items`` as Markdown bullets (one ``- `` line each)."""
    return [f"- {item}" for item in items]


def render_adr(adr_meta: AdrMeta) -> str:
    """Render a canonical ADR Markdown document.

    Deterministic: the same :class:`AdrMeta` always yields byte-identical
    output. The structure matches the existing
    ``docs/05-architecture-decisions/`` records — frontmatter with a
    zero-padded ``adr_id``, an ``# ADR NNNN — {title}`` H1, then
    ``## Contexto`` / ``## Decisión`` / ``## Consecuencias``, plus an
    optional ``## Alternativas consideradas`` and ``## Referencias`` (each
    omitted when empty).

    Args:
        adr_meta: The ADR metadata (see :class:`AdrMeta`). Allocate
            ``number`` with :func:`next_adr_number`.

    Returns:
        The full Markdown document as a single string, ending in a trailing
        newline.
    """
    language = _resolve_language(adr_meta.docs_language)
    labels = SECTION_LABELS[language]
    padded = format_adr_number(adr_meta.number)

    parts: list[str] = [
        _frontmatter(adr_meta, language, padded),
        "",
        f"# ADR {padded} — {adr_meta.title}",
        "",
        f"## {labels['contexto']}",
        "",
        adr_meta.context.strip(),
        "",
        f"## {labels['decision']}",
        "",
        adr_meta.decision.strip(),
        "",
        f"## {labels['consecuencias']}",
        "",
        adr_meta.consequences.strip(),
        "",
    ]

    # Tail sections are omitted entirely when empty — the corpus norm.
    if adr_meta.alternatives:
        parts.append(f"## {labels['alternativas']}")
        parts.append("")
        parts.extend(_bullets(adr_meta.alternatives))
        parts.append("")

    if adr_meta.references:
        parts.append(f"## {labels['referencias']}")
        parts.append("")
        parts.extend(_bullets(adr_meta.references))
        parts.append("")

    return "\n".join(parts)


__all__ = [
    "ADR_FIRST_NUMBER",
    "ADR_NUMBER_WIDTH",
    "DEFAULT_ADR_STATUS",
    "DEFAULT_DOCS_LANGUAGE",
    "SECTION_LABELS",
    "AdrMeta",
    "adr_filename_stem",
    "format_adr_number",
    "next_adr_number",
    "render_adr",
]
