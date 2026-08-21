"""Unit tests for the canonical ADR template + numbering (Plan 07 task_07_08).

Pure tests — :func:`render_adr` touches no I/O, and :func:`next_adr_number`
only *reads* a ``tmp_path`` directory listing (no DB, no git, no clock). They
pin the contract from the task brief:

  * ``next_adr_number`` returns ``max + 1`` (e.g. ``0030`` present → ``0031``),
    zero-padded on render;
  * an empty / non-existent dir starts at ``0001``;
  * the rendered ADR has the right filename stem + the canonical sections.

Plus the supporting surface: frontmatter parseability + key order, the
optional tail sections, the language fallback, and determinism.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from api_server.docs_structure.language import SUPPORTED_LANGUAGES, split_frontmatter
from api_server.tech_writer.adr import (
    ADR_FIRST_NUMBER,
    DEFAULT_ADR_STATUS,
    DEFAULT_DOCS_LANGUAGE,
    SECTION_LABELS,
    AdrMeta,
    adr_filename_stem,
    format_adr_number,
    next_adr_number,
    render_adr,
)

pytestmark = pytest.mark.unit


# --- fixtures --------------------------------------------------------------


def _sample_meta(**overrides: object) -> AdrMeta:
    """A realistic, fully-populated ADR meta; override per-test as needed."""
    base: dict[str, object] = {
        "number": 31,
        "title": "Plantilla canónica de ADR numerado secuencialmente",
        "slug": "adr-template-sequential-numbering",
        "context": (
            "Los ADR viven bajo docs/05-architecture-decisions/ y se numeran "
            "secuencialmente. Hacía falta una plantilla determinista."
        ),
        "decision": "Un render_adr(adr_meta) determinista que rellena la plantilla.",
        "consequences": "Los ADR nunca colisionan de número y la estructura es fija.",
        "alternatives": ("Free-form por LLM (descartado: no determinista).",),
        "references": ("ADR 0030 — corpus de referencia de estilo.",),
        "status": "accepted",
        "date": "2026-05-29",
        "authors": ("system_architect",),
        "plan_referenced": "07-documentacion-visor",
        "docs_language": "es",
    }
    base.update(overrides)
    return AdrMeta(**base)  # type: ignore[arg-type]


def _touch_adr(adr_dir: Path, name: str) -> None:
    """Create a stub file ``name`` in ``adr_dir`` (parents created)."""
    adr_dir.mkdir(parents=True, exist_ok=True)
    (adr_dir / name).write_text("---\n---\n", encoding="utf-8")


# --- next_adr_number: max + 1, zero-padded ---------------------------------


def test_next_number_is_max_plus_one(tmp_path: Path) -> None:
    # 0030 present -> next is 0031.
    _touch_adr(tmp_path, "0029-platform-tenant.md")
    _touch_adr(tmp_path, "0030-catalog-ingestion.md")
    assert next_adr_number(tmp_path) == 31
    assert format_adr_number(next_adr_number(tmp_path)) == "0031"


def test_next_number_ignores_non_adr_files(tmp_path: Path) -> None:
    # README.md and a number-less file must NOT count toward the max.
    _touch_adr(tmp_path, "0030-catalog-ingestion.md")
    _touch_adr(tmp_path, "README.md")
    _touch_adr(tmp_path, "notes.md")
    _touch_adr(tmp_path, "draft.txt")
    assert next_adr_number(tmp_path) == 31


def test_next_number_picks_the_highest_not_the_last(tmp_path: Path) -> None:
    # Out-of-order on disk: the max wins, not lexical / creation order.
    _touch_adr(tmp_path, "0005-five.md")
    _touch_adr(tmp_path, "0042-forty-two.md")
    _touch_adr(tmp_path, "0013-thirteen.md")
    assert next_adr_number(tmp_path) == 43


def test_next_number_ignores_subdirectories(tmp_path: Path) -> None:
    _touch_adr(tmp_path, "0007-seven.md")
    # A directory whose name *looks* like an ADR must be skipped.
    (tmp_path / "0099-a-directory.md").mkdir()
    assert next_adr_number(tmp_path) == 8


# --- empty / missing dir starts at 0001 ------------------------------------


def test_empty_dir_starts_at_first_number(tmp_path: Path) -> None:
    assert next_adr_number(tmp_path) == ADR_FIRST_NUMBER == 1
    assert format_adr_number(next_adr_number(tmp_path)) == "0001"


def test_dir_with_only_readme_starts_at_first_number(tmp_path: Path) -> None:
    _touch_adr(tmp_path, "README.md")
    assert next_adr_number(tmp_path) == 1


def test_missing_dir_starts_at_first_number(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    assert not missing.exists()
    assert next_adr_number(missing) == 1


# --- format_adr_number: zero-padded ----------------------------------------


@pytest.mark.parametrize(
    ("number", "expected"),
    [(1, "0001"), (31, "0031"), (999, "0999"), (1234, "1234")],
)
def test_format_adr_number_zero_pads(number: int, expected: str) -> None:
    assert format_adr_number(number) == expected


def test_format_adr_number_does_not_truncate_wide_numbers() -> None:
    # Past the canonical width we widen rather than corrupt the id.
    assert format_adr_number(12345) == "12345"


# --- filename stem ---------------------------------------------------------


def test_filename_stem_is_padded_number_dash_slug() -> None:
    meta = _sample_meta(number=31, slug="adr-template-sequential-numbering")
    assert adr_filename_stem(meta) == "0031-adr-template-sequential-numbering"


# --- render_adr: frontmatter + sections ------------------------------------


def test_render_yields_parseable_frontmatter_with_seven_keys() -> None:
    md = render_adr(_sample_meta())
    frontmatter, body = split_frontmatter(md)
    assert frontmatter, "frontmatter block must be present and --- delimited"

    data = yaml.safe_load(frontmatter)
    assert data == {
        # Quoted in the source so the leading zeros survive: a STRING, not int 31.
        "adr_id": "0031",
        "title": "Plantilla canónica de ADR numerado secuencialmente",
        "status": "accepted",
        # Unquoted ISO date round-trips as datetime.date, like the corpus.
        "date": __import__("datetime").date(2026, 5, 29),
        "authors": ["system_architect"],
        "plan_referenced": "07-documentacion-visor",
        "docs_language": "es",
    }
    assert isinstance(data["adr_id"], str)
    assert body.lstrip().startswith("# ADR 0031 —")


def test_render_h1_carries_padded_number_and_title() -> None:
    md = render_adr(_sample_meta())
    assert "# ADR 0031 — Plantilla canónica de ADR numerado secuencialmente" in md


def test_render_contains_three_canonical_sections_in_order() -> None:
    md = render_adr(_sample_meta())
    labels = SECTION_LABELS["es"]
    idx_ctx = md.index(f"## {labels['contexto']}")
    idx_dec = md.index(f"## {labels['decision']}")
    idx_con = md.index(f"## {labels['consecuencias']}")
    assert idx_ctx < idx_dec < idx_con


def test_section_bodies_are_rendered() -> None:
    meta = _sample_meta(
        context="Contexto X.",
        decision="Decisión Y.",
        consequences="Consecuencia Z.",
    )
    md = render_adr(meta)
    assert "## Contexto\n\nContexto X.\n" in md
    assert "## Decisión\n\nDecisión Y.\n" in md
    assert "## Consecuencias\n\nConsecuencia Z.\n" in md


def test_optional_sections_render_when_present() -> None:
    md = render_adr(_sample_meta())
    assert "## Alternativas consideradas" in md
    assert "- Free-form por LLM (descartado: no determinista)." in md
    assert "## Referencias" in md
    assert "- ADR 0030 — corpus de referencia de estilo." in md


def test_default_status_is_accepted() -> None:
    meta = _sample_meta()
    md = render_adr(meta)
    assert "status: accepted" in md
    assert (
        AdrMeta(  # default kicks in when omitted
            number=1, title="t", slug="s", context="c", decision="d", consequences="x"
        ).status
        == DEFAULT_ADR_STATUS
    )


# --- edge: optional sections omitted when empty ----------------------------


def test_empty_alternatives_omits_section() -> None:
    md = render_adr(_sample_meta(alternatives=()))
    assert "## Alternativas consideradas" not in md
    # The mandatory sections survive.
    assert "## Contexto" in md
    assert "## Consecuencias" in md


def test_empty_references_omits_section() -> None:
    md = render_adr(_sample_meta(references=()))
    assert "## Referencias" not in md
    assert "## Consecuencias" in md


def test_no_optional_sections_renders_only_the_three() -> None:
    md = render_adr(_sample_meta(alternatives=(), references=()))
    assert "## Alternativas consideradas" not in md
    assert "## Referencias" not in md
    assert md.count("## ") == 3


def test_date_none_renders_yaml_null() -> None:
    md = render_adr(_sample_meta(date=None))
    frontmatter, _ = split_frontmatter(md)
    data = yaml.safe_load(frontmatter)
    assert data["date"] is None
    assert "date: null" in frontmatter


def test_plan_referenced_none_renders_yaml_null() -> None:
    md = render_adr(_sample_meta(plan_referenced=None))
    frontmatter, _ = split_frontmatter(md)
    data = yaml.safe_load(frontmatter)
    assert data["plan_referenced"] is None


def test_multiple_authors_render_as_yaml_flow_list() -> None:
    md = render_adr(_sample_meta(authors=("system_architect", "ai-engineer")))
    frontmatter, _ = split_frontmatter(md)
    data = yaml.safe_load(frontmatter)
    assert data["authors"] == ["system_architect", "ai-engineer"]


# --- docs_language is honoured ---------------------------------------------


def test_docs_language_en_uses_english_headings() -> None:
    md = render_adr(_sample_meta(docs_language="en"))
    frontmatter, _ = split_frontmatter(md)
    assert "docs_language: en" in frontmatter

    en = SECTION_LABELS["en"]
    assert f"## {en['contexto']}" in md  # "Context"
    assert f"## {en['decision']}" in md  # "Decision"
    assert f"## {en['consecuencias']}" in md  # "Consequences"
    # Spanish headings must NOT leak in.
    assert "## Contexto" not in md
    assert "## Decisión" not in md


@pytest.mark.parametrize("declared", ["ES", " es ", "Es"])
def test_docs_language_is_normalised(declared: str) -> None:
    md = render_adr(_sample_meta(docs_language=declared))
    frontmatter, _ = split_frontmatter(md)
    assert "docs_language: es" in frontmatter


# --- negative: unsupported language falls back to default ------------------


def test_unsupported_language_falls_back_to_default() -> None:
    md = render_adr(_sample_meta(docs_language="fr"))
    frontmatter, _ = split_frontmatter(md)
    assert f"docs_language: {DEFAULT_DOCS_LANGUAGE}" in frontmatter
    assert DEFAULT_DOCS_LANGUAGE in SUPPORTED_LANGUAGES
    # The Spanish (default) headings render.
    assert "## Contexto" in md


# --- determinism -----------------------------------------------------------


def test_render_is_deterministic() -> None:
    meta = _sample_meta()
    assert render_adr(meta) == render_adr(meta)


def test_render_ends_with_single_trailing_newline() -> None:
    md = render_adr(_sample_meta())
    assert md.endswith("\n")
    assert not md.endswith("\n\n")


def test_allocate_then_render_uses_the_allocated_number(tmp_path: Path) -> None:
    # End-to-end of the contract: scan -> allocate -> render the stem.
    _touch_adr(tmp_path, "0030-catalog-ingestion.md")
    number = next_adr_number(tmp_path)
    meta = _sample_meta(number=number)
    md = render_adr(meta)
    assert f"# ADR {format_adr_number(number)} —" in md
    assert adr_filename_stem(meta) == "0031-adr-template-sequential-numbering"


# --- rejects: qué casillas invalida esta decisión (task_gov_01) -------------
#
# El campo es OPCIONAL a propósito: la mayoría de los ADR no invalidan ninguna
# casilla, y una clave `rejects: []` en 155 documentos sería ruido que nadie
# lee. Se emite solo cuando hay algo que declarar — el mismo criterio que ya
# gobierna `## Alternativas consideradas` y `## Referencias`.


def test_rejects_renders_as_yaml_flow_list_when_present() -> None:
    md = render_adr(_sample_meta(rejects=("task_prod07_09", "task_prod13_15")))
    frontmatter, _ = split_frontmatter(md)
    data = yaml.safe_load(frontmatter)
    assert data["rejects"] == ["task_prod07_09", "task_prod13_15"]


def test_no_rejects_key_when_nothing_is_rejected() -> None:
    """Sin rechazos NO hay clave: `rejects: []` en 155 ADR es ruido."""
    frontmatter, _ = split_frontmatter(render_adr(_sample_meta()))
    assert "rejects:" not in frontmatter
    assert yaml.safe_load(frontmatter).get("rejects") is None


def test_rejects_sits_after_plan_referenced_in_key_order() -> None:
    """El orden de claves es contrato (el corpus se lee a ojo, no con un parser).

    `rejects:` va donde el lector ya está mirando la relación con el roadmap:
    justo detrás de `plan_referenced`.
    """
    frontmatter, _ = split_frontmatter(render_adr(_sample_meta(rejects=("task_prod07_09",))))
    lines = [line.split(":", 1)[0] for line in frontmatter.split("\n") if ":" in line]
    assert lines.index("rejects") == lines.index("plan_referenced") + 1
