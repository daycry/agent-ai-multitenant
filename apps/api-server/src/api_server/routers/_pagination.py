"""Pagination helpers for tenant-scoped list endpoints (task_06_14_13).

Audit finding `api-routers-validation-3`: the GET list endpoints
(`/agents`, `/agents/{id}/knowledge-bases`, tasks, skills, tools, plans,
comments, escalated-tasks) returned every matching row unbounded. A
tenant with thousands of rows would get an unbounded response — wasted
memory on the server and the wire.

The fix is a uniform `limit`/`offset` pair on every list endpoint,
validated by FastAPI (`ge`/`le`) so an out-of-range `limit` is a clean
`422` instead of a silent clamp. Defaults are generous on purpose so
existing callers (and the admin-panel, which does not yet paginate) keep
working unchanged: `limit` defaults to `DEFAULT_PAGE_SIZE` and never
exceeds `MAX_PAGE_SIZE`.

The two page-size bounds are module-level named constants — not magic
numbers scattered across six routers — per the project's config
principle. They are platform invariants of the REST contract (a
response-shape guardrail), not a per-tenant operational tunable, so they
live here rather than in `platform_settings` / `settings_registry`.
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import Query
from sqlalchemy import Select

# Backward-compatible default page size. Generous so callers that omit
# `limit` (the current admin-panel, scripts) keep getting a full-enough
# page without opting in to pagination.
DEFAULT_PAGE_SIZE = 100

# Hard upper bound a caller may request via `?limit=`. A request above
# this is rejected with 422 (not silently clamped) so the client learns
# its page is too big rather than getting a surprising truncation.
MAX_PAGE_SIZE = 500


def limit_query(default: int = DEFAULT_PAGE_SIZE) -> int:
    """Build the shared `limit` query parameter.

    Returned as a plain `int` whose runtime value is a FastAPI `Query`
    descriptor; declare the endpoint param as ``limit: int = limit_query()``.
    ``ge=1`` rejects 0/negative, ``le=MAX_PAGE_SIZE`` caps the page so a
    single request cannot ask for an unbounded result set.

    The runtime value is a FastAPI `Query` descriptor (typed `Any`); we
    cast to `int` so the endpoint signature reads as a plain `int` and
    mypy-strict stays happy at the call site.
    """
    return cast(
        int,
        Query(
            default=default,
            ge=1,
            le=MAX_PAGE_SIZE,
            description=(
                f"Max rows returned (1..{MAX_PAGE_SIZE}). Use a smaller value for "
                "typeahead/comboboxes; combine with `offset` to page."
            ),
        ),
    )


def offset_query() -> int:
    """Build the shared `offset` query parameter (``ge=0``)."""
    return cast(
        int,
        Query(
            default=0,
            ge=0,
            description="Number of leading rows to skip (for paging). Must be >= 0.",
        ),
    )


def apply_pagination(stmt: Select[Any], *, limit: int, offset: int) -> Select[Any]:
    """Apply ``LIMIT``/``OFFSET`` to an already-ordered SELECT.

    The caller is responsible for a deterministic ``.order_by(...)``
    before calling this — without it, `offset` paging would return
    arbitrary, overlapping rows. This helper deliberately does not add an
    ordering of its own to avoid masking a missing one.
    """
    return stmt.limit(limit).offset(offset)


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "apply_pagination",
    "limit_query",
    "offset_query",
]
