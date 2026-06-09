"""llm_providers: add the unique ``slug`` handle (differentiate same-kind providers).

A provider's identity was only its UUID (not human-readable) + ``display_name``
(free text, not unique), so two providers of the same kind — e.g. a LOCAL Ollama
and an Ollama CLOUD — were indistinguishable in dropdowns and could be created
as accidental duplicates. This adds ``slug``: a stable, kebab-case, UNIQUE handle
the operator sets (``ollama-local`` / ``ollama-cloud`` / ``azure-prod`` …).

Online-safe three-step: add the column NULLable, backfill a slug for every
existing row (slugified from ``display_name``, de-duplicated with a numeric
suffix), then make it NOT NULL + add the ``uq_llm_providers_slug`` constraint.

Reversible: ``downgrade`` drops the constraint + the column. The table is
platform-global with no RLS (ADR 0028), so no policy/grant changes are needed.

Revision ID: 0083_llm_provider_slug
Revises: 0082_drop_shared_mem_ns
Create Date: 2026-06-09
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0083_llm_provider_slug"
down_revision: str | Sequence[str] | None = "0082_drop_shared_mem_ns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SLUG_MAX = 64


def _slugify(text: str) -> str:
    """display_name → kebab-case slug (mirrors db.llm_providers.SLUG_PATTERN)."""
    base = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    base = base[:_SLUG_MAX].strip("-")
    return base or "provider"


def upgrade() -> None:
    op.add_column("llm_providers", sa.Column("slug", sa.String(length=64), nullable=True))

    # Backfill: one unique slug per existing row, derived from display_name.
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, kind, display_name FROM llm_providers ORDER BY id")
    ).fetchall()
    seen: set[str] = set()
    for row in rows:
        base = _slugify(row.display_name) or _slugify(row.kind)
        slug = base
        n = 2
        while slug in seen:
            suffix = f"-{n}"
            slug = base[: _SLUG_MAX - len(suffix)].strip("-") + suffix
            n += 1
        seen.add(slug)
        bind.execute(
            sa.text("UPDATE llm_providers SET slug = :slug WHERE id = :id"),
            {"slug": slug, "id": row.id},
        )

    op.alter_column("llm_providers", "slug", existing_type=sa.String(length=64), nullable=False)
    op.create_unique_constraint("uq_llm_providers_slug", "llm_providers", ["slug"])


def downgrade() -> None:
    op.drop_constraint("uq_llm_providers_slug", "llm_providers", type_="unique")
    op.drop_column("llm_providers", "slug")
