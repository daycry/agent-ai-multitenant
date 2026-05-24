"""Hierarchical conversation compression (Plan 03 task_03_04).

Long chats blow past any LLM's context window long before the planning
flow finishes. The system handles this by collapsing windows of older
messages into summary rows: a sub-agent reads N consecutive messages,
emits a one-paragraph summary, and the platform persists the summary
as a synthetic ``system`` message with ``is_summary=true``. The
summary's ``attachments`` carry the UUIDs of the messages it replaces
so the original feed is preserved for audit while the *context view*
only loads the summary in their stead.

"Hierarchical" means the procedure can be applied repeatedly: a window
that already contains summary rows produces a higher-level summary that
replaces them, growing the abstraction one floor at a time as the chat
keeps moving.

The Summariser is a Protocol so this module stays free of any LLM SDK
dependency. The agent loop (and Plan 04 RAG flows) will plug a real
LiteLLM-backed summariser in; tests use the scripted one below.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.conversation import (
    Conversation,
    Message,
    MessageAuthorKind,
)

# Sentinel kept in `Message.attachments` to flag which messages a summary
# replaces. The shape is:
#   {"kind": "summary_replaces", "message_ids": ["<uuid>", ...]}
SUMMARY_REPLACES_KIND = "summary_replaces"


# ---------------------------------------------------------------------------
# Summariser protocol + a deterministic test implementation
# ---------------------------------------------------------------------------
@runtime_checkable
class Summariser(Protocol):
    """The LLM-shaped seam used by `compress_old_messages`."""

    async def summarise(self, messages: list[Message]) -> str: ...


@dataclass
class ScriptedSummariser:
    """Replays a fixed sequence of summary strings.

    When the script is exhausted the last entry repeats. Records each
    call's input ids in `seen_windows` for tests to assert against
    without having to peek into the DB.
    """

    summaries: list[str]
    seen_windows: list[list[UUID]] = field(default_factory=list)
    _cursor: int = 0

    async def summarise(self, messages: list[Message]) -> str:
        if not self.summaries:
            raise ValueError("ScriptedSummariser needs at least one summary entry")
        self.seen_windows.append([m.id for m in messages])
        index = min(self._cursor, len(self.summaries) - 1)
        self._cursor += 1
        return self.summaries[index]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _replaced_message_ids(message: Message) -> set[UUID]:
    """Return the set of message ids a summary message replaces, or {}."""
    if not message.is_summary:
        return set()
    for att in message.attachments:
        if isinstance(att, dict) and att.get("kind") == SUMMARY_REPLACES_KIND:
            ids = att.get("message_ids") or []
            try:
                return {UUID(str(x)) for x in ids}
            except (ValueError, TypeError):
                return set()
    return set()


async def _load_all_messages(session: AsyncSession, conversation_id: UUID) -> list[Message]:
    """All messages of the conversation ordered chronologically (UUID v7
    is timestamp-sortable so ordering by id is the same as by send time)."""
    result = await session.execute(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)
    )
    return list(result.scalars().all())


def _uncovered_messages(messages: list[Message]) -> list[Message]:
    """Return messages NOT replaced by any later summary in the feed.

    Summary rows themselves are kept — a later compression pass may fold
    them into a higher-level summary (the "hierarchical" bit).
    """
    replaced: set[UUID] = set()
    # Walk newest-to-oldest so later summaries shadow earlier history.
    for m in reversed(messages):
        replaced |= _replaced_message_ids(m)
    return [m for m in messages if m.id not in replaced]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def compress_old_messages(
    session: AsyncSession,
    conversation_id: UUID,
    summariser: Summariser,
    *,
    threshold_messages: int = 20,
    window_messages: int = 10,
) -> Message | None:
    """Compress the oldest contiguous window of uncovered messages.

    If the conversation has fewer than `threshold_messages` uncovered
    messages, returns None (nothing to do). Otherwise:

      1. Take the oldest `window_messages` uncovered rows.
      2. Ask the summariser for a one-paragraph summary.
      3. Persist a new ``system``-authored message with `is_summary=True`,
         the summary text as `content`, and a `summary_replaces`
         attachment listing the replaced ids.

    The new row's `mode` is copied from the conversation's
    `current_mode` so the feed UI can keep its mode-tinted styling.

    Hierarchical: when the window contains earlier summaries, the new
    summary's `summary_replaces` list will include those summaries too —
    the next call to `load_context_window` will then return *one*
    higher-level summary in place of the chain it folded.
    """
    if window_messages <= 0 or threshold_messages <= 0:
        raise ValueError("threshold_messages and window_messages must be > 0")

    all_messages = await _load_all_messages(session, conversation_id)
    uncovered = _uncovered_messages(all_messages)
    if len(uncovered) < threshold_messages:
        return None

    window = uncovered[:window_messages]
    summary_text = await summariser.summarise(window)

    # Load the conversation so we know the active mode (and the tenant
    # for the synthesised row).
    conv = await session.get(Conversation, conversation_id)
    if conv is None:  # pragma: no cover - defensive, the caller had the id
        raise ValueError(f"conversation {conversation_id} not found")

    summary = Message(
        tenant_id=conv.tenant_id,
        conversation_id=conv.id,
        author_kind=MessageAuthorKind.SYSTEM.value,
        content=summary_text,
        mode=conv.current_mode,
        attachments=[
            {
                "kind": SUMMARY_REPLACES_KIND,
                "message_ids": [str(m.id) for m in window],
            }
        ],
        is_summary=True,
    )
    session.add(summary)
    await session.flush()
    await session.refresh(summary)
    return summary


async def load_context_window(
    session: AsyncSession,
    conversation_id: UUID,
    *,
    max_messages: int = 50,
) -> list[Message]:
    """Return the *contextually useful* slice of a conversation.

    Walks the message history newest-to-oldest:

      - Includes every uncovered message until `max_messages` is hit.
      - When a summary is encountered it is included (it stands in for
        older history), and once included no further older messages are
        appended for *that* covered range.

    Returned list is in chronological order so the caller can feed it to
    an LLM verbatim.
    """
    if max_messages <= 0:
        raise ValueError("max_messages must be > 0")

    all_messages = await _load_all_messages(session, conversation_id)
    uncovered = _uncovered_messages(all_messages)
    # Truncate from the front to the most recent `max_messages` (chat
    # apps want the newest, not the oldest, when the window is full).
    return uncovered[-max_messages:]


__all__ = [
    "SUMMARY_REPLACES_KIND",
    "ScriptedSummariser",
    "Summariser",
    "compress_old_messages",
    "load_context_window",
]
