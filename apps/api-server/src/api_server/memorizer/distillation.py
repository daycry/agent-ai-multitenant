"""LLM distillation step (Plan 04 task_04_03).

Turns an `Execution` (steps_log + task context + agent context) into
0-N short :class:`MemoryCandidate` instances. The transform is one
LLM call; the prompt asks for both episodic ("this happened in this
run") and semantic ("here's a rule we learnt") facts, in strict JSON.

We try hard to fail soft:

  - parser is forgiving (loads first JSON array it finds in the LLM
    response — models love wrapping JSON in markdown fences);
  - any malformed candidate is skipped, the rest survive;
  - returning an empty list is a perfectly valid outcome — not
    every execution produces something worth remembering.

The LLM call is abstracted behind :class:`shared_llm.base.LLMProvider`
so tests inject a deterministic fake and production wires a real
Ollama / Claude / Copilot / Azure Foundry provider (ADR 0021).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog
from shared_llm.base import LLMProvider
from shared_llm.types import Message

logger = structlog.get_logger(__name__)


# Hard caps so a runaway LLM cannot stuff the memory store with junk.
MAX_CANDIDATES_PER_EXECUTION = 5
MAX_CONTENT_CHARS = 2000

# Models we trust to follow JSON instructions well. Anything else
# still works (we try to parse), but we cap the run with a tighter
# token budget by default.
_DEFAULT_MAX_TOKENS = 800


@dataclass(frozen=True)
class MemoryCandidate:
    """One memory the LLM thinks is worth remembering.

    Persistence (:func:`api_server.memorizer.persistence.persist_memory_candidates`)
    fills in tenant / scope / owner pointers from the execution +
    agent context; the candidate carries only the "what" and the
    "how-to-classify".
    """

    content: str
    type: str  # 'episodic' or 'semantic'
    tags: tuple[str, ...] = field(default_factory=tuple)


_SYSTEM_PROMPT = (
    "You are the Memorizer of an agentic platform. After every "
    "execution you extract a SHORT list of useful memories to store for "
    "future agent runs. Always answer with a JSON array (no prose, no "
    "markdown fences). Each item is an object with exactly three keys:\n"
    '  "content" — a single-paragraph fact (max 2000 chars).\n'
    '  "type"    — "episodic" (a concrete event) or "semantic" '
    "(a generalised rule extracted from one or more events).\n"
    '  "tags"    — array of short string tags for filtering, e.g. '
    '["sqlalchemy", "asyncpg"].\n'
    "Return between 0 and 5 items. Return an empty array if the "
    "execution produced nothing worth remembering."
)


def _user_prompt(*, execution: Mapping[str, Any], agent: Mapping[str, Any]) -> str:
    """Compact human-readable summary the LLM works on.

    We deliberately do not dump the full steps_log — it can be huge.
    The Memorizer is supposed to extract value, not summarise verbatim;
    the highlights (final output + a small slice of the most recent
    steps) are enough."""
    task_title = execution.get("task_title") or "(no title)"
    agent_role = agent.get("role") or "(unknown role)"
    status = execution.get("status") or "(unknown)"
    output = (execution.get("output") or "")[:1500]
    steps = execution.get("steps_log") or []
    # Show the most recent ~6 steps — that's where errors and useful
    # observations cluster; earlier steps are usually setup.
    tail = steps[-6:]
    tail_lines: list[str] = []
    for step in tail:
        kind = step.get("kind", "step") if isinstance(step, dict) else "step"
        note = (
            (step.get("note") or step.get("output") or step.get("content") or "")
            if isinstance(step, dict)
            else ""
        )
        if note:
            tail_lines.append(f"  - [{kind}] {str(note)[:200]}")
        else:
            tail_lines.append(f"  - [{kind}]")
    tail_block = "\n".join(tail_lines) if tail_lines else "  (no recent steps)"

    return (
        f"Execution status: {status}\n"
        f"Agent role: {agent_role}\n"
        f"Task: {task_title}\n"
        f"Final output (truncated):\n{output}\n\n"
        f"Last steps:\n{tail_block}\n\n"
        "Return the JSON array now."
    )


async def distil_execution(
    *,
    execution: Mapping[str, Any],
    agent: Mapping[str, Any],
    llm: LLMProvider,
    model: str | None = None,
) -> list[MemoryCandidate]:
    """Ask the LLM to extract memory candidates.

    Args:
        execution: A dict-shaped view of the `Execution` row (must
            carry at least `status`, `output`, `steps_log`; the
            optional `task_title` is used for context).
        agent: A dict-shaped view of the `Agent` row (uses `role` for
            prompt context).
        llm: any `LLMProvider`. Tests inject a fake.
        model: model id override; if None the provider picks its
            default.

    Returns:
        A list of 0..`MAX_CANDIDATES_PER_EXECUTION` candidates.
    """
    messages: Sequence[Message] = [
        Message(role="system", content=_SYSTEM_PROMPT),
        Message(role="user", content=_user_prompt(execution=execution, agent=agent)),
    ]
    try:
        response = await llm.complete(
            messages,
            model=model,
            max_tokens=_DEFAULT_MAX_TOKENS,
            temperature=0.2,
        )
    except Exception as exc:  # the LLM call failed — log and move on
        logger.warning("memorizer.llm_call_failed", error=str(exc))
        return []

    return _parse_response(response.content)


def _parse_response(text: str) -> list[MemoryCandidate]:
    """Best-effort JSON extraction.

    Strategies, in order:
      1. parse the whole response as JSON;
      2. find the first ``[...]`` block and parse that;
      3. give up and return [].
    """
    candidates_raw = _try_parse_json(text)
    if candidates_raw is None:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match is not None:
            candidates_raw = _try_parse_json(match.group(0))
    if not isinstance(candidates_raw, list):
        logger.info("memorizer.parse_failed", preview=text[:200])
        return []

    out: list[MemoryCandidate] = []
    for raw in candidates_raw:
        if len(out) >= MAX_CANDIDATES_PER_EXECUTION:
            break
        if not isinstance(raw, Mapping):
            continue
        content = raw.get("content")
        kind = raw.get("type")
        tags = raw.get("tags") or []
        if not isinstance(content, str) or not content.strip():
            continue
        if kind not in ("episodic", "semantic"):
            continue
        if not isinstance(tags, list):
            tags = []
        clean_tags = tuple(t.strip() for t in tags if isinstance(t, str) and t.strip())
        out.append(
            MemoryCandidate(
                content=content.strip()[:MAX_CONTENT_CHARS],
                type=kind,
                tags=clean_tags,
            )
        )
    return out


def _try_parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


__all__ = [
    "MAX_CANDIDATES_PER_EXECUTION",
    "MAX_CONTENT_CHARS",
    "MemoryCandidate",
    "distil_execution",
]
