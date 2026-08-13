"""Unit tests for the Memorizer distillation step (Plan 04 task_04_03).

Exercises the pure async distiller against a fake `LLMProvider` that
returns canned responses — so we can assert how the parser handles
clean JSON, markdown-fenced JSON, malformed JSON, partial fields,
oversized payloads and LLM-side errors.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest
from api_server.memorizer.distillation import (
    MAX_CANDIDATES_PER_EXECUTION,
    MAX_CONTENT_CHARS,
    MemoryCandidate,
    distil_execution,
    distil_execution_result,
)
from shared_llm.types import CompletionResponse, Message, StreamChunk, Usage

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fake LLM provider
# ---------------------------------------------------------------------------
class FakeLLM:
    """Minimal `LLMProvider`-shaped fake.

    Returns a fixed `content` (or raises if `raises` is set). Records
    the messages it received so tests can assert on the prompt.
    """

    name = "fake"

    def __init__(self, *, content: str = "[]", raises: Exception | None = None) -> None:
        self.content = content
        self.raises = raises
        self.last_messages: Sequence[Message] | None = None
        self.last_kwargs: dict[str, Any] | None = None

    async def complete(
        self,
        messages: Sequence[Message],
        **kwargs: Any,
    ) -> CompletionResponse:
        self.last_messages = list(messages)
        self.last_kwargs = kwargs
        if self.raises is not None:
            raise self.raises
        return CompletionResponse(
            content=self.content,
            model=kwargs.get("model") or "fake-model",
            provider=self.name,
            usage=Usage(),
            tool_calls=None,
            raw={},
        )

    async def stream(
        self,
        messages: Sequence[Message],
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:  # pragma: no cover — not used in this module
        yield StreamChunk(delta="", usage=None, raw={})

    async def aclose(self) -> None:  # pragma: no cover
        pass


_EXECUTION = {
    "status": "done",
    "output": "Fixed the asyncpg import after psycopg3 was missing.",
    "steps_log": [
        {"kind": "tool_call", "note": "tried psycopg3.connect — ImportError"},
        {"kind": "tool_call", "note": "switched to asyncpg.connect"},
        {"kind": "observation", "note": "tests pass"},
    ],
    "task_title": "Add an /events endpoint",
}
_AGENT = {"role": "backend-dev"}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_clean_json_yields_candidates_in_order() -> None:
    llm = FakeLLM(
        content=(
            '[{"content": "Project uses asyncpg, not psycopg3.",'
            ' "type": "semantic", "tags": ["sqlalchemy", "asyncpg"]},'
            ' {"content": "On 2026-05-25 the backend-dev fixed a missing-import bug.",'
            ' "type": "episodic", "tags": []}]'
        )
    )
    cands = await distil_execution(execution=_EXECUTION, agent=_AGENT, llm=llm)
    assert len(cands) == 2
    assert cands[0].type == "semantic"
    assert cands[0].tags == ("sqlalchemy", "asyncpg")
    assert cands[1].type == "episodic"
    assert cands[1].tags == ()


@pytest.mark.asyncio
async def test_markdown_fenced_json_is_parsed() -> None:
    """Models love wrapping JSON in fences; the parser unwraps it."""
    llm = FakeLLM(content=('```json\n[{"content": "Foo", "type": "semantic", "tags": []}]\n```'))
    cands = await distil_execution(execution=_EXECUTION, agent=_AGENT, llm=llm)
    assert len(cands) == 1
    assert cands[0].content == "Foo"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_empty_array_yields_empty_list() -> None:
    cands = await distil_execution(execution=_EXECUTION, agent=_AGENT, llm=FakeLLM(content="[]"))
    assert cands == []


# ---------------------------------------------------------------------------
# F2.3 (auditoría 2026-07-02): distil_execution_result separa las 3 causas que
# `llm_empty` conflataba — fallo de la llamada LLM, respuesta no parseable y
# "el LLM decidió que no hay nada que recordar" (la única legítima). El caso
# 019f1dcd (run done con skip llm_empty) era indiagnosticable sin esto.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_result_reports_llm_error_cause() -> None:
    llm = FakeLLM(raises=RuntimeError("connection refused"))
    result = await distil_execution_result(execution=_EXECUTION, agent=_AGENT, llm=llm)
    assert result.candidates == []
    assert result.cause == "llm_error"


@pytest.mark.asyncio
async def test_result_reports_unparseable_cause() -> None:
    llm = FakeLLM(content="I could not produce JSON, sorry.")
    result = await distil_execution_result(execution=_EXECUTION, agent=_AGENT, llm=llm)
    assert result.candidates == []
    assert result.cause == "llm_unparseable"


@pytest.mark.asyncio
async def test_result_reports_legit_empty_cause() -> None:
    result = await distil_execution_result(
        execution=_EXECUTION, agent=_AGENT, llm=FakeLLM(content="[]")
    )
    assert result.candidates == []
    assert result.cause == "llm_empty"


@pytest.mark.asyncio
async def test_result_ok_cause_with_candidates() -> None:
    llm = FakeLLM(content='[{"content": "Foo", "type": "semantic", "tags": []}]')
    result = await distil_execution_result(execution=_EXECUTION, agent=_AGENT, llm=llm)
    assert result.cause == "ok"
    assert len(result.candidates) == 1


@pytest.mark.asyncio
async def test_invalid_type_is_skipped() -> None:
    """Type must be 'episodic' or 'semantic'; anything else is dropped."""
    llm = FakeLLM(
        content=(
            '[{"content": "good", "type": "episodic", "tags": []},'
            ' {"content": "bad", "type": "notes", "tags": []}]'
        )
    )
    cands = await distil_execution(execution=_EXECUTION, agent=_AGENT, llm=llm)
    assert [c.content for c in cands] == ["good"]


@pytest.mark.asyncio
async def test_empty_content_is_skipped() -> None:
    llm = FakeLLM(
        content=(
            '[{"content": "  ", "type": "episodic", "tags": []},'
            ' {"content": "kept", "type": "episodic", "tags": []}]'
        )
    )
    cands = await distil_execution(execution=_EXECUTION, agent=_AGENT, llm=llm)
    assert [c.content for c in cands] == ["kept"]


@pytest.mark.asyncio
async def test_garbage_response_returns_empty_list() -> None:
    llm = FakeLLM(content="I'm sorry, I cannot do that, Dave.")
    cands = await distil_execution(execution=_EXECUTION, agent=_AGENT, llm=llm)
    assert cands == []


@pytest.mark.asyncio
async def test_llm_error_returns_empty_list_not_raises() -> None:
    """Memorisation is fire-and-forget — never break the execution
    pipeline because the LLM is down."""
    llm = FakeLLM(raises=RuntimeError("ollama unreachable"))
    cands = await distil_execution(execution=_EXECUTION, agent=_AGENT, llm=llm)
    assert cands == []


@pytest.mark.asyncio
async def test_oversized_content_is_truncated() -> None:
    big = "x" * (MAX_CONTENT_CHARS + 500)
    llm = FakeLLM(content=f'[{{"content": "{big}", "type": "episodic", "tags": []}}]')
    cands = await distil_execution(execution=_EXECUTION, agent=_AGENT, llm=llm)
    assert len(cands) == 1
    assert len(cands[0].content) == MAX_CONTENT_CHARS


@pytest.mark.asyncio
async def test_too_many_candidates_are_capped() -> None:
    items = ",".join(
        f'{{"content": "x{i}", "type": "episodic", "tags": []}}'
        for i in range(MAX_CANDIDATES_PER_EXECUTION + 3)
    )
    llm = FakeLLM(content=f"[{items}]")
    cands = await distil_execution(execution=_EXECUTION, agent=_AGENT, llm=llm)
    assert len(cands) == MAX_CANDIDATES_PER_EXECUTION


@pytest.mark.asyncio
async def test_tags_are_stripped_and_filtered() -> None:
    llm = FakeLLM(
        content=(
            '[{"content": "x", "type": "semantic", "tags": ["  ok ", "", null, 42, "  good"]}]'
        )
    )
    cands = await distil_execution(execution=_EXECUTION, agent=_AGENT, llm=llm)
    assert cands[0].tags == ("ok", "good")


# ---------------------------------------------------------------------------
# Prompt sanity
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_user_prompt_carries_task_and_agent_context() -> None:
    llm = FakeLLM(content="[]")
    await distil_execution(execution=_EXECUTION, agent=_AGENT, llm=llm)
    assert llm.last_messages is not None
    user_msg = llm.last_messages[-1]
    assert user_msg.role == "user"
    assert "backend-dev" in user_msg.content
    assert "Add an /events endpoint" in user_msg.content


@pytest.mark.asyncio
async def test_steps_log_tail_is_included() -> None:
    """The last steps should make it into the prompt — that's where
    the interesting stuff lives."""
    llm = FakeLLM(content="[]")
    await distil_execution(execution=_EXECUTION, agent=_AGENT, llm=llm)
    user_msg = llm.last_messages[-1]
    assert "asyncpg.connect" in user_msg.content
    assert "tests pass" in user_msg.content


@pytest.mark.asyncio
async def test_immutability_of_candidate() -> None:
    cand = MemoryCandidate(content="x", type="episodic", tags=("a",))
    with pytest.raises((AttributeError, Exception)):
        cand.content = "y"  # type: ignore[misc]
