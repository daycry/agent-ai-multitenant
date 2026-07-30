"""Unit tests for the per-task acceptance-criteria generation service.

The generator drives any ``shared_llm.LLMProvider`` (ADR 0021) to propose 2-5
descriptive, verifiable acceptance criteria for a SINGLE task, reusing the
planner's cleaner (``_clean_acceptance_criteria``). It NEVER persists; the
endpoint returns the proposal and the operator confirms + saves via the PUT.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any

import pytest
from api_server.chat.criteria_llm import (
    build_criteria_messages,
    format_sibling_context,
    generate_task_acceptance_criteria,
)
from shared_llm.types import CompletionResponse, Message, StreamChunk


@dataclass
class _FakeProvider:
    """Returns a fixed `content` for every complete() call; records messages."""

    content: str
    name: str = "fake"
    seen: list[list[Message]] | None = None

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> CompletionResponse:
        if self.seen is not None:
            self.seen.append(list(messages))
        return CompletionResponse(content=self.content, model=model or "m", provider=self.name)

    async def stream(self, *a: Any, **k: Any) -> AsyncIterator[StreamChunk]:  # pragma: no cover
        yield StreamChunk(delta="", done=True)

    async def aclose(self) -> None:  # pragma: no cover
        return None


def _text(messages: list[Message]) -> str:
    return "\n".join(m.content for m in messages)


# --------------------------------------------------------------------------
# build_criteria_messages (pure)
# --------------------------------------------------------------------------
def test_build_messages_requests_json_and_includes_task() -> None:
    msgs = build_criteria_messages(
        title="Auditar dependencias",
        description="Revisar composer.lock y vulnerabilidades",
        existing=[],
        project_context={"name": "Api CI", "description": "API CodeIgniter 4"},
    )
    assert msgs and msgs[0].role == "system"
    blob = _text(msgs)
    assert "acceptance_criteria" in blob  # the exact JSON key the parser expects
    assert "Auditar dependencias" in blob
    assert "composer.lock" in blob
    # No existing criteria -> no "Criterios actuales" block.
    assert "Criterios actuales" not in blob


def test_format_sibling_context_lists_titles_and_criteria() -> None:
    ctx = format_sibling_context(
        [
            ("Definir contrato", ["el cuerpo usa {message, meta}", "errores 404/405"]),
            ("Configurar CI", ["el YAML declara el paso de linter"]),
        ]
    )
    assert "Definir contrato" in ctx
    assert "el cuerpo usa {message, meta}" in ctx
    assert "Configurar CI" in ctx


def test_format_sibling_context_empty_when_no_siblings() -> None:
    assert format_sibling_context([]) == ""
    # Siblings with no criteria contribute nothing usable.
    assert format_sibling_context([("Tarea sin criterios", [])]).strip() != ""  # still lists title


def test_build_messages_includes_sibling_context_and_coherence_rule() -> None:
    msgs = build_criteria_messages(
        title="Implementar controladores",
        description=None,
        existing=[],
        project_context={},
        sibling_context="- Definir contrato: el cuerpo usa {message, meta}",
    )
    blob = _text(msgs)
    assert "el cuerpo usa {message, meta}" in blob
    # A coherence instruction must be present so the model does not contradict siblings.
    assert "hermana" in blob.lower() or "coheren" in blob.lower()


def test_build_messages_includes_existing_and_asks_to_refine() -> None:
    msgs = build_criteria_messages(
        title="Tarea",
        description=None,
        existing=["ya existe este criterio"],
        project_context={},
    )
    blob = _text(msgs)
    assert "Criterios actuales" in blob
    assert "ya existe este criterio" in blob


# --------------------------------------------------------------------------
# generate_task_acceptance_criteria (async, drives the provider)
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_generate_parses_wrapped_object() -> None:
    provider = _FakeProvider('{"acceptance_criteria": ["uno", "dos"]}')
    got = await generate_task_acceptance_criteria(
        provider, title="t", description="d", existing=[], project_context={}, model="m"
    )
    assert got == ["uno", "dos"]


@pytest.mark.asyncio
async def test_generate_parses_bare_array() -> None:
    provider = _FakeProvider('["uno", "dos", "tres"]')
    got = await generate_task_acceptance_criteria(
        provider, title="t", description="d", existing=[], project_context={}, model="m"
    )
    assert got == ["uno", "dos", "tres"]


@pytest.mark.asyncio
async def test_generate_parses_wrapped_object_with_trailing_prose() -> None:
    # A less-obedient model may append a note after the JSON — and that note may
    # itself contain braces. A greedy ``{.*}`` over-captures and drops everything;
    # a first-complete-value decode must still recover the criteria.
    provider = _FakeProvider(
        '{"acceptance_criteria": ["a", "b"]}\n\nNota: he seguido el formato {exacto}.'
    )
    got = await generate_task_acceptance_criteria(
        provider, title="t", description="d", existing=[], project_context={}, model="m"
    )
    assert got == ["a", "b"]


@pytest.mark.asyncio
async def test_generate_parses_json_in_markdown_fence() -> None:
    provider = _FakeProvider('```json\n{"acceptance_criteria": ["uno", "dos"]}\n```')
    got = await generate_task_acceptance_criteria(
        provider, title="t", description="d", existing=[], project_context={}, model="m"
    )
    assert got == ["uno", "dos"]


@pytest.mark.asyncio
async def test_generate_caps_count_and_length() -> None:
    items = [f"criterio {i}" for i in range(20)]
    long_item = "x" * 400
    import json

    provider = _FakeProvider(json.dumps({"acceptance_criteria": [long_item, *items]}))
    got = await generate_task_acceptance_criteria(
        provider, title="t", description="d", existing=[], project_context={}, model="m"
    )
    assert len(got) <= 8
    assert all(len(c) <= 300 for c in got)


@pytest.mark.asyncio
async def test_generate_returns_empty_on_garbage() -> None:
    provider = _FakeProvider("lo siento, no puedo ayudarte con eso")
    got = await generate_task_acceptance_criteria(
        provider, title="t", description="d", existing=[], project_context={}, model="m"
    )
    assert got == []


@pytest.mark.asyncio
async def test_generate_passes_existing_into_prompt() -> None:
    seen: list[list[Message]] = []
    provider = _FakeProvider('{"acceptance_criteria": ["x"]}', seen=seen)
    await generate_task_acceptance_criteria(
        provider,
        title="t",
        description="d",
        existing=["criterio previo del reviewer"],
        project_context={},
        model="m",
    )
    assert seen, "provider.complete was not called"
    blob = "\n".join(m.content for m in seen[-1])
    assert "criterio previo del reviewer" in blob


@pytest.mark.asyncio
async def test_generate_passes_sibling_context_into_prompt() -> None:
    seen: list[list[Message]] = []
    provider = _FakeProvider('{"acceptance_criteria": ["x"]}', seen=seen)
    await generate_task_acceptance_criteria(
        provider,
        title="t",
        description="d",
        existing=[],
        project_context={},
        model="m",
        sibling_context="- Definir contrato: el cuerpo usa {message, meta}",
    )
    blob = "\n".join(m.content for m in seen[-1])
    assert "el cuerpo usa {message, meta}" in blob
