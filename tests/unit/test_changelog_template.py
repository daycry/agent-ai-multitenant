"""Unit tests for the per-plan changelog template (Plan 07 task_07_07).

Pure-text tests — no DB, no git, no clock. They pin the contract from the
task brief: rendering a sample ``PlanMeta`` yields valid frontmatter + all
sections + the task list; empty decisions omits that section; the
``docs_language`` is honoured.

Plus the supporting surface: the H1 short-id derivation, the PR
placeholder vs link, the partial-close glyphs, determinism, and the
language fallback for an unsupported declared language.
"""

from __future__ import annotations

import datetime

import pytest
import yaml
from api_server.docs_structure.language import SUPPORTED_LANGUAGES, split_frontmatter
from api_server.tech_writer.changelog import (
    DEFAULT_DOCS_LANGUAGE,
    SECTION_LABELS,
    ChangelogDecision,
    ChangelogTask,
    PlanMeta,
    render_changelog,
)

pytestmark = pytest.mark.unit


# --- fixtures --------------------------------------------------------------


def _sample_meta(**overrides: object) -> PlanMeta:
    """A realistic, fully-populated plan meta; override per-test as needed."""
    base: dict[str, object] = {
        "plan_id": "07-documentacion-visor",
        "title": "Documentación Estructurada y Visor Cross-Proyecto",
        "summary": (
            "Estructura canónica /docs en 7 carpetas obligatorias enforced por "
            "guardrails. El Technical Writer agente mantiene la documentación al "
            "cierre de cada plan."
        ),
        "tasks": (
            ChangelogTask("task_07_01", "Bootstrap de las 7 carpetas obligatorias"),
            ChangelogTask("task_07_07", "Plantilla canónica de changelog por plan"),
        ),
        "decisions": (
            ChangelogDecision("Estructura Diátaxis adaptada: 7 carpetas numeradas", adr="ADR 0030"),
        ),
        "pr_url": "https://github.com/acme/agentic-platform/pull/42",
        "completed_at": "2026-06-10",
        "docs_language": "es",
    }
    base.update(overrides)
    return PlanMeta(**base)  # type: ignore[arg-type]


# --- happy path: valid frontmatter + all sections + the task list ----------


def test_render_yields_parseable_frontmatter_with_four_keys() -> None:
    md = render_changelog(_sample_meta())
    frontmatter, body = split_frontmatter(md)
    assert frontmatter, "frontmatter block must be present and delimited by --- fences"

    data = yaml.safe_load(frontmatter)
    # Unquoted ISO dates round-trip through PyYAML as ``datetime.date`` —
    # exactly as the existing corpus entries are parsed by tooling.
    assert data == {
        "plan_id": "07-documentacion-visor",
        "title": "Documentación Estructurada y Visor Cross-Proyecto",
        "completed_at": datetime.date(2026, 6, 10),
        "docs_language": "es",
    }
    assert body.lstrip().startswith("# Plan 07 —")


def test_render_contains_all_sections_in_order() -> None:
    md = render_changelog(_sample_meta())
    labels = SECTION_LABELS["es"]

    # H1 with the short plan id (slug stripped) + title.
    assert "# Plan 07 — Documentación Estructurada y Visor Cross-Proyecto" in md

    idx_resumen = md.index(f"## {labels['resumen']}")
    idx_cambios = md.index(f"## {labels['cambios']}")
    idx_decisiones = md.index(f"## {labels['decisiones']}")
    idx_pr = md.index(f"## {labels['pr']}")

    # Canonical order: Resumen → Cambios → Decisiones → PR.
    assert idx_resumen < idx_cambios < idx_decisiones < idx_pr


def test_summary_text_is_rendered_under_resumen() -> None:
    meta = _sample_meta(summary="Un resumen breve del plan.")
    md = render_changelog(meta)
    assert "## Resumen\n\nUn resumen breve del plan.\n" in md


def test_task_list_renders_one_bullet_per_task() -> None:
    md = render_changelog(_sample_meta())
    assert "- ✅ **`task_07_01`** — Bootstrap de las 7 carpetas obligatorias" in md
    assert "- ✅ **`task_07_07`** — Plantilla canónica de changelog por plan" in md
    # Two task bullets, one per task.
    assert md.count("**`task_07_") == 2


def test_decisions_render_with_optional_adr_tag() -> None:
    meta = _sample_meta(
        decisions=(
            ChangelogDecision("Decisión con ADR", adr="ADR 0031"),
            ChangelogDecision("Decisión sin ADR"),
        )
    )
    md = render_changelog(meta)
    assert "- **ADR 0031** — Decisión con ADR" in md
    assert "- Decisión sin ADR" in md


def test_pr_link_is_rendered_when_present() -> None:
    md = render_changelog(_sample_meta(pr_url="https://example.test/pr/7"))
    assert "## PR" in md
    assert "- https://example.test/pr/7" in md


# --- edge: empty decisions omits that section ------------------------------


def test_empty_decisions_omits_section() -> None:
    md = render_changelog(_sample_meta(decisions=()))
    assert "## Decisiones" not in md
    # The other sections survive.
    assert "## Resumen" in md
    assert "## Cambios" in md
    assert "## PR" in md
    # Cambios is immediately followed by PR (no Decisiones between them).
    assert md.index("## Cambios") < md.index("## PR")


def test_missing_pr_url_renders_pending_placeholder() -> None:
    md = render_changelog(_sample_meta(pr_url=None))
    assert "## PR" in md
    assert f"- {SECTION_LABELS['es']['pending']}" in md
    assert "_pendiente_" in md


def test_completed_at_none_renders_yaml_null() -> None:
    md = render_changelog(_sample_meta(completed_at=None))
    frontmatter, _ = split_frontmatter(md)
    data = yaml.safe_load(frontmatter)
    # ``null`` in YAML parses back to None — matches the corpus in-flight style.
    assert data["completed_at"] is None
    assert "completed_at: null" in frontmatter


def test_no_tasks_renders_explicit_placeholder() -> None:
    md = render_changelog(_sample_meta(tasks=()))
    assert "## Cambios" in md
    assert SECTION_LABELS["es"]["no_tasks"] in md


def test_unfinished_task_renders_cross_glyph() -> None:
    meta = _sample_meta(
        tasks=(ChangelogTask("task_07_09", "Sincronización pendiente", done=False),)
    )
    md = render_changelog(meta)
    assert "- ❌ **`task_07_09`** — Sincronización pendiente" in md
    assert "✅" not in md


# --- docs_language is honoured ---------------------------------------------


def test_docs_language_en_uses_english_headings() -> None:
    meta = _sample_meta(docs_language="en")
    md = render_changelog(meta)
    frontmatter, _ = split_frontmatter(md)
    assert "docs_language: en" in frontmatter

    en = SECTION_LABELS["en"]
    assert f"## {en['resumen']}" in md  # "Summary"
    assert f"## {en['cambios']}" in md  # "Changes"
    assert f"## {en['decisiones']}" in md  # "Decisions"
    # Spanish headings must NOT leak in.
    assert "## Resumen" not in md
    assert "## Cambios" not in md


def test_docs_language_en_pending_placeholder_is_english() -> None:
    md = render_changelog(_sample_meta(docs_language="en", pr_url=None))
    assert "_pending_" in md
    assert "_pendiente_" not in md


@pytest.mark.parametrize("declared", ["ES", " es ", "Es"])
def test_docs_language_is_normalised(declared: str) -> None:
    md = render_changelog(_sample_meta(docs_language=declared))
    frontmatter, _ = split_frontmatter(md)
    assert "docs_language: es" in frontmatter


# --- negative: unsupported language falls back to the corpus default -------


def test_unsupported_language_falls_back_to_default() -> None:
    md = render_changelog(_sample_meta(docs_language="fr"))
    frontmatter, _ = split_frontmatter(md)
    assert f"docs_language: {DEFAULT_DOCS_LANGUAGE}" in frontmatter
    # Default is one of the supported set, and the Spanish headings render.
    assert DEFAULT_DOCS_LANGUAGE in SUPPORTED_LANGUAGES
    assert "## Resumen" in md


# --- determinism -----------------------------------------------------------


def test_render_is_deterministic() -> None:
    meta = _sample_meta()
    assert render_changelog(meta) == render_changelog(meta)


def test_render_ends_with_single_trailing_newline() -> None:
    md = render_changelog(_sample_meta())
    assert md.endswith("\n")
    assert not md.endswith("\n\n")


def test_task_and_decision_order_is_preserved() -> None:
    meta = _sample_meta(
        tasks=(
            ChangelogTask("task_b", "segunda"),
            ChangelogTask("task_a", "primera"),
        ),
        decisions=(
            ChangelogDecision("zeta"),
            ChangelogDecision("alfa"),
        ),
    )
    md = render_changelog(meta)
    assert md.index("task_b") < md.index("task_a")
    assert md.index("zeta") < md.index("alfa")
