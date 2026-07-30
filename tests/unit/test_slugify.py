"""Unit test — prod-18 task_prod18_design_01.

`slugify` produces a stable, path/branch-safe kebab slug for project/plan worktree
paths and plan branch names (ADR 0085). Distinct from `normalize_tool_name` (which
uses `_` and keeps dots for MCP namespacing).
"""

from __future__ import annotations

import pytest
from api_server.slug import slugify

pytestmark = pytest.mark.unit


def test_basic_kebab() -> None:
    assert slugify("Api CI") == "api-ci"


def test_collapses_and_strips_separators() -> None:
    assert slugify("  Hello --  World!! ") == "hello-world"


def test_drops_non_alnum_keeps_digits() -> None:
    assert slugify("Plan v1.2 (final)") == "plan-v1-2-final"


def test_unicode_and_accents_transliterate() -> None:
    # PROY2-14: acentos/diéresis/ñ se transliteran (no se pierden letras);
    # sigue siendo ascii path-safe y nunca lanza.
    assert slugify("Café Münster") == "cafe-munster"
    assert slugify("Planificación") == "planificacion"
    assert slugify("Diseño según año") == "diseno-segun-ano"


def test_empty_or_symbol_only_falls_back() -> None:
    # A name with no slug-safe chars yields the documented fallback, not "".
    assert slugify("!!!") == "untitled"
    assert slugify("") == "untitled"


def test_max_length_truncation() -> None:
    out = slugify("x" * 200, max_length=40)
    assert len(out) <= 40
    assert out == "x" * 40


def test_truncation_cuts_at_word_boundary() -> None:
    # PROY2-14: el corte no deja media palabra ("aaaa-b") ni guion colgando.
    assert slugify("aaaa bbbb cccc", max_length=6) == "aaaa"
    assert slugify("plataforma agentes ia", max_length=15) == "plataforma"


def test_truncation_without_boundary_still_hard_cuts() -> None:
    # Una sola palabra más larga que el máximo se corta duro (no hay frontera).
    assert slugify("supercalifragilistico", max_length=8) == "superca" + "l"
