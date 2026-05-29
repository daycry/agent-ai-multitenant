"""KB visibility resolver (Plan 06.9 task_06_9_03).

A KB is visible to a `(project_id, agent_id)` pair when **any** of:

  1. It is granted to the project (`kb_projects` row exists).
  2. It is granted to the agent template (`agent_knowledge_bases`
     row exists). Only applies when ``agent_id`` is provided.
  3. (Reserved for global KBs once those land — Plan 06.9 explicitly
     defers global scope; the SQL is shaped so adding the third
     branch is a code-only change.)

The resolver lives separate from the RAG search to keep the SQL
filter composable: ``recall_chunks`` builds a chunks query and wraps
it in an EXISTS clause; the LangGraph node that picks "which KBs am
I retrieving from" calls :func:`resolve_visible_kbs` directly.

Both surfaces use the same SQL fragment so they cannot drift.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def resolve_visible_kbs(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    agent_id: UUID | None = None,
) -> list[UUID]:
    """Return the **deduplicated** list of KB ids visible to this
    `(project, agent)` pair.

    The query unions two sources:
      * KBs granted to the project via ``kb_projects``.
      * KBs granted to the agent template via ``agent_knowledge_bases``
        (skipped if ``agent_id`` is None).

    Results are filtered to KBs whose `deleted_at IS NULL` so the
    caller can pass them straight to a chunk query without re-checking.
    Order: KB id ascending (deterministic — the caller sorts by other
    criteria if it cares about presentation).
    """
    # `kb.tenant_id = :tenant_id OR kb.is_builtin`: el tenant ve sus
    # propias KB y las built-in del catálogo global (Plan 06.12 / ADR
    # 0029). Las built-in NO son auto-visibles: la cláusula de grant de
    # abajo sigue siendo obligatoria, así que una built-in solo aparece
    # si el tenant la concedió a este proyecto/agente. Cross-tenant
    # sigue aislado: una KB de otro tenant no es ni propia ni built-in.
    sql = (
        "SELECT DISTINCT kb.id"
        " FROM knowledge_bases kb"
        " WHERE (kb.tenant_id = :tenant_id OR kb.is_builtin)"
        "   AND kb.deleted_at IS NULL"
        "   AND ("
        "        EXISTS ("
        "            SELECT 1 FROM kb_projects kp"
        "             WHERE kp.kb_id = kb.id AND kp.project_id = :project_id"
        "        )"
    )
    params: dict[str, object] = {
        "tenant_id": str(tenant_id),
        "project_id": str(project_id),
    }
    if agent_id is not None:
        sql += (
            "        OR EXISTS ("
            "            SELECT 1 FROM agent_knowledge_bases ak"
            "             WHERE ak.kb_id = kb.id AND ak.agent_id = :agent_id"
            "        )"
        )
        params["agent_id"] = str(agent_id)
    sql += "       )" " ORDER BY kb.id"

    result = await session.execute(text(sql), params)
    return [row[0] for row in result.all()]


def visibility_filter_clause(*, with_agent: bool) -> str:
    """Return the AND-clause that restricts a `chunks` query to
    visible KBs.

    Used by :mod:`api_server.rag.search` to keep one source of truth
    for "what is a visible KB". Bound parameters expected by the
    caller's `text(...).execute(...)`:

      * ``:tenant_id``
      * ``:project_id``
      * ``:agent_id`` — only when ``with_agent=True``.

    The clause assumes the outer query has `chunks` aliased as
    ``chunks`` and `documents` joinable via ``document_id``.
    """
    # Both branches join knowledge_bases and check `deleted_at IS NULL`
    # on the KB AND the document so a soft-deleted KB (or document)
    # stops feeding the RAG — mirroring the `resolve_visible_kbs`
    # filter so the two surfaces cannot drift (Plan 06.11 task_06_11_02).
    project_branch = (
        "EXISTS ("
        "    SELECT 1 FROM kb_projects kp"
        "    JOIN documents d ON d.id = chunks.document_id"
        "    JOIN knowledge_bases kb ON kb.id = d.kb_id"
        "    WHERE kp.kb_id = d.kb_id AND kp.project_id = :project_id"
        "      AND d.deleted_at IS NULL AND kb.deleted_at IS NULL"
        ")"
    )
    # Tenant guard (defence in depth on top of RLS): the chunk is the
    # tenant's OR belongs to a built-in KB of the global catalog (whose
    # chunks live under the platform tenant). Built-in chunks still only
    # surface through the grant branches below, so cross-tenant isolation
    # holds — a built-in granted to tenant A is invisible to tenant B
    # because B has no grant row (Plan 06.12 / ADR 0029).
    builtin_chunk = (
        "EXISTS ("
        "    SELECT 1 FROM documents db"
        "    JOIN knowledge_bases kbb ON kbb.id = db.kb_id"
        "    WHERE db.id = chunks.document_id AND kbb.is_builtin"
        ")"
    )
    tenant_guard = f"(chunks.tenant_id = :tenant_id OR {builtin_chunk})"
    if not with_agent:
        return f" AND {tenant_guard} AND {project_branch}"

    agent_branch = (
        "EXISTS ("
        "    SELECT 1 FROM agent_knowledge_bases ak"
        "    JOIN documents d2 ON d2.id = chunks.document_id"
        "    JOIN knowledge_bases kb2 ON kb2.id = d2.kb_id"
        "    WHERE ak.kb_id = d2.kb_id AND ak.agent_id = :agent_id"
        "      AND d2.deleted_at IS NULL AND kb2.deleted_at IS NULL"
        ")"
    )
    return f" AND {tenant_guard} AND ({project_branch} OR {agent_branch})"


__all__ = ["resolve_visible_kbs", "visibility_filter_clause"]
