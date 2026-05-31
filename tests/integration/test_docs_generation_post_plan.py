"""Integration tests for post-plan doc generation (Plan 07 task_07_06).

These exercise the deterministic core the Technical Writer agent invokes at
plan close: :func:`api_server.tech_writer.generation.generate_plan_docs`.
There is **no live LLM call** here — the workflow fills the canonical
templates (task_07_07 changelog, task_07_08 ADR) from plan metadata, so the
test asserts files-on-disk, not model behaviour.

They run against a real temporary ``/docs`` tree (``tmp_path``) rather than
the DB: ``session_or_repo`` reduces, for the deterministic core, to the
docs-root path a worktree exposes. They are filed under ``tests/integration``
because the unit of behaviour is "writes the right files on disk".

Coverage:
  * happy path — changelog created with the right content; one ADR per
    declared decision created with the **next sequential** number;
  * idempotency — a second run creates no new files, allocates no new ADR
    number and does not clobber a human edit;
  * numbering — generation respects existing ADRs in the directory;
  * negative — passing the repo root (not the docs dir) still resolves;
    a missing decision set writes no ADR.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from api_server.docs_structure.language import split_frontmatter
from api_server.tech_writer.adr import ADR_FIRST_NUMBER, format_adr_number
from api_server.tech_writer.changelog import (
    ChangelogDecision,
    ChangelogTask,
    PlanMeta,
)
from api_server.tech_writer.generation import (
    ADR_FOLDER,
    CHANGELOG_FOLDER,
    REFERENCE_FOLDER,
    REFERENCE_UPDATE_NOTE,
    DecisionSpec,
    generate_plan_docs,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------
def _sample_plan_meta(**overrides: object) -> PlanMeta:
    """A realistic closed-plan meta; override per-test as needed."""
    base: dict[str, object] = {
        "plan_id": "07-documentacion-visor",
        "title": "Documentación Estructurada y Visor Cross-Proyecto",
        "summary": (
            "Estructura canónica /docs en 7 carpetas obligatorias. El "
            "Technical Writer agente genera la documentación al cierre del plan."
        ),
        "tasks": (
            ChangelogTask("task_07_06", "Workflow automático al cierre del plan"),
            ChangelogTask("task_07_07", "Plantilla canónica de changelog por plan"),
        ),
        "decisions": (ChangelogDecision("Generación post-plan determinista", adr="ADR 0031"),),
        "pr_url": "https://github.com/acme/agentic-platform/pull/77",
        "completed_at": "2026-06-15",
        "docs_language": "es",
    }
    base.update(overrides)
    return PlanMeta(**base)  # type: ignore[arg-type]


def _sample_decision(**overrides: object) -> DecisionSpec:
    base: dict[str, object] = {
        "slug": "docs-generation-post-plan",
        "title": "Generación de documentación post-plan determinista",
        "context": "Al cerrar un plan hay que materializar changelog + ADRs.",
        "decision": "Una función determinista rellena las plantillas canónicas.",
        "consequences": "Salida reproducible; las puertas de Fase A siempre pasan.",
    }
    base.update(overrides)
    return DecisionSpec(**base)  # type: ignore[arg-type]


def _seed_existing_adrs(adr_dir: Path, numbers: list[int]) -> None:
    """Drop conforming ``NNNN-slug.md`` files so numbering has a baseline."""
    adr_dir.mkdir(parents=True, exist_ok=True)
    for n in numbers:
        padded = format_adr_number(n)
        (adr_dir / f"{padded}-prior-decision.md").write_text(
            f'---\nadr_id: "{padded}"\n---\n\n# prior\n',
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
def test_changelog_is_created_with_expected_content(tmp_path: Path) -> None:
    meta = _sample_plan_meta()
    manifest = generate_plan_docs(tmp_path, meta, decisions=())

    changelog = tmp_path / "docs" / CHANGELOG_FOLDER / f"{meta.plan_id}.md"
    assert changelog.is_file(), "changelog file must be created"

    text = changelog.read_text(encoding="utf-8")
    frontmatter, body = split_frontmatter(text)
    data = yaml.safe_load(frontmatter)
    assert data["plan_id"] == meta.plan_id
    assert data["title"] == meta.title
    assert data["docs_language"] == "es"
    assert body.lstrip().startswith("# Plan 07 —")
    assert "## Resumen" in text
    assert "- ✅ **`task_07_06`**" in text
    assert "https://github.com/acme/agentic-platform/pull/77" in text

    # Manifest reflects exactly the one written file.
    assert manifest.changed is True
    assert [w.kind for w in manifest.written] == ["changelog"]
    assert manifest.written[0].path.endswith(f"{CHANGELOG_FOLDER}/{meta.plan_id}.md")
    assert manifest.skipped == ()
    assert manifest.reference_note == REFERENCE_UPDATE_NOTE


def test_adr_is_created_with_next_sequential_number(tmp_path: Path) -> None:
    # Pre-seed ADRs 0001..0030 so the next free number is 0031.
    adr_dir = tmp_path / "docs" / ADR_FOLDER
    _seed_existing_adrs(adr_dir, list(range(1, 31)))

    meta = _sample_plan_meta()
    decision = _sample_decision()
    manifest = generate_plan_docs(tmp_path, meta, decisions=(decision,))

    expected = adr_dir / f"0031-{decision.slug}.md"
    assert expected.is_file(), "ADR must get the next sequential number 0031"

    text = expected.read_text(encoding="utf-8")
    frontmatter, body = split_frontmatter(text)
    data = yaml.safe_load(frontmatter)
    assert data["adr_id"] == "0031"
    assert data["plan_referenced"] == meta.plan_id
    assert data["docs_language"] == "es"
    assert body.lstrip().startswith("# ADR 0031 —")
    assert "## Contexto" in text
    assert "## Decisión" in text
    assert "## Consecuencias" in text

    adr_written = [w for w in manifest.written if w.kind == "adr"]
    assert len(adr_written) == 1
    assert adr_written[0].adr_number == 31


def test_first_adr_in_empty_dir_starts_at_first_number(tmp_path: Path) -> None:
    meta = _sample_plan_meta()
    decision = _sample_decision(slug="first-ever-decision")
    generate_plan_docs(tmp_path, meta, decisions=(decision,))

    adr_dir = tmp_path / "docs" / ADR_FOLDER
    expected = adr_dir / f"{format_adr_number(ADR_FIRST_NUMBER)}-{decision.slug}.md"
    assert expected.is_file()


def test_multiple_decisions_get_consecutive_numbers(tmp_path: Path) -> None:
    adr_dir = tmp_path / "docs" / ADR_FOLDER
    _seed_existing_adrs(adr_dir, [5])  # next free = 0006

    meta = _sample_plan_meta()
    decisions = (
        _sample_decision(slug="decision-uno", title="Uno"),
        _sample_decision(slug="decision-dos", title="Dos"),
    )
    manifest = generate_plan_docs(tmp_path, meta, decisions=decisions)

    assert (adr_dir / "0006-decision-uno.md").is_file()
    assert (adr_dir / "0007-decision-dos.md").is_file()
    numbers = sorted(w.adr_number for w in manifest.written if w.kind == "adr")
    assert numbers == [6, 7]


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------
def test_rerun_creates_no_duplicates(tmp_path: Path) -> None:
    meta = _sample_plan_meta()
    decision = _sample_decision()

    first = generate_plan_docs(tmp_path, meta, decisions=(decision,))
    assert first.changed is True
    assert len(first.written) == 2  # changelog + 1 ADR

    second = generate_plan_docs(tmp_path, meta, decisions=(decision,))
    assert second.changed is False, "a re-run must create nothing"
    assert second.written == ()
    skipped_kinds = sorted(s.kind for s in second.skipped)
    assert skipped_kinds == ["adr", "changelog"]

    # No duplicate ADR was minted: still exactly one ADR for this slug.
    adr_dir = tmp_path / "docs" / ADR_FOLDER
    matches = list(adr_dir.glob(f"*-{decision.slug}.md"))
    assert len(matches) == 1
    # And no second changelog under a different name.
    changelog_dir = tmp_path / "docs" / CHANGELOG_FOLDER
    assert len(list(changelog_dir.glob("*.md"))) == 1


def test_rerun_does_not_clobber_human_edited_changelog(tmp_path: Path) -> None:
    meta = _sample_plan_meta()
    generate_plan_docs(tmp_path, meta, decisions=())

    changelog = tmp_path / "docs" / CHANGELOG_FOLDER / f"{meta.plan_id}.md"
    edited = changelog.read_text(encoding="utf-8") + "\n\n## Nota humana\n\nEditado a mano.\n"
    changelog.write_text(edited, encoding="utf-8")

    manifest = generate_plan_docs(tmp_path, meta, decisions=())
    assert manifest.changed is False
    assert changelog.read_text(encoding="utf-8") == edited, "human edit must survive"


def test_rerun_does_not_reallocate_adr_number(tmp_path: Path) -> None:
    adr_dir = tmp_path / "docs" / ADR_FOLDER
    _seed_existing_adrs(adr_dir, list(range(1, 31)))  # next free = 0031

    meta = _sample_plan_meta()
    decision = _sample_decision()
    generate_plan_docs(tmp_path, meta, decisions=(decision,))
    # Second run: must match the existing 0031 by slug, NOT mint 0032.
    second = generate_plan_docs(tmp_path, meta, decisions=(decision,))

    assert not (adr_dir / f"0032-{decision.slug}.md").exists()
    assert (adr_dir / f"0031-{decision.slug}.md").is_file()
    assert [s.kind for s in second.skipped if s.kind == "adr"] == ["adr"]


# ---------------------------------------------------------------------------
# Path resolution + edges
# ---------------------------------------------------------------------------
def test_accepts_docs_dir_directly(tmp_path: Path) -> None:
    """Passing the docs dir itself must not double-nest into docs/docs."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    meta = _sample_plan_meta()
    generate_plan_docs(docs_dir, meta, decisions=())

    assert (docs_dir / CHANGELOG_FOLDER / f"{meta.plan_id}.md").is_file()
    assert not (docs_dir / "docs").exists()


def test_no_decisions_writes_no_adr(tmp_path: Path) -> None:
    meta = _sample_plan_meta()
    manifest = generate_plan_docs(tmp_path, meta, decisions=())

    adr_dir = tmp_path / "docs" / ADR_FOLDER
    # The ADR dir is not even created when there are no decisions.
    assert not adr_dir.exists()
    assert all(w.kind != "adr" for w in manifest.written)


def test_reference_folder_is_not_touched(tmp_path: Path) -> None:
    """Negative/no-op: reference updates are deliberately not generated."""
    meta = _sample_plan_meta()
    manifest = generate_plan_docs(tmp_path, meta, decisions=(_sample_decision(),))

    reference_dir = tmp_path / "docs" / REFERENCE_FOLDER
    assert not reference_dir.exists()
    assert "04-reference" in manifest.reference_note


def test_en_plan_yields_english_adr_and_changelog(tmp_path: Path) -> None:
    meta = _sample_plan_meta(docs_language="en")
    decision = _sample_decision(slug="english-decision")
    generate_plan_docs(tmp_path, meta, decisions=(decision,))

    changelog = (tmp_path / "docs" / CHANGELOG_FOLDER / f"{meta.plan_id}.md").read_text(
        encoding="utf-8"
    )
    assert "## Summary" in changelog
    assert "## Resumen" not in changelog

    adr_dir = tmp_path / "docs" / ADR_FOLDER
    adr = next(adr_dir.glob(f"*-{decision.slug}.md")).read_text(encoding="utf-8")
    assert "## Context" in adr
    assert "docs_language: en" in adr
