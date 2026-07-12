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

from api_server.memorizer.recall import query_entity_terms

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
    # Normalised entities (ADR 0059 Opción A — idea nativa de mem0): the third
    # recall signal. Lowercased significant tokens (same tokenizer the recall
    # query uses, so write + read align). Empty when the LLM returns none.
    entities: tuple[str, ...] = field(default_factory=tuple)


_SYSTEM_PROMPT = (
    "You are the Memorizer of an agentic platform. After every "
    "execution you extract a SHORT list of useful memories to store for "
    "future agent runs. Always answer with a JSON array (no prose, no "
    "markdown fences). Each item is an object with exactly four keys:\n"
    '  "content"  — a single-paragraph fact (max 2000 chars).\n'
    '  "type"     — "episodic" (a concrete event) or "semantic" '
    "(a generalised rule extracted from one or more events).\n"
    '  "tags"     — array of short string tags for filtering, e.g. '
    '["sqlalchemy", "asyncpg"].\n'
    '  "entities" — array of the key NAMED ENTITIES the memory is about '
    "(people, projects, components, technologies, files), e.g. "
    '["PostgreSQL", "RLS", "agent-runtime"]. Used to find this memory later.\n'
    "Return between 0 and 5 items. Return an empty array if the "
    "execution produced nothing worth remembering.\n"
    "If the execution FAILED or was aborted, focus on the LESSON: the root "
    "cause, the dead end to avoid, or the precondition that was missing — "
    'prefer type "semantic" for those.'
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


@dataclass(frozen=True)
class DistillationResult:
    """El destilado + su CAUSA cuando viene vacío (F2.3, auditoría 2026-07-02).

    ``llm_empty`` conflataba tres situaciones muy distintas — fallo de la
    llamada LLM, respuesta no parseable y "no hay nada que recordar" (la única
    legítima) — haciendo indiagnosticables casos como el run done 019f1dcd.

    ``cause``: ``ok`` | ``llm_empty`` | ``llm_unparseable`` | ``llm_error``.
    """

    candidates: list[MemoryCandidate]
    cause: str


async def distil_execution_result(
    *,
    execution: Mapping[str, Any],
    agent: Mapping[str, Any],
    llm: LLMProvider,
    model: str | None = None,
) -> DistillationResult:
    """Como :func:`distil_execution` pero con la causa separada (F2.3).

    El worker persiste la causa como ``memorize_skip_reason`` cuando no hay
    candidatos, así el operador distingue "provider caído" de "modelo que no
    sigue el formato" de "nada que recordar".
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
        return DistillationResult(candidates=[], cause="llm_error")

    candidates, parse_cause = _parse_response_result(response.content)
    if candidates:
        return DistillationResult(candidates=candidates, cause="ok")
    return DistillationResult(candidates=[], cause=parse_cause)


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
    result = await distil_execution_result(execution=execution, agent=agent, llm=llm, model=model)
    return result.candidates


_HUMAN_SYSTEM_PROMPT = (
    "You are the Memorizer of an agentic platform. A HUMAN agent just finished "
    "a work session on a task. Extract a SHORT list of memories worth keeping "
    "for future plans — especially DECISIONS the human made, the context they "
    "made them in, and the outcome. Phrase semantic facts as reusable "
    'knowledge (e.g. "<name> decided to <decision> in <context>, which led to '
    '<outcome>"). Always answer with a JSON array (no prose, no markdown '
    "fences). Each item is an object with exactly four keys:\n"
    '  "content"  — a single-paragraph fact (max 2000 chars). When a human name '
    "is known, cite WHO made the decision.\n"
    '  "type"     — "episodic" (a concrete event of this session) or "semantic" '
    "(a generalised rule / decision rationale worth reusing).\n"
    '  "tags"     — array of short string tags for filtering, e.g. '
    '["legal-review", "brand"].\n'
    '  "entities" — array of the key NAMED ENTITIES the memory is about '
    "(people, projects, brands, documents), e.g. "
    '["Ana", "campaña-verano", "contrato-marco"]. Used to find this memory later.\n'
    "Return between 0 and 5 items. Return an empty array if the work session "
    "produced nothing worth remembering."
)


def _human_user_prompt(
    *, session: Mapping[str, Any], agent: Mapping[str, Any], user: Mapping[str, Any]
) -> str:
    """Compact human-readable summary of a finished :class:`HumanWorkSession`.

    Unlike :func:`_user_prompt` (which works off an AI execution's steps_log),
    a human session carries free-form ``comments`` (the human's notes / output)
    plus optional ``hours_logged`` and a list of attached deliverables. We
    surface WHO did the work so the LLM can cite the decision-maker.
    """
    task_title = session.get("task_title") or "(no title)"
    agent_role = agent.get("role") or "(human agent)"
    who = user.get("name") or "(an unnamed user)"
    comments = (session.get("comments") or "")[:1500]
    hours = session.get("hours_logged")
    attachments = session.get("output_files_attached") or []
    att_lines: list[str] = []
    for att in attachments[:6]:
        if isinstance(att, Mapping):
            label = att.get("name") or att.get("url") or att.get("title") or att.get("type")
            if label:
                att_lines.append(f"  - {str(label)[:200]}")
        elif isinstance(att, str) and att.strip():
            att_lines.append(f"  - {att.strip()[:200]}")
    att_block = "\n".join(att_lines) if att_lines else "  (no attachments)"
    hours_line = f"Hours logged: {hours}\n" if hours is not None else ""

    return (
        f"Human worker: {who}\n"
        f"Human agent role: {agent_role}\n"
        f"Task: {task_title}\n"
        f"{hours_line}"
        f"Human's notes / output (truncated):\n{comments}\n\n"
        f"Deliverables attached:\n{att_block}\n\n"
        "Return the JSON array now."
    )


async def distil_human_work_session(
    *,
    session: Mapping[str, Any],
    agent: Mapping[str, Any],
    user: Mapping[str, Any],
    llm: LLMProvider,
    model: str | None = None,
) -> list[MemoryCandidate]:
    """Ask the LLM to extract memory candidates from a human work session.

    The human equivalent of :func:`distil_execution` (which stays unchanged).
    Where an AI execution carries a ``steps_log``, a human session carries the
    human's free-form ``comments`` + attached deliverables; the prompt steers
    the LLM toward capturing the DECISION the human made and its outcome, with
    the worker's name so the memory can cite who decided what.

    Args:
        session: A dict-shaped view of the ``HumanWorkSession`` row (uses
            ``comments``, ``hours_logged``, ``output_files_attached`` and the
            optional ``task_title``).
        agent: A dict-shaped view of the human ``Agent`` row (uses ``role``).
        user: A dict-shaped view of the worker ``User`` (uses ``name`` for the
            citation).
        llm: any ``LLMProvider``. Tests inject a fake.
        model: model id override; if None the provider picks its default.

    Returns:
        A list of 0..``MAX_CANDIDATES_PER_EXECUTION`` candidates.
    """
    messages: Sequence[Message] = [
        Message(role="system", content=_HUMAN_SYSTEM_PROMPT),
        Message(
            role="user",
            content=_human_user_prompt(session=session, agent=agent, user=user),
        ),
    ]
    try:
        response = await llm.complete(
            messages,
            model=model,
            max_tokens=_DEFAULT_MAX_TOKENS,
            temperature=0.2,
        )
    except Exception as exc:  # the LLM call failed — log and move on
        logger.warning("memorizer.human_llm_call_failed", error=str(exc))
        return []

    return _parse_response(response.content)


def _parse_response(text: str) -> list[MemoryCandidate]:
    """Best-effort JSON extraction (compat). Ver `_parse_response_result`."""
    return _parse_response_result(text)[0]


def _parse_response_result(text: str) -> tuple[list[MemoryCandidate], str]:
    """Best-effort JSON extraction, con causa (F2.3).

    Strategies, in order:
      1. parse the whole response as JSON;
      2. find the first ``[...]`` block and parse that;
      3. give up → ``([], "llm_unparseable")``.

    Un array parseado sin candidatos válidos → ``([], "llm_empty")`` (la causa
    legítima: el LLM decidió que no hay nada que recordar).
    """
    candidates_raw = _try_parse_json(text)
    if candidates_raw is None:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match is not None:
            candidates_raw = _try_parse_json(match.group(0))
    if not isinstance(candidates_raw, list):
        logger.info("memorizer.parse_failed", preview=text[:200])
        return [], "llm_unparseable"

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
        entities_raw = raw.get("entities") or []
        if not isinstance(entities_raw, list):
            entities_raw = []
        # Normalise with the SAME tokenizer the recall query uses, so the stored
        # entities and the query terms match on shared tokens (ADR 0059).
        clean_entities = tuple(
            query_entity_terms(" ".join(e for e in entities_raw if isinstance(e, str)))
        )
        out.append(
            MemoryCandidate(
                content=content.strip()[:MAX_CONTENT_CHARS],
                type=kind,
                tags=clean_tags,
                entities=clean_entities,
            )
        )
    return out, "llm_empty" if not out else "ok"


def _try_parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


__all__ = [
    "MAX_CANDIDATES_PER_EXECUTION",
    "MAX_CONTENT_CHARS",
    "DistillationResult",
    "MemoryCandidate",
    "distil_execution",
    "distil_execution_result",
    "distil_human_work_session",
]
