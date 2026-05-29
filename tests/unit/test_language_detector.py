"""Unit tests for the docs language validator (Plan 07 task_07_04).

Pure-text and tmp-file tests — no DB, no git. They pin the contract from
the task brief:

  * a clearly-Spanish body declared ``docs_language: es`` passes;
  * a clearly-English body declared ``docs_language: es`` is flagged;
  * ambiguous / short / code-heavy content does NOT false-positive (the
    detector only ever reports a *confident* mismatch).

Plus the supporting surface: frontmatter splitting / parsing, the
``lang`` alias, unsupported / missing declarations, and the file-level
convenience.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from api_server.docs_structure.language import (
    LANGUAGE_FRONTMATTER_KEYS,
    MIN_CONFIDENCE,
    MIN_STOPWORD_HITS,
    MISMATCH_DECLARED_FLOOR,
    SUPPORTED_LANGUAGES,
    Language,
    check_doc_file,
    detect_doc_language,
    parse_declared_language,
    split_frontmatter,
    validate_doc_language,
)

pytestmark = pytest.mark.unit


# --- realistic sample bodies ----------------------------------------------

_SPANISH_BODY = """\
# Visión general del sistema

Esta plataforma permite construir y orquestar equipos de agentes autónomos
que trabajan de forma cooperativa sobre los proyectos. La unidad operativa
es el plan, un conjunto de tareas con dependencias que los agentes ejecutan
en paralelo. Cada tarea se asigna a un trabajador y, cuando todas las
tareas están completas, el sistema abre una petición de cambios para que
el equipo la revise. Los agentes pueden colaborar entre sí mediante una
memoria compartida según el alcance configurado.
"""

_ENGLISH_BODY = """\
# System overview

This platform lets you build and orchestrate teams of autonomous agents
that work cooperatively on the projects. The operating unit is the plan, a
set of tasks with dependencies that the agents run in parallel. Each task
is assigned to a worker and, when all of the tasks are complete, the system
opens a pull request so that the team can review it. The agents may
collaborate with each other through a shared memory according to the
configured scope.
"""

_DOC_ES_DECLARED_ES = f"---\ndocs_language: es\ntitle: overview\n---\n\n{_SPANISH_BODY}"
_DOC_EN_DECLARED_ES = f"---\ndocs_language: es\ntitle: overview\n---\n\n{_ENGLISH_BODY}"
_DOC_EN_DECLARED_EN = f"---\ndocs_language: en\ntitle: overview\n---\n\n{_ENGLISH_BODY}"
_DOC_ES_DECLARED_EN = f"---\ndocs_language: en\ntitle: overview\n---\n\n{_SPANISH_BODY}"


# --- detect_doc_language: happy path ---------------------------------------


def test_detects_clearly_spanish() -> None:
    result = detect_doc_language(_SPANISH_BODY)
    assert result.language is Language.ES
    assert result.confidence >= MIN_CONFIDENCE
    assert result.es_hits > result.en_hits


def test_detects_clearly_english() -> None:
    result = detect_doc_language(_ENGLISH_BODY)
    assert result.language is Language.EN
    assert result.confidence >= MIN_CONFIDENCE
    assert result.en_hits > result.es_hits


def test_detection_ignores_frontmatter_block() -> None:
    # An English frontmatter declaration must not sway a Spanish body.
    result = detect_doc_language(_DOC_ES_DECLARED_EN)
    assert result.language is Language.ES


# --- detect_doc_language: edge / negative ----------------------------------


def test_short_content_is_unknown_not_a_guess() -> None:
    result = detect_doc_language("# Título\n\nHola mundo.\n")
    assert result.language is Language.UNKNOWN
    assert result.confidence == 0.0


def test_empty_body_is_unknown() -> None:
    result = detect_doc_language("")
    assert result.language is Language.UNKNOWN
    assert result.es_hits == 0
    assert result.en_hits == 0


def test_code_heavy_doc_does_not_classify_as_english() -> None:
    # A doc whose prose is one short Spanish sentence but body is mostly a
    # Python snippet full of English keywords must NOT read as English.
    doc = """\
# Ejemplo

Este es un ejemplo corto.

```python
from typing import Any
def select_from_table(where: str, this: Any) -> bool:
    return True if where in this else False
```
"""
    result = detect_doc_language(doc)
    # The English keywords live inside the fenced block and are stripped;
    # the verdict is either Spanish or (if too little prose) UNKNOWN — but
    # never a confident English false positive.
    assert result.language is not Language.EN


def test_inline_code_and_links_are_stripped() -> None:
    # Inline code identifiers and a link URL with English path segments
    # must not bias an otherwise-Spanish body.
    doc = (
        "Para configurar el servicio usa `the-config-flag` y revisa "
        "[la guía](https://example.com/the/getting/started/with/the/setup) "
        "porque cada uno de los parámetros del sistema debe estar definido "
        "según lo que indica la documentación interna del proyecto.\n"
    )
    result = detect_doc_language(doc)
    assert result.language is Language.ES


def test_mixed_language_near_even_split_is_unknown() -> None:
    # Roughly balanced es/en stopwords ⇒ below MIN_CONFIDENCE ⇒ UNKNOWN.
    doc = "el los las del con para the of and to in is are this that with from"
    result = detect_doc_language(doc)
    assert result.language is Language.UNKNOWN
    assert result.confidence < MIN_CONFIDENCE


def test_thresholds_are_named_constants() -> None:
    assert MIN_STOPWORD_HITS >= 1
    assert 0.5 < MIN_CONFIDENCE <= 1.0


# --- split_frontmatter / parse_declared_language ---------------------------


def test_split_frontmatter_extracts_block_and_body() -> None:
    fm, body = split_frontmatter(_DOC_ES_DECLARED_ES)
    assert "docs_language: es" in fm
    assert body.lstrip().startswith("# Visión general")


def test_split_frontmatter_handles_no_frontmatter() -> None:
    fm, body = split_frontmatter(_SPANISH_BODY)
    assert fm == ""
    assert body == _SPANISH_BODY


def test_split_frontmatter_tolerates_leading_bom() -> None:
    fm, body = split_frontmatter("﻿---\ndocs_language: en\n---\n\nbody\n")
    assert "docs_language: en" in fm
    assert body.strip() == "body"


def test_split_frontmatter_handles_crlf() -> None:
    fm, body = split_frontmatter("---\r\ndocs_language: es\r\n---\r\n\r\ncuerpo\r\n")
    assert "docs_language: es" in fm
    assert "cuerpo" in body


def test_parse_declared_language_reads_docs_language() -> None:
    assert parse_declared_language("docs_language: es\ntitle: x") == "es"


def test_parse_declared_language_lang_alias() -> None:
    # The bootstrap README stub emits `lang:`; it is the fallback key.
    assert parse_declared_language("title: x\nlang: en") == "en"
    assert "docs_language" in LANGUAGE_FRONTMATTER_KEYS
    assert "lang" in LANGUAGE_FRONTMATTER_KEYS


def test_parse_declared_language_docs_language_wins_over_lang() -> None:
    assert parse_declared_language("lang: en\ndocs_language: es") == "es"


def test_parse_declared_language_normalises_case_and_space() -> None:
    assert parse_declared_language("docs_language:  ES  ") == "es"


def test_parse_declared_language_absent_returns_none() -> None:
    assert parse_declared_language("title: x\nstatus: draft") is None


def test_parse_declared_language_empty_returns_none() -> None:
    assert parse_declared_language("") is None
    assert parse_declared_language("   \n  ") is None


def test_parse_declared_language_malformed_yaml_returns_none() -> None:
    # Unbalanced brackets / bad YAML must not raise — just yields None.
    assert parse_declared_language("docs_language: [unterminated\n: : :") is None


def test_parse_declared_language_non_mapping_returns_none() -> None:
    assert parse_declared_language("- just\n- a\n- list") is None


# --- validate_doc_language: the checker ------------------------------------


def test_spanish_body_declared_es_passes() -> None:
    result = validate_doc_language(_DOC_ES_DECLARED_ES)
    assert result.ok
    assert result.mismatch is None
    assert result.declared == "es"
    assert result.detection.language is Language.ES


def test_english_body_declared_es_is_flagged() -> None:
    result = validate_doc_language(_DOC_EN_DECLARED_ES)
    assert not result.ok
    assert result.mismatch is not None
    assert result.mismatch.declared == "es"
    assert result.mismatch.detected is Language.EN
    assert "docs_language" in result.mismatch.message


def test_english_body_declared_en_passes() -> None:
    result = validate_doc_language(_DOC_EN_DECLARED_EN)
    assert result.ok


def test_spanish_body_declared_en_is_flagged() -> None:
    result = validate_doc_language(_DOC_ES_DECLARED_EN)
    assert not result.ok
    assert result.mismatch is not None
    assert result.mismatch.declared == "en"
    assert result.mismatch.detected is Language.ES


def test_short_doc_does_not_false_positive() -> None:
    # Declares es, body too short to classify ⇒ UNKNOWN ⇒ pass.
    doc = "---\ndocs_language: es\n---\n\n# Hi\n\nThe end.\n"
    result = validate_doc_language(doc)
    assert result.ok
    assert result.detection.language is Language.UNKNOWN


def test_no_declaration_never_flags() -> None:
    # English body, no docs_language ⇒ nothing to compare against ⇒ pass.
    result = validate_doc_language(_ENGLISH_BODY)
    assert result.ok
    assert result.declared is None


def test_unsupported_declared_language_is_not_flagged() -> None:
    # Declares an out-of-scope language (fr); we only police es/en, so we
    # do not flag it as a mismatch even though the body reads English.
    doc = f"---\ndocs_language: fr\n---\n\n{_ENGLISH_BODY}"
    result = validate_doc_language(doc)
    assert result.ok
    assert result.declared == "fr"
    assert "fr" not in SUPPORTED_LANGUAGES


def test_mixed_body_declared_es_does_not_false_positive() -> None:
    doc = "---\ndocs_language: es\n---\n\nel the los of las and del to con in"
    result = validate_doc_language(doc)
    assert result.ok
    assert result.detection.language is Language.UNKNOWN


def test_bilingual_doc_with_strong_declared_presence_is_not_flagged() -> None:
    # A Spanish-declared doc that quotes a lot of English (e.g. a roadmap
    # that embeds English spec/code text): the body detects as EN by sheer
    # volume, BUT Spanish is present in force (share >= the floor), so the
    # checker treats it as genuinely bilingual, not mislabelled.
    # 48 es hits vs 182 en hits ⇒ es-share ≈ 0.21 (above the floor) while
    # EN clearly wins the detection (≈ 0.79 confidence).
    declared_heavy_es = " ".join(["el", "los", "las", "del", "con", "para"] * 8)
    english_heavy = " ".join(["the", "of", "and", "to", "in", "is", "for"] * 26)
    doc = f"---\ndocs_language: es\n---\n\n{declared_heavy_es} {english_heavy}\n"
    result = validate_doc_language(doc)
    # EN dominates the detection...
    assert result.detection.language is Language.EN
    # ...but the declared (es) share clears the floor ⇒ not flagged.
    assert result.detection.es_hits / (result.detection.es_hits + result.detection.en_hits) >= (
        MISMATCH_DECLARED_FLOOR
    )
    assert result.ok


def test_declared_language_nearly_absent_is_flagged() -> None:
    # Same shape but Spanish is a tiny minority (below the floor): now it
    # is a confident mislabel and the checker flags it.
    tiny_es = "el los"  # 2 es hits
    english_heavy = " ".join(["the", "of", "and", "to", "in", "is", "for"] * 8)
    doc = f"---\ndocs_language: es\n---\n\n{tiny_es} {english_heavy}\n"
    result = validate_doc_language(doc)
    assert result.detection.language is Language.EN
    es_share = result.detection.es_hits / (result.detection.es_hits + result.detection.en_hits)
    assert es_share < MISMATCH_DECLARED_FLOOR
    assert not result.ok


def test_mismatch_floor_is_a_named_constant() -> None:
    assert 0.0 < MISMATCH_DECLARED_FLOOR < MIN_CONFIDENCE


# --- check_doc_file (the one I/O boundary) ---------------------------------


def test_check_doc_file_flags_mismatch_on_disk(tmp_path: Path) -> None:
    md = tmp_path / "overview.md"
    md.write_text(_DOC_EN_DECLARED_ES, encoding="utf-8")

    result = check_doc_file(md)

    assert not result.ok
    assert result.mismatch is not None
    assert result.mismatch.detected is Language.EN


def test_check_doc_file_passes_matching_doc(tmp_path: Path) -> None:
    md = tmp_path / "overview.md"
    md.write_text(_DOC_ES_DECLARED_ES, encoding="utf-8")

    result = check_doc_file(md)

    assert result.ok


# --- corpus guard: the repo's own docs must not regress --------------------


def test_supported_languages_is_es_en_only() -> None:
    assert frozenset({"es", "en"}) == SUPPORTED_LANGUAGES
