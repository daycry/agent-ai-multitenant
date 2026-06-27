"""claude_sdk review path — adapter ↔ provider wiring (ADR 0086/0087, phase B).

`test_review_verdict` exercises `_review_from` with a SimpleNamespace response.
This pins the OTHER half: the real `ClaudeSDKModelClient.review()` driving the
actual `ClaudeAgentProvider` host-tool path (the deny+interrupt harvest). It is
the seam the analysis flagged as untested — two client classes that can drift and
the claude_sdk ``content=""``-when-a-tool-fires surprise.

The SDK names the in-process MCP tool ``mcp__<server>__submit_verdict``; the
provider trims it to the base ``submit_verdict`` the host registered. We inject a
fake ``query_fn`` (the SDK message stream) so no real CLI/SDK is needed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from agent_runtime.providers import ClaudeSDKModelClient


@dataclass
class _TextBlock:
    text: str


@dataclass
class _ToolUseBlock:
    name: str
    input: dict[str, Any]
    id: str = "tu_1"


@dataclass
class _AssistantMessage:
    content: list[Any]


@dataclass
class _UsageBlock:
    input_tokens: int = 1
    output_tokens: int = 1
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class _ResultMessage:
    total_cost_usd: float
    usage: _UsageBlock


def _make_query(*messages: Any):  # type: ignore[no-untyped-def]
    async def _q(prompt: str, options: Any) -> AsyncIterator[Any]:  # noqa: ARG001
        for m in messages:
            yield m

    return _q


_STATE = {"task": {"title": "JWT auth", "description": "implement it"}, "output": "I wrote it."}


def _client(*messages: Any) -> ClaudeSDKModelClient:  # type: ignore[no-untyped-def]
    return ClaudeSDKModelClient(model="claude-x", query_fn=_make_query(*messages))


def test_review_reads_submit_verdict_tool_call_through_provider() -> None:
    # The model calls submit_verdict (named as an MCP tool) → structured verdict.
    client = _client(
        _AssistantMessage(
            content=[
                _ToolUseBlock(
                    name="mcp__host_tools__submit_verdict",
                    input={"passed": False, "feedback": "falta el refresh"},
                    id="tu_9",
                )
            ]
        ),
    )
    r = client.review(_STATE)
    assert r.passed is False and r.inconclusive is False and r.feedback == "falta el refresh"


def test_review_prose_fallback_explicit_reject_through_provider() -> None:
    # CLI degrades to prose with an explicit rejection → fail (not inconclusive).
    client = _client(
        _AssistantMessage(content=[_TextBlock("La salida no cumple el criterio 2.")]),
        _ResultMessage(total_cost_usd=0.0, usage=_UsageBlock()),
    )
    r = client.review(_STATE)
    assert r.passed is False and r.inconclusive is False


def test_review_prose_fallback_ambiguous_is_inconclusive_through_provider() -> None:
    # CLI degrades to ambiguous prose (auth domain words, no clear verdict) →
    # INCONCLUSIVE → the loop escalates to a human (ADR 0087), never a silent pass.
    client = _client(
        _AssistantMessage(
            content=[_TextBlock("El filtro rechaza tokens inválidos y maneja el fallo.")]
        ),
        _ResultMessage(total_cost_usd=0.0, usage=_UsageBlock()),
    )
    r = client.review(_STATE)
    assert r.passed is False and r.inconclusive is True
