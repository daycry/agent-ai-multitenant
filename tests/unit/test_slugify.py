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


def test_unicode_and_accents_degrade() -> None:
    # Non-ascii is dropped (path-safe ascii only); never raises.
    assert slugify("Café Münster") == "caf-mnster"


def test_empty_or_symbol_only_falls_back() -> None:
    # A name with no slug-safe chars yields the documented fallback, not "".
    assert slugify("!!!") == "untitled"
    assert slugify("") == "untitled"


def test_max_length_truncation() -> None:
    out = slugify("x" * 200, max_length=40)
    assert len(out) <= 40
    assert out == "x" * 40


def test_truncation_does_not_leave_trailing_hyphen() -> None:
    out = slugify("aaaa bbbb cccc", max_length=6)
    assert not out.endswith("-")
