"""LLM-backed ``AssistantModelClient`` adapter (Plan 10 task_10_14).

Bridges the assistant sub-graph's synchronous ``decide`` seam onto an
async ``shared_llm.LLMProvider`` (ADR 0021). The graph stays
provider-agnostic; this is the single place that knows about
``LLMProvider`` and how to translate its ``CompletionResponse`` /
``ToolCall`` shapes into the graph's ``ModelTurn`` / ``ToolInvocation``.

Tests do NOT use this adapter — they inject a ``ScriptedAssistantModel``
through the router's ``get_assistant_model`` dependency override, so no
real provider is ever contacted (the established chat-test pattern).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, cast

from shared_llm.base import LLMProvider
from shared_llm.types import Message as LLMMessage
from shared_llm.types import Role

from api_server.assistant.graph import AssistantState, ModelTurn, ToolInvocation
from api_server.assistant.tools import tool_schemas


@dataclass
class LLMAssistantModel:
    """Adapt an ``LLMProvider`` to the assistant graph's ``decide`` seam.

    ``decide`` is synchronous (the graph calls it inside an async node but
    expects a value back); the provider is async, so we drive one
    ``complete()`` call to completion on a fresh event loop slice. Each
    call sends the system prompt + chat history + accumulated tool
    results and lets the model either call more tools or answer.
    """

    provider: LLMProvider
    model: str | None = None
    max_tokens: int = 1024
    temperature: float = 0.3

    def decide(self, state: AssistantState) -> ModelTurn:
        messages = self._build_messages(state)
        schemas = tool_schemas(state.enabled_tools)
        # Wrap each schema in the OpenAI-style {type:function,function:{...}}
        # envelope most providers expect; harmless for those that ignore it.
        tools = [{"type": "function", "function": s} for s in schemas] if schemas else None

        response = _run_sync(
            self.provider.complete(
                messages,
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                tools=tools,
            )
        )
        if response.tool_calls:
            calls = tuple(
                ToolInvocation(name=tc.name, arguments=dict(tc.arguments))
                for tc in response.tool_calls
            )
            return ModelTurn(content=response.content or None, tool_calls=calls)
        return ModelTurn(content=response.content or "", tool_calls=())

    def _build_messages(self, state: AssistantState) -> list[LLMMessage]:
        messages: list[LLMMessage] = [LLMMessage(role="system", content=state.system_prompt)]
        for entry in state.chat_history:
            raw_role = str(entry.get("role", "user"))
            role: Role = (
                cast(Role, raw_role)
                if raw_role in ("user", "assistant", "system", "tool")
                else "user"
            )
            messages.append(LLMMessage(role=role, content=str(entry.get("content", ""))))
        # Feed accumulated tool results back as a system note so the model
        # can ground its answer on real data.
        if state.tool_results:
            summary = "\n".join(f"[{r['tool']}] {r['result']}" for r in state.tool_results)
            messages.append(
                LLMMessage(
                    role="system",
                    content=f"Resultados de herramientas:\n{summary}",
                )
            )
        return messages


def _run_sync(coro: Any) -> Any:
    """Run an async coroutine to completion from a sync context.

    The graph node that calls ``decide`` is itself async, but ``decide``
    is a sync method on the Protocol (so the scripted test double stays
    trivial). We therefore drive the provider coroutine on its own loop.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        # Already inside a loop (the graph node) — run on a private loop in
        # a worker thread to avoid re-entrancy.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


__all__ = ["LLMAssistantModel"]
