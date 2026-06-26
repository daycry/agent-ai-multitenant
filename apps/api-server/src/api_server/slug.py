"""Path/branch-safe slug helper (prod-18 / ADR 0085).

`slugify` produces a stable kebab-case slug (``[a-z0-9-]``) for the worktree paths
(`BareRepoLayout`) and plan branch names (`make_plan_branch_name`) of git execution.
It is intentionally distinct from `schemas.catalog.normalize_tool_name`, which uses
``_`` and preserves the dot for MCP tool namespacing — wrong for filesystem paths.

The slug is generated ONCE at creation and persisted (``projects.slug`` /
``plans.slug``); it never changes when the name does, so a renamed project/plan does
not orphan its worktree or branch.
"""

from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
#: Fallback when a name has no slug-safe characters (e.g. "!!!", "", non-ascii only).
FALLBACK_SLUG = "untitled"


def slugify(value: str, *, max_length: int = 60) -> str:
    """Return a kebab-case ascii slug. Never empty (falls back to ``untitled``),
    never longer than ``max_length``, never with a leading/trailing hyphen."""
    # ascii-fold by dropping non-ascii (no transliteration dependency); lower-case.
    ascii_only = value.encode("ascii", "ignore").decode("ascii").lower()
    slug = _NON_ALNUM.sub("-", ascii_only).strip("-")
    if max_length > 0 and len(slug) > max_length:
        slug = slug[:max_length].rstrip("-")
    return slug or FALLBACK_SLUG
