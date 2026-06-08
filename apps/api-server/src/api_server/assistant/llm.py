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

from dataclasses import dataclass
from typing import cast

from shared_llm.base import LLMProvider
from shared_llm.types import Message as LLMMessage
from shared_llm.types import Role

from api_server.assistant.graph import AssistantState, ModelTurn, ToolInvocation
from api_server.assistant.tools import tool_schemas


@dataclass
class LLMAssistantModel:
    """Adapt an ``LLMProvider`` to the assistant graph's ``decide`` seam.

    ``decide`` is async: the graph node awaits it on the request's event
    loop, so the async ``provider.complete()`` runs on that same loop. (It
    used to be sync and bridge to async via a worker-thread ``asyncio.run``
    per call — but that closed a fresh loop each round, and a provider's
    pooled httpx connection from round 1 then blew up on round 2 with
    "Event loop is closed" on Windows. Awaiting directly avoids the whole
    cross-loop problem.) Each call sends the system prompt + chat history +
    accumulated tool results and lets the model either call more tools or
    answer.
    """

    provider: LLMProvider
    model: str | None = None
    max_tokens: int = 1024
    temperature: float = 0.3

    async def decide(self, state: AssistantState) -> ModelTurn:
        messages = self._build_messages(state)
        schemas = tool_schemas(state.enabled_tools)
        # Wrap each schema in the OpenAI-style {type:function,function:{...}}
        # envelope most providers expect; harmless for those that ignore it.
        tools = [{"type": "function", "function": s} for s in schemas] if schemas else None

        response = await self.provider.complete(
            messages,
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            tools=tools,
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


__all__ = ["LLMAssistantModel"]
