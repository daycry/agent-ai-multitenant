"""tools deduplication + closed taxonomy (Plan 06.18 task_06_18_04, ADR 0049).

The ``tools`` table shipped (migration 0002) with ``category`` /
``security_level`` / ``implementation_type`` as free ``String`` columns and
**no** uniqueness on ``(tenant_id, name)`` — so a tenant could create two live
tools with the same name, or a custom tool homonymous with a platform built-in,
and the taxonomy facets accepted any text. This migration closes both holes:

  * **Partial unique index** ``uq_tools_tenant_name (tenant_id, name)
    WHERE deleted_at IS NULL`` — no two *live* tools of the same tenant share a
    name; a soft-deleted name is free to reuse. (PostgreSQL UNIQUE constraints
    cannot carry a WHERE clause, hence a partial unique *index* — mirrors the
    ``ix_custom_chat_modes_tenant_name`` posture from migration 0015.)
  * Three **CHECK constraints** pinning the taxonomy to the closed value sets
    of ADR 0049. ``category`` is built from the real seed
    (``api_server.seeds.builtin_tools``) plus the documented origin /
    orchestration / custom buckets (``ToolCategory`` is the single declaration,
    and a runtime assertion proves the enum still covers every seed category);
    ``security_level`` and ``implementation_type`` mirror their StrEnums.

Pre-flight sanitisation (ADR 0049, "saneo de filas existentes fuera de enum"):

  * Any **custom** tool whose ``category`` is outside the closed set is remapped
    to the ``custom`` catch-all bucket *before* the CHECK is added (dev DBs may
    carry experiment rows such as ``code`` / ``data``). Built-ins always
    conform, so they are never touched.
  * ``security_level`` / ``implementation_type`` have no safe automatic remap (a
    default could silently downgrade security or break execution), so the
    upgrade *asserts* there is no out-of-enum row for those two — a loud, early
    failure the operator must sanitise by hand. In practice the built-ins and
    every API-created row already conform.

Dedup (task_06_18_04 "dedupe"): before the unique index is built, any
``(tenant_id, name)`` group with more than one *live* row is consolidated —
the most-recently-updated row survives and the losers are soft-deleted
(``deleted_at = now()``), the same "latest wins" stance migration 0076 used to
consolidate SSO rows. The seed has no live duplicates; this only fires on dirty
dev data.

Reversible: ``downgrade`` drops the three CHECKs and the unique index, restoring
0076 exactly (the columns stay free ``String`` again — no schema/data is lost;
the ``category`` remap and the dedup soft-deletes are one-way, the ADR's
accepted trade-off — Alembic's "data loss on downgrade is explicit" stance).

Revision ID: 0077_tools_dedup_taxonomy
Revises: 0076_sso_global
Create Date: 2026-06-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0077_tools_dedup_taxonomy"
down_revision: str | Sequence[str] | None = "0076_sso_global"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Documented buckets that are NOT in the catalog seed (ADR 0049 / 0052):
#   mcp           imported MCP tools (origin facet), <server>.<tool>
#   orchestration runtime-registered orchestration tools (ADR 0048)
#   custom        catch-all for tenant-authored tools
_EXTRA_CATEGORIES: frozenset[str] = frozenset({"mcp", "orchestration", "custom"})
_SECURITY_LEVELS: tuple[str, ...] = ("safe", "sandboxed", "privileged")
_IMPLEMENTATION_TYPES: tuple[str, ...] = (
    "builtin",
    "python_function",
    "http_endpoint",
    "mcp_tool",
    "docker_command",
)


def _allowed_categories() -> tuple[str, ...]:
    """Derive the closed category set from the real seed + extra buckets.

    Imported lazily inside the migration so the value set is genuinely
    data-driven from ``builtin_tools`` (task_06_18_04), and cross-checked
    against the ``ToolCategory`` declaration so the two never drift.
    """
    from api_server.db.domain import ToolCategory
    from api_server.seeds.builtin_tools import BUILTIN_TOOLS

    seed_categories = {t.category for t in BUILTIN_TOOLS}
    allowed = seed_categories | _EXTRA_CATEGORIES
    enum_values = {c.value for c in ToolCategory}
    # The enum must cover the seed; if a seed category is missing the enum is
    # stale and the contract test (task_06_18_14) would also fail.
    missing = seed_categories - enum_values
    assert not missing, f"ToolCategory is missing seed categories: {sorted(missing)}"
    return tuple(sorted(allowed | enum_values))


def _in_list(column: str, values: Sequence[str]) -> str:
    rendered = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({rendered})"


def upgrade() -> None:
    categories = _allowed_categories()
    bind = op.get_bind()

    # Sanitise: remap any out-of-taxonomy CUSTOM category to the `custom`
    # catch-all bucket (built-ins always conform and are left untouched).
    bind.execute(
        sa.text(
            "UPDATE tools SET category = 'custom'"
            " WHERE is_builtin = false AND category NOT IN :allowed"
        ).bindparams(sa.bindparam("allowed", tuple(categories), expanding=True))
    )

    # No safe automatic remap for these two -> assert there is nothing to fix.
    for column, allowed in (
        ("category", categories),
        ("security_level", _SECURITY_LEVELS),
        ("implementation_type", _IMPLEMENTATION_TYPES),
    ):
        offending = bind.execute(
            sa.text(f"SELECT count(*) FROM tools WHERE {column} NOT IN :allowed").bindparams(
                sa.bindparam("allowed", tuple(allowed), expanding=True)
            )
        ).scalar_one()
        assert offending == 0, (
            f"{offending} tools rows have an out-of-taxonomy {column}; "
            "sanitise them before applying ck_tools_"
        )

    # Dedup live duplicates so the partial unique index can build: per
    # (tenant_id, name) keep the most-recently-updated live row and soft-delete
    # the losers (same "latest wins" stance as migration 0076's consolidation).
    bind.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY tenant_id, name
                           ORDER BY updated_at DESC, id DESC
                       ) AS rn
                  FROM tools
                 WHERE deleted_at IS NULL
            )
            UPDATE tools t
               SET deleted_at = now()
              FROM ranked r
             WHERE t.id = r.id AND r.rn > 1
            """
        )
    )

    op.create_index(
        "uq_tools_tenant_name",
        "tools",
        ["tenant_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_check_constraint("ck_tools_category", "tools", _in_list("category", categories))
    op.create_check_constraint(
        "ck_tools_security_level", "tools", _in_list("security_level", _SECURITY_LEVELS)
    )
    op.create_check_constraint(
        "ck_tools_implementation_type",
        "tools",
        _in_list("implementation_type", _IMPLEMENTATION_TYPES),
    )


def downgrade() -> None:
    op.drop_constraint("ck_tools_implementation_type", "tools", type_="check")
    op.drop_constraint("ck_tools_security_level", "tools", type_="check")
    op.drop_constraint("ck_tools_category", "tools", type_="check")
    op.drop_index("uq_tools_tenant_name", table_name="tools")
