"""User-memory for the personal assistant (ADR 0054).

The assistant builds up private, per-user memory (the user's name,
preferences, tastes) and recalls it on later turns, reusing the platform
memory subsystem: rows live in ``memory_entries`` with ``scope='private'``
and ``user_id`` = the chatting user, stamped ``metadata.source='assistant'``.

  * :func:`remember_user_fact` — the write side behind the ``remember_about_me``
    tool: dedup (skip an identical fact the user already stored) then persist.
  * :func:`recall_user_memories` — the user's private memories to surface this
    turn. MVP: the most recent N (a personal assistant has few facts per user,
    so injecting them all is more reliable than query matching and needs no
    FTS/embedding on the chat hot path — ADR 0054). Relevance ranking
    (BM25/vector via ``memorizer.recall``) is the scale-path fast-follow.
  * :func:`augment_system_prompt` — fold recalled facts (+ a write hint) into
    the assistant's system prompt so it "knows" the user without a tool call.

Isolation: every read/write is scoped to ``(tenant_id, scope='private',
user_id)`` — a user only ever sees/writes their own memory; the tenant filter
is defence-in-depth on top of RLS.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.memory import MemoryEntry
from api_server.memorizer.distillation import MemoryCandidate
from api_server.memorizer.persistence import persist_memory_candidates

# Stamped on assistant-written memories (filterable in /admin/memories).
ASSISTANT_MEMORY_SOURCE = "assistant"
# Content cap (mirrors the memorizer candidate convention).
MAX_MEMORY_CONTENT = 2000
# How many of the user's private memories to inject per turn (most recent
# first). A personal assistant accumulates few facts per user, so this cap is
# generous; the scale-path (relevance ranking) is a fast-follow.
USER_MEMORY_INJECT_LIMIT = 20
_VALID_TYPES = ("semantic", "episodic")


async def remember_user_fact(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    content: str,
    type: str = "semantic",
    tags: tuple[str, ...] = (),
) -> dict[str, object]:
    """Persist one durable fact about the user as a private memory.

    Deduplicates against an existing non-deleted private memory of the same
    user with identical normalised content (case/whitespace-insensitive), so
    the assistant re-saving the same fact is a no-op. Returns a small result
    dict the tool surfaces to the model.
    """
    normalised = " ".join(content.split())[:MAX_MEMORY_CONTENT]
    if not normalised:
        return {"stored": False, "reason": "empty content"}
    memory_type = type if type in _VALID_TYPES else "semantic"

    # Dedup: identical normalised content already stored for this user.
    existing = await session.execute(
        select(MemoryEntry.id)
        .where(
            MemoryEntry.tenant_id == tenant_id,
            MemoryEntry.scope == "private",
            MemoryEntry.user_id == user_id,
            MemoryEntry.deleted_at.is_(None),
            func.lower(func.btrim(MemoryEntry.content)) == normalised.lower(),
        )
        .limit(1)
    )
    if existing.scalar_one_or_none() is not None:
        return {"stored": False, "deduped": True}

    rows = await persist_memory_candidates(
        session,
        [MemoryCandidate(content=normalised, type=memory_type, tags=tuple(tags))],
        tenant_id=tenant_id,
        scope="private",
        user_id=user_id,
        agent_id=None,
        extra_metadata={"source": ASSISTANT_MEMORY_SOURCE},
    )
    await session.flush()
    return {"stored": True, "id": str(rows[0].id), "type": memory_type}


async def recall_user_memories(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    limit: int = USER_MEMORY_INJECT_LIMIT,
) -> list[str]:
    """The user's private memories to surface this turn (most recent first).

    MVP injects the user's facts directly rather than query-matching them: a
    personal assistant has few facts per user, so this is more reliable than
    FTS (``plainto_tsquery`` ANDs terms, so a natural-language question rarely
    matches a short stored fact) and needs no embedding on the hot path. The
    read is scoped to ``(tenant_id, scope='private', user_id)`` — only the
    user's own memory.
    """
    stmt = (
        select(MemoryEntry.content)
        .where(
            MemoryEntry.tenant_id == tenant_id,
            MemoryEntry.scope == "private",
            MemoryEntry.user_id == user_id,
            MemoryEntry.deleted_at.is_(None),
        )
        .order_by(MemoryEntry.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


def augment_system_prompt(
    base_prompt: str, *, known_facts: list[str], remember_enabled: bool
) -> str:
    """Fold recalled user facts (+ a write hint) into the system prompt.

    ``known_facts`` becomes a "Lo que sé de ti" section so the assistant
    answers as if it knows the user. When the ``remember_about_me`` tool is
    enabled, a short instruction nudges the model to save durable personal
    facts it learns. Returns ``base_prompt`` unchanged when there is nothing
    to add.
    """
    sections = [base_prompt]
    if known_facts:
        facts = "\n".join(f"- {fact}" for fact in known_facts)
        sections.append(
            "Lo que sé de ti (son datos CIERTOS sobre el usuario con el que hablas; "
            "tenlos en cuenta al responder y NUNCA digas que no los sabes):\n" + facts
        )
    if remember_enabled:
        sections.append(
            "Si el usuario comparte un dato personal duradero (su nombre, una "
            "preferencia, un gusto o su estilo), usa la herramienta "
            "remember_about_me para recordarlo en futuras conversaciones. No "
            "guardes información efímera ni la repitas si ya la sabes."
        )
    return "\n\n".join(sections)


__all__ = [
    "ASSISTANT_MEMORY_SOURCE",
    "MAX_MEMORY_CONTENT",
    "USER_MEMORY_INJECT_LIMIT",
    "augment_system_prompt",
    "recall_user_memories",
    "remember_user_fact",
]
