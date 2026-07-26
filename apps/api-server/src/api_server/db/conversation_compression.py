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
summariser backed by `shared_llm.LLMProvider` (ADR 0021) — any of the
four catalog providers fits. Tests use the scripted one below.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.conversation import (
    Conversation,
    Message,
    MessageAuthorKind,
)

_log = structlog.get_logger("api_server.db.conversation_compression")

# Sentinel kept in `Message.attachments` to flag which messages a summary
# replaces. The shape is:
#   {"kind": "summary_replaces", "message_ids": ["<uuid>", ...]}
SUMMARY_REPLACES_KIND = "summary_replaces"

# Second attachment kind, added by task_wf_06: the STRUCTURED record of what the
# folded window contained. See `SummaryRecord` for why prose alone is not enough.
#   {"kind": "summary_record", "requisitos": [...], "decisiones": [...],
#    "descartado": [...], "abierto": [...]}
SUMMARY_RECORD_KIND = "summary_record"

# A turn starts at every user message — the only boundary the feed already carries.
_TURN_START_KIND = MessageAuthorKind.USER.value

# ~4 characters per token. See `estimate_tokens` for why this is a heuristic.
_CHARS_PER_TOKEN = 4


# ---------------------------------------------------------------------------
# Summariser protocol + a deterministic test implementation
# ---------------------------------------------------------------------------
@runtime_checkable
class Summariser(Protocol):
    """The LLM-shaped seam used by `compress_old_messages`."""

    async def summarise(self, messages: list[Message]) -> str: ...


_RECORD_FIELDS: tuple[str, ...] = ("requisitos", "decisiones", "descartado", "abierto")

_RECORD_HEADINGS: dict[str, str] = {
    "requisitos": "Requisitos",
    "decisiones": "Decisiones",
    "descartado": "Descartado",
    "abierto": "Abierto",
}


@dataclass(frozen=True)
class SummaryRecord:
    """Lo que un resumen conserva **literal** al plegarse (task_wf_06 b).

    La compresión jerárquica de prosa degrada: cada piso vuelve a pasar por un
    modelo un texto que ya era un resumen, y a los tres pisos «debe funcionar sin
    conexión» se ha convertido en «el usuario mencionó varios requisitos». Con la
    conversación de planning eso es fatal, porque el plan se genera al final y los
    requisitos se enuncian al principio.

    Por eso el resumen lleva doble representación: prosa en ``content`` para el
    humano y este registro en ``attachments``. Al plegar, el LLM solo resume los
    mensajes crudos; los registros que ya existían se fusionan de forma
    determinista (:meth:`merged_with`), así que una entrada del piso 1 llega
    intacta al piso 5 sin volver a pasar por un modelo.
    """

    requisitos: tuple[str, ...] = ()
    decisiones: tuple[str, ...] = ()
    descartado: tuple[str, ...] = ()
    abierto: tuple[str, ...] = ()

    def is_empty(self) -> bool:
        return not any(getattr(self, name) for name in _RECORD_FIELDS)

    def as_attachment(self) -> dict[str, Any]:
        att: dict[str, Any] = {"kind": SUMMARY_RECORD_KIND}
        for name in _RECORD_FIELDS:
            att[name] = list(getattr(self, name))
        return att

    @classmethod
    def from_attachment(cls, att: Mapping[str, Any]) -> SummaryRecord:
        """Tolerante por diseño: el attachment es JSONB y puede venir de una
        versión anterior o de un modelo que se saltó una clave."""
        return cls(**{name: _clean_entries(att.get(name)) for name in _RECORD_FIELDS})

    def merged_with(self, other: SummaryRecord) -> SummaryRecord:
        """Concatenar y deduplicar, conservando el orden de primera aparición.

        Deduplicar no es cosmético: cada pliegue re-emite lo que ya sabía, así que
        sin esto el registro crecería con el número de pisos y acabaría
        desbordando el prompt él solo.
        """
        merged: dict[str, tuple[str, ...]] = {}
        for name in _RECORD_FIELDS:
            merged[name] = _dedup(list(getattr(self, name)) + list(getattr(other, name)))
        return SummaryRecord(**merged)


def _clean_entries(raw: Any) -> tuple[str, ...]:
    """Normalise a JSONB list into a tuple of non-empty strings."""
    if not isinstance(raw, list):
        return ()
    return _dedup(item for item in raw if isinstance(item, str))


def _dedup(entries: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for entry in entries:
        text = entry.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return tuple(out)


def record_of(message: Message) -> SummaryRecord | None:
    """The message's structured record, or ``None`` when it carries none.

    The distinction matters: ``None`` means "raw message, the LLM must summarise
    it", an empty record means "already-folded summary with nothing to inherit".
    A summary written before task_wf_06 has no record, so it reads as raw and its
    prose goes back to the model — degraded, but never lost.
    """
    for att in message.attachments:
        if isinstance(att, dict) and att.get("kind") == SUMMARY_RECORD_KIND:
            return SummaryRecord.from_attachment(att)
    return None


def split_window(window: Sequence[Message]) -> tuple[list[Message], SummaryRecord]:
    """Split a fold window into (what the LLM must read, what is inherited literally).

    This is the hybrid fold. The prose of an already-folded summary is NEVER an
    input here — feeding it back to a model is exactly the lossy re-summarisation
    the structured record exists to avoid.
    """
    raw: list[Message] = []
    inherited = SummaryRecord()
    for message in window:
        record = record_of(message)
        if record is None:
            raw.append(message)
        else:
            inherited = inherited.merged_with(record)
    return raw, inherited


def render_record(record: SummaryRecord) -> str:
    """Render the record as Markdown bullets, or ``""`` when it is empty.

    Not cosmetic: ``history_from_messages`` feeds the LLM only ``Message.content``,
    so a record that lives solely in ``attachments`` is invisible to the model and
    the survival guarantee would be a lie. The rendered block is what actually
    carries a floor-1 requirement into the floor-5 prompt.
    """
    blocks: list[str] = []
    for name in _RECORD_FIELDS:
        entries = getattr(record, name)
        if not entries:
            continue
        bullets = "\n".join(f"- {entry}" for entry in entries)
        blocks.append(f"**{_RECORD_HEADINGS[name]}**\n{bullets}")
    return "\n\n".join(blocks)


@dataclass(frozen=True)
class WindowSummary:
    """A structured summariser's answer for one window.

    ``cause`` copies the shape of ``DistillationResult``
    (``memorizer/distillation.py``): ``ok`` | ``llm_empty`` | ``llm_unparseable``
    | ``llm_error``. That discriminant exists because conflating "the provider
    failed", "the model did not follow the format" and "there was nothing to
    record" made the failure undiagnosable. A non-``ok`` outcome means the
    conversation simply stays uncompressed — never an exception towards the turn.
    """

    content: str
    record: SummaryRecord = field(default_factory=SummaryRecord)
    cause: str = "ok"

    @property
    def ok(self) -> bool:
        return self.cause == "ok" and bool(self.content.strip())


@runtime_checkable
class StructuredSummariser(Protocol):
    """A summariser that also produces the structured record.

    Kept separate from :class:`Summariser` so the prose-only implementations
    (notably :class:`ScriptedSummariser`, and any caller written before
    task_wf_06) keep working unchanged; ``compress_old_messages`` picks the
    richer path only when the summariser offers it.
    """

    async def summarise_window(self, messages: list[Message]) -> WindowSummary: ...


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
    """Return the set of message ids a summary message replaces, or {}.

    Only a ``system``-authored summary is believed. ``POST /messages`` takes
    ``is_summary`` and ``attachments`` as free client input, and once the team's
    prompt started going through :func:`load_context_window` a tenant member
    could post their OWN message carrying ``is_summary=true`` plus a
    ``summary_replaces`` pointing at SOMEONE ELSE'S messages — and those messages
    vanished from the context the team reads, with the feed not showing anything
    odd because ``GET /messages`` still returns them (adversarial audit
    2026-07-25).

    The endpoint already rejects ``author_kind != 'user'`` so nobody can forge an
    attachment with an agent's voice; this is the same attack through the next
    door. The only legitimate writer of coverage is
    :func:`compress_old_messages`, which authors as ``system``.
    """
    if not message.is_summary or message.author_kind != MessageAuthorKind.SYSTEM.value:
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


def aligned_window_size(author_kinds: Sequence[str], window_messages: int) -> int:
    """Shrink (or stretch) a fold window so it never cuts a turn in half.

    The defaults of `compress_old_messages` assume a 1-to-1 chat. This one is not:
    **one turn is 6-10 messages** (the PM's framing + N specialists + the
    synthesis), so a fixed 10-message window would fold the framing and four
    specialists and leave the synthesis out — the summary would then describe half
    a discussion as if it were the whole thing.

    A turn starts at every ``user`` message, so whole turns are exactly the slices
    between those boundaries. No new state is needed.

    Rules, in order:
      1. Take as many WHOLE turns as fit in ``window_messages``.
      2. If not even one whole turn fits, fold that one turn entire — trimming to
         zero would stall compression forever on a long turn.
      3. With no boundaries at all (a feed with no user messages) there are no
         turns to respect, so the plain window applies.

    The most recent turn is never folded: it is the one being answered right now.
    """
    boundaries = [i for i, kind in enumerate(author_kinds) if i > 0 and kind == _TURN_START_KIND]
    fitting = [b for b in boundaries if b <= window_messages]
    if fitting:
        return max(fitting)
    if boundaries:
        return boundaries[0]
    return min(window_messages, len(author_kinds))


def estimate_tokens(text: str) -> int:
    """Rough token count for the context budget (~4 chars per token).

    Deliberately a heuristic and not a tokenizer: the platform is
    provider-agnostic (ADR 0021) and each backend tokenises differently, so an
    exact count for one of them would be wrong for the other three. This is a
    guard rail against 50 very long rows, not an accounting figure.
    """
    return (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN


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


def compose_summary_content(prose: str, record: SummaryRecord, *, replaced: int) -> str:
    """Build the summary message body: a header, the prose, then the record.

    The record is rendered INTO the content on purpose — see :func:`render_record`.
    """
    header = f"🗂️ **Resumen de {replaced} mensajes anteriores**"
    parts = [header]
    if prose.strip():
        parts.append(prose.strip())
    rendered = render_record(record)
    if rendered:
        parts.append(rendered)
    return "\n\n".join(parts)


async def _summarise_window(
    summariser: Summariser | StructuredSummariser, window: list[Message]
) -> tuple[str, SummaryRecord | None] | None:
    """Run the right summarisation path for this summariser.

    Returns ``(content, record | None)``, or ``None`` when the structured
    summariser reported a failure and the window must stay uncompressed.
    """
    if not isinstance(summariser, StructuredSummariser):
        prose_only = await summariser.summarise(window)
        return prose_only, None

    raw, inherited = split_window(window)
    outcome = await summariser.summarise_window(raw)
    if not outcome.ok and raw:
        # A failed model call on real content: compressing now would drop those
        # messages from the context view in exchange for nothing.
        _log.warning("chat.summariser_failed", cause=outcome.cause, window=len(window))
        return None
    # `raw` empty means the window folds only already-structured summaries: the
    # merge is deterministic, so a non-ok outcome (no model call was made) is fine.
    record = inherited.merged_with(outcome.record)
    if record.is_empty() and not outcome.content.strip():
        _log.warning("chat.summariser_empty", cause=outcome.cause, window=len(window))
        return None
    prose = outcome.content if outcome.ok else ""
    return compose_summary_content(prose, record, replaced=len(window)), record


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
    align_to_turns: bool = False,
) -> Message | None:
    """Compress the oldest contiguous window of uncovered messages.

    If the conversation has fewer than `threshold_messages` uncovered
    messages, returns None (nothing to do). Otherwise:

      1. Take the oldest `window_messages` uncovered rows (rounded to whole
         turns when ``align_to_turns``; see :func:`aligned_window_size`).
      2. Ask the summariser to summarise them.
      3. Persist a new ``system``-authored message with `is_summary=True`,
         the summary text as `content`, and a `summary_replaces`
         attachment listing the replaced ids.

    The new row's `mode` is copied from the conversation's
    `current_mode` so the feed UI can keep its mode-tinted styling.

    Hierarchical: when the window contains earlier summaries, the new
    summary's `summary_replaces` list will include those summaries too —
    the next call to `load_context_window` will then return *one*
    higher-level summary in place of the chain it folded.

    **The fold is hybrid** when the summariser implements
    :class:`StructuredSummariser`: the model only reads the RAW messages of the
    window, while the `summary_record` of the summaries being folded is merged
    deterministically and carried over literally (task_wf_06 b). A prose-only
    summariser keeps the original behaviour.

    Returns ``None`` — leaving the conversation uncompressed — when the
    summariser reports a failure. Never raises towards the caller's turn.
    """
    if window_messages <= 0 or threshold_messages <= 0:
        raise ValueError("threshold_messages and window_messages must be > 0")

    all_messages = await _load_all_messages(session, conversation_id)
    uncovered = _uncovered_messages(all_messages)
    if len(uncovered) < threshold_messages:
        return None

    size = window_messages
    if align_to_turns:
        size = aligned_window_size([m.author_kind for m in uncovered], window_messages)
    window = uncovered[:size]
    if not window:  # pragma: no cover - aligned_window_size never returns 0 here
        return None

    outcome = await _summarise_window(summariser, window)
    if outcome is None:
        return None
    summary_text, record = outcome

    # Load the conversation so we know the active mode (and the tenant
    # for the synthesised row).
    conv = await session.get(Conversation, conversation_id)
    if conv is None:  # pragma: no cover - defensive, the caller had the id
        raise ValueError(f"conversation {conversation_id} not found")

    attachments: list[dict[str, Any]] = [
        {
            "kind": SUMMARY_REPLACES_KIND,
            "message_ids": [str(m.id) for m in window],
        }
    ]
    if record is not None:
        attachments.append(record.as_attachment())

    summary = Message(
        tenant_id=conv.tenant_id,
        conversation_id=conv.id,
        author_kind=MessageAuthorKind.SYSTEM.value,
        content=summary_text,
        mode=conv.current_mode,
        attachments=attachments,
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
    max_tokens: int | None = None,
) -> list[Message]:
    """Return the *contextually useful* slice of a conversation.

    Walks the message history newest-to-oldest:

      - Includes every uncovered message until `max_messages` is hit.
      - When a summary is encountered it is included (it stands in for
        older history), and once included no further older messages are
        appended for *that* covered range.

    ``max_tokens`` is a SECOND guard (task_wf_06 e). ``max_messages`` is a
    counter, and a counter cannot see that 50 very long rows overflow the model
    just as surely as 500 short ones. With compression on, the counter rarely
    bites; the token budget covers the case it cannot. The newest message is
    always kept even if it alone exceeds the budget — returning nothing would
    leave the model with no idea what it is answering.

    Returned list is in chronological order so the caller can feed it to
    an LLM verbatim.
    """
    if max_messages <= 0:
        raise ValueError("max_messages must be > 0")
    if max_tokens is not None and max_tokens <= 0:
        raise ValueError("max_tokens must be > 0 when given")

    all_messages = await _load_all_messages(session, conversation_id)
    uncovered = _uncovered_messages(all_messages)
    # Truncate from the front to the most recent `max_messages` (chat
    # apps want the newest, not the oldest, when the window is full).
    window = uncovered[-max_messages:]
    if max_tokens is None:
        return window

    kept: list[Message] = []
    spent = 0
    for message in reversed(window):
        cost = estimate_tokens(message.content or "")
        if kept and spent + cost > max_tokens:
            break
        kept.append(message)
        spent += cost
    kept.reverse()
    if len(kept) < len(window):
        _log.info(
            "chat.context_window_token_capped",
            kept=len(kept),
            dropped=len(window) - len(kept),
            max_tokens=max_tokens,
        )
    return kept


__all__ = [
    "SUMMARY_RECORD_KIND",
    "SUMMARY_REPLACES_KIND",
    "ScriptedSummariser",
    "StructuredSummariser",
    "Summariser",
    "SummaryRecord",
    "WindowSummary",
    "aligned_window_size",
    "compose_summary_content",
    "compress_old_messages",
    "estimate_tokens",
    "load_context_window",
    "record_of",
    "render_record",
    "split_window",
]
