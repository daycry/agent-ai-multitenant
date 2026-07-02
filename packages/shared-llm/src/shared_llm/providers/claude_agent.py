"""Claude Agent SDK wrapper (ADR 0021).

The SDK is not just a chat client — it runs an agent loop with tool
use, filesystem access, MCP, sub-agents, etc. Two modes are exposed
here:

  * **`complete()` / `stream()`** — fits the `LLMProvider` Protocol.
    Runs the SDK with `max_turns=1`, no tools, no host-side state.
    Use when you want "Claude as a chat backend".
  * **`run_agent()`** — escape hatch that yields `AgentRunEvent`s
    (typed wrapper around the SDK's heterogeneous messages). Use when
    you want the SDK's full agent capabilities.

`claude-agent-sdk` is an optional dependency (extra `claude` in the
package's pyproject). The provider only imports it when actually used,
so a deployment without Claude doesn't drag the SDK + the Node CLI in.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Sequence
from typing import Any

from shared_llm.exceptions import AuthError, LLMError, ProviderError
from shared_llm.types import (
    AgentRunEvent,
    CompletionResponse,
    Message,
    StreamChunk,
    ToolCall,
    Usage,
)

# In-process MCP server name used to advertise host tool schemas to the SDK.
# The SDK namespaces these tools as ``mcp__{_HOST_TOOLS_SERVER}__{tool}``.
_HOST_TOOLS_SERVER = "host_tools"

# The Claude Agent SDK exposes its full native toolset (Claude Code's tools) to
# the model by default. When we advertise the platform's HOST tools (MCP,
# host-executed), the model must use THOSE: a native tool call is harvested with
# its native name, which the host's ToolRegistry doesn't know and rejects
# ("tool '<X>' not allowed in this mode") — the agent then spins on rejected
# calls and times out. So on the host-tool path we DISABLE the natives via
# ``disallowed_tools``; a caller re-enables specific ones (the córtex's
# WebSearch/WebFetch, ADR 0076) by listing them in ``allowed_tools``, which is
# subtracted from this set.
_SDK_NATIVE_TOOLS: tuple[str, ...] = (
    "Bash",
    "BashOutput",
    "KillBash",
    "KillShell",
    "Read",
    "Write",
    "Edit",
    "NotebookEdit",
    "Glob",
    "Grep",
    "Task",
    "Agent",
    "TodoWrite",
    "ToolSearch",
    "AskUserQuestion",
    "Workflow",
    "ExitPlanMode",
    "WebSearch",
    "WebFetch",
    "ListMcpResources",
    "ReadMcpResource",
)


def _model_usage_tokens(mu: Any) -> tuple[int, int]:
    """Suma (input, output) del mapa ``model_usage`` del ResultMessage (F1.4).

    El CLI lo emite por modelo y en camelCase (``inputTokens``); se aceptan
    ambas formas. ``(0, 0)`` cuando no hay mapa."""
    total_in = total_out = 0
    if isinstance(mu, dict):
        for per_model in mu.values():
            total_in += _usage_get(per_model, "inputTokens") or _usage_get(
                per_model, "input_tokens"
            )
            total_out += _usage_get(per_model, "outputTokens") or _usage_get(
                per_model, "output_tokens"
            )
    return total_in, total_out


def _usage_get(u: Any, name: str, default: int = 0) -> int:
    """Read a usage field whether the SDK exposes ``usage`` as an OBJECT (attribute)
    or a DICT (key). The Claude Agent SDK's ResultMessage carries ``total_cost_usd``
    as a message attribute but its ``usage`` may arrive as a plain dict — in which
    case ``getattr(u, "input_tokens")`` silently returned 0, so runs showed cost>0
    with tokens=0. This reads both shapes."""
    if u is None:
        return default
    val = u.get(name, default) if isinstance(u, dict) else getattr(u, name, default)
    return int(val or 0)


# Markers in the CLI's error ``result`` text that mean "fix your credential", so a
# failed run raises the typed ``AuthError`` (actionable: tell the operator to set
# the provider's api_key / oauth_token — ADR 0064) instead of a generic error.
_AUTH_RESULT_MARKERS = (
    "not logged in",
    "/login",
    "invalid api key",
    "invalid x-api-key",
    "authentication",
    "unauthorized",
    "oauth",
    "credit balance",
)


def _surface_result_error(collected: list[Any]) -> str | None:
    """Recover the human-readable failure reason from a failing ``ResultMessage``.

    The SDK replaces the CLI's trailing non-zero exit with "Claude Code returned
    an error result: <subtype>", built from the ``errors`` field only — so an auth
    failure whose real reason lives in ``result`` ("Not logged in · Please run
    /login") degrades to the useless "...: success". We read the real text back off
    the result message the SDK already yielded before raising.
    """
    for msg in reversed(collected):
        if not getattr(msg, "is_error", False):
            continue
        text = (getattr(msg, "result", None) or "").strip()
        if not text:
            errors = getattr(msg, "errors", None) or []
            text = "; ".join(str(e) for e in errors).strip()
        if not text:
            continue
        status = getattr(msg, "api_error_status", None)
        return f"{text} (HTTP {status})" if status else text
    return None


def _run_error(exc: Exception, collected: list[Any]) -> LLMError:
    """Map a failed SDK run to a typed error, preferring the CLI's real reason over
    the SDK's cryptic 'error result: <subtype>'. Auth failures become ``AuthError``
    (so the assistant's handler tells the operator to fix the provider credential —
    ADR 0064); everything else is a ``ProviderError`` carrying the real text."""
    surfaced = _surface_result_error(collected)
    message = surfaced or str(exc)
    if surfaced and any(marker in surfaced.lower() for marker in _AUTH_RESULT_MARKERS):
        return AuthError(message)
    return ProviderError(message)


class ClaudeAgentProvider:
    name = "claude_agent"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        # Pro/Max subscription token from `claude setup-token` (ADR 0063). The
        # Claude Agent SDK reads CLAUDE_CODE_OAUTH_TOKEN to authenticate against
        # a subscription WITHOUT an API key — the alternative auth mode for the
        # same `claude_sdk` provider kind.
        oauth_token: str | None = None,
        default_model: str = "claude-sonnet-4-5",
        # The completion-shaped path keeps tools off by default — the
        # `run_agent()` path is where tools belong.
        default_allowed_tools: list[str] | None = None,
        default_system_prompt: str | None = None,
        # Injectable for tests: a callable with the same shape as
        # claude_agent_sdk.query. None -> real SDK is loaded lazily.
        query_fn: Any | None = None,
    ) -> None:
        if api_key:
            os.environ["ANTHROPIC_API_KEY"] = api_key
        if oauth_token:
            os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token
        if (
            not (api_key or oauth_token)
            and not os.environ.get("ANTHROPIC_API_KEY")
            and query_fn is None
        ):
            # Pro/Max subscription users may rely on ambient auth (a token
            # already in the environment / the SDK's own credentials). We do
            # NOT fail here — the SDK surfaces an auth error at call time.
            pass
        self._default_model = default_model
        self._default_allowed_tools = default_allowed_tools or []
        self._default_system_prompt = default_system_prompt
        self._query_fn = query_fn

    # ------------------------------------------------------------------
    # Internals — lazy import keeps the SDK optional
    # ------------------------------------------------------------------
    def _query(self) -> Any:
        if self._query_fn is not None:
            return self._query_fn
        # Lazy import — claude-agent-sdk is an optional extra of this
        # package. Loading it at module import time would force every
        # deployment (Azure / Copilot / Ollama) to carry the SDK and
        # its Node CLI dependency.
        try:
            from claude_agent_sdk import query
        except ImportError as exc:
            raise ImportError(
                "claude-agent-sdk is not installed. " "Run `pip install 'shared-llm[claude]'`."
            ) from exc
        return query

    def _build_options(
        self,
        *,
        model: str | None,
        system: str | None,
        allowed_tools: list[str] | None,
        max_turns: int,
        effort: str | None = None,
        disallow_native_tools: bool = False,
    ) -> Any:
        if self._query_fn is not None:
            return None  # the injected fake accepts whatever we pass
        try:
            from claude_agent_sdk import ClaudeAgentOptions
        except ImportError as exc:
            raise ImportError(
                "claude-agent-sdk is not installed. " "Run `pip install 'shared-llm[claude]'`."
            ) from exc
        # ADR 0070: extended-thinking effort (EffortLevel: low/medium/high/xhigh/max).
        # Solo se pasa cuando hay valor — así seguimos compatibles con SDKs sin el
        # campo `effort`; `None` (off) reproduce el comportamiento previo.
        extra: dict[str, Any] = {}
        if effort:
            extra["effort"] = effort
        resolved_allowed = (
            allowed_tools if allowed_tools is not None else self._default_allowed_tools
        )
        # F31/P1.6: the chat-shaped path (`complete()`/`stream()` con `tools` vacías)
        # NO debe permitir que el SDK auto-ejecute sus tools NATIVAS (Bash/Write/
        # Read/WebSearch…) fuera del ToolRegistry/approval/loop-detection del host.
        # Sin esto, un `decide()` "sin tools" podía disparar ejecución nativa fuera
        # del lazo mediado por el host. Desactivamos las nativas salvo las que el
        # caller permita explícitamente (córtex WebSearch/WebFetch, ADR 0076). El
        # camino `run_agent()` (escape hatch agéntico) NO activa esto: ahí las
        # nativas son justo lo que se quiere.
        if disallow_native_tools:
            _allowed = set(resolved_allowed)
            disabled = [name for name in _SDK_NATIVE_TOOLS if name not in _allowed]
            if disabled:
                extra["disallowed_tools"] = disabled
        return ClaudeAgentOptions(
            model=model or self._default_model,
            system_prompt=system if system is not None else self._default_system_prompt,
            allowed_tools=resolved_allowed,
            max_turns=max_turns,
            **extra,
        )

    @staticmethod
    def _flatten(messages: Sequence[Message]) -> tuple[str | None, str]:
        """Join `messages` into (system, prompt). The SDK takes a string
        prompt and an optional system block; the chat history collapses
        into a `Human:`/`Assistant:` transcript."""
        system_parts = [m.content for m in messages if m.role == "system"]
        system = "\n\n".join(system_parts) if system_parts else None
        convo: list[str] = []
        for m in messages:
            if m.role == "system":
                continue
            tag = "Human" if m.role == "user" else "Assistant"
            convo.append(f"{tag}: {m.content}")
        return system, "\n\n".join(convo)

    @staticmethod
    def _harvest(messages: list[Any]) -> tuple[list[str], Usage]:
        """Walk SDK messages: collect text blocks + the turn's usage.

        Auditoría 2026-07-02 (F1.4): en un turno con tool call interrumpido
        (``can_use_tool`` deny+interrupt) el ``ResultMessage`` llega sin
        ``usage`` — o no llega — así que los runs cuyo cada turno acababa en
        tool call persistían ``total_tokens=0`` con ``cost>0``. La cosecha usa
        tres canales por orden de autoridad:

          1. el ``usage`` agregado del ResultMessage (si trae tokens);
          2. la SUMA de los ``usage`` por-AssistantMessage del turno;
          3. el ``model_usage`` del ResultMessage (mapa por modelo; el CLI lo
             emite en camelCase ``inputTokens``/``outputTokens``).
        """
        text_parts: list[str] = []
        usage = Usage()
        assistant_in = assistant_out = 0
        result_in = result_out = 0
        model_usage_in = model_usage_out = 0
        for msg in messages:
            content = getattr(msg, "content", None)
            if isinstance(content, list):
                for block in content:
                    text = getattr(block, "text", None)
                    if text:
                        text_parts.append(text)
            is_result = getattr(msg, "total_cost_usd", None) is not None or (
                getattr(msg, "model_usage", None) is not None
            )
            u = getattr(msg, "usage", None)
            if u:
                if is_result:
                    result_in = _usage_get(u, "input_tokens")
                    result_out = _usage_get(u, "output_tokens")
                else:
                    assistant_in += _usage_get(u, "input_tokens")
                    assistant_out += _usage_get(u, "output_tokens")
                usage.cache_read_tokens = _usage_get(u, "cache_read_input_tokens")
                usage.cache_write_tokens = _usage_get(u, "cache_creation_input_tokens")
            mu_in, mu_out = _model_usage_tokens(getattr(msg, "model_usage", None))
            model_usage_in += mu_in
            model_usage_out += mu_out
            cost = getattr(msg, "total_cost_usd", None)
            if cost is not None:
                usage.cost_usd = float(cost)
        if result_in or result_out:
            usage.input_tokens, usage.output_tokens = result_in, result_out
        elif assistant_in or assistant_out:
            usage.input_tokens, usage.output_tokens = assistant_in, assistant_out
        else:
            usage.input_tokens, usage.output_tokens = model_usage_in, model_usage_out
        return text_parts, usage

    # ------------------------------------------------------------------
    # LLMProvider Protocol
    # ------------------------------------------------------------------
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,  # noqa: ARG002 — SDK does not expose this
        temperature: float = 0.7,  # noqa: ARG002 — same
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> CompletionResponse:
        system, prompt = self._flatten(messages)
        max_turns = int(kwargs.pop("max_turns", 8))
        effort = kwargs.pop("effort", None)
        allowed_tools = kwargs.pop("allowed_tools", None)
        # ADR 0021 / Protocol (base.py): complete() DEBE honrar `tools` cuando el
        # backend soporta tool-calling. El SDK de Claude no es chat-completions:
        # advertimos las tools como un MCP server in-process (el modelo ve sus
        # esquemas) y CAPTURAMOS la tool-call vía `can_use_tool` (deny+interrupt)
        # en vez de que la ejecute el SDK, devolviéndola en `tool_calls` para que
        # la ejecute el HOST — idéntico contrato a los providers OpenAI-compatibles.
        # Así el grafo del asistente (y un provider futuro, p.ej. OpenAI) se
        # comportan igual sea cual sea el backend LLM.
        if tools:
            return await self._complete_with_tools(
                prompt=prompt,
                system=system,
                model=model,
                tools=tools,
                max_turns=max_turns,
                effort=effort,
                # Las web tools NATIVAS del SDK (WebSearch/WebFetch, ADR 0076) viajan
                # como `allowed_tools` y deben seguir activas AUN cuando hay host
                # tools en juego — el córtex (F1) tiene ambas. Aditivo: el asistente
                # no pasa `allowed_tools`, así que None mantiene el comportamiento.
                allowed_tools=allowed_tools,
            )
        options = self._build_options(
            model=model,
            system=system,
            allowed_tools=allowed_tools,
            # `max_turns=1` agota el loop interno del Claude Code CLI (incluso una
            # respuesta simple cuenta como >1 turno) → "Reached maximum number of
            # turns (1)". 8 deja responder + algún paso interno; overridable.
            max_turns=max_turns,
            effort=effort,
            # F31/P1.6: sin host tools, NO permitir ejecución nativa del SDK fuera
            # del lazo mediado por el host (salvo lo que el caller permita).
            disallow_native_tools=True,
        )
        query_fn = self._query()
        collected: list[Any] = []
        try:
            async for msg in query_fn(prompt=prompt, options=options):
                collected.append(msg)
        except Exception as exc:  # — surface the CLI's real reason, typed
            raise _run_error(exc, collected) from exc
        text_parts, usage = self._harvest(collected)
        return CompletionResponse(
            content="".join(text_parts),
            model=model or self._default_model,
            provider=self.name,
            usage=usage,
            tool_calls=None,  # no tools requested
            raw=collected,
        )

    async def _complete_with_tools(
        self,
        *,
        prompt: str,
        system: str | None,
        model: str | None,
        tools: list[dict[str, Any]],
        max_turns: int,
        effort: str | None,
        allowed_tools: list[str] | None = None,
    ) -> CompletionResponse:
        """Honor `tools` with the Claude Agent SDK (host-executed tool-calling).

        The SDK drives its own tool loop, so we expose the host tools as an
        in-process MCP server and intercept each call with a ``can_use_tool``
        callback that DENIES execution and interrupts — the host then runs the
        tool, exactly like the OpenAI-compatible providers. The model's
        ``tool_use`` blocks are harvested into ``CompletionResponse.tool_calls``.

        ``allowed_tools`` carries the SDK's NATIVE tools (WebSearch/WebFetch, ADR
        0076) that must stay enabled even when host tools are advertised — the
        córtex (F1) uses both. They are auto-approved (in ``allowed_tools``) so the
        ``can_use_tool`` interceptor only fires for the host (MCP) tools.
        """
        query_fn = self._query()
        specs = _unwrap_tool_schemas(tools)
        if self._query_fn is not None:
            # Test mode: the injected fake takes whatever we pass; no SDK import.
            prompt_arg: Any = prompt
            options: Any = None
        else:
            options = self._build_tool_options(
                system=system,
                model=model,
                specs=specs,
                max_turns=max_turns,
                effort=effort,
                allowed_tools=allowed_tools,
            )
            prompt_arg = _single_user_prompt_stream(prompt)
        collected: list[Any] = []
        try:
            async for msg in query_fn(prompt=prompt_arg, options=options):
                collected.append(msg)
        except Exception as exc:
            # `can_use_tool(interrupt=True)` puede cerrar el stream con una señal;
            # si ya cosechamos la tool-call, ESO es el resultado. Si no, es error.
            tool_calls = _harvest_tool_calls(collected)
            if not tool_calls:
                raise _run_error(exc, collected) from exc
            _, usage = self._harvest(collected)
            # Tool-call turn → DROP any partial text: the SDK's interrupt notice
            # / the model's preamble is NOT the user-facing answer (the host runs
            # the tool and the answer comes on a later, clean turn).
            return CompletionResponse(
                content="",
                model=model or self._default_model,
                provider=self.name,
                usage=usage,
                tool_calls=tool_calls,
                raw=collected,
            )
        text_parts, usage = self._harvest(collected)
        tool_calls = _harvest_tool_calls(collected)
        # Same rule on the clean path: when the model asked for tools, the partial
        # text from this turn is not the answer; only return text when it DIDN'T.
        content = "" if tool_calls else "".join(text_parts)
        return CompletionResponse(
            content=content,
            model=model or self._default_model,
            provider=self.name,
            usage=usage,
            tool_calls=tool_calls or None,
            raw=collected,
        )

    def _build_tool_options(
        self,
        *,
        system: str | None,
        model: str | None,
        specs: list[dict[str, Any]],
        max_turns: int,
        effort: str | None,
        allowed_tools: list[str] | None = None,
    ) -> Any:
        """Build ``ClaudeAgentOptions`` advertising `specs` as an in-process MCP
        server, with a ``can_use_tool`` that denies+interrupts so the HOST runs
        the tool (the SDK only surfaces the call). Production path only — in tests
        the injected ``query_fn`` short-circuits this (no SDK import).

        ``allowed_tools`` lists the SDK's NATIVE tools (WebSearch/WebFetch, ADR
        0076) to keep auto-approved alongside the intercepted host tools."""
        try:
            from claude_agent_sdk import (  # lazy — optional extra
                ClaudeAgentOptions,
                PermissionResultDeny,
                create_sdk_mcp_server,
                tool,
            )
        except ImportError as exc:
            raise ImportError(
                "claude-agent-sdk is not installed. Run `pip install 'shared-llm[claude]'`."
            ) from exc

        async def _stub(args: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001 — SDK tool sig
            # Never executed: can_use_tool denies before the SDK would run it.
            return {"content": [{"type": "text", "text": ""}]}

        sdk_tools = [
            tool(
                spec["name"],
                spec.get("description") or spec["name"],
                _json_schema_to_tool_schema(spec.get("parameters")),
            )(_stub)
            for spec in specs
        ]
        server = create_sdk_mcp_server(name=_HOST_TOOLS_SERVER, version="1.0.0", tools=sdk_tools)

        async def _capture_and_deny(
            tool_name: str,  # noqa: ARG001 — SDK can_use_tool sig
            tool_input: dict[str, Any],  # noqa: ARG001 — SDK can_use_tool sig
            context: Any,  # noqa: ARG001 — SDK can_use_tool sig
        ) -> Any:
            # The call is harvested from the message stream; deny+interrupt keeps
            # the SDK from executing it or looping — the host runs it instead.
            return PermissionResultDeny(
                message="Tool ejecutada por el host (host-executed tool-calling).",
                interrupt=True,
            )

        extra: dict[str, Any] = {}
        if effort:
            extra["effort"] = effort
        # NB: las tools HOST (MCP) NO van en `allowed_tools` a propósito → el SDK
        # evalúa el permiso a "ask" → dispara `can_use_tool` → interceptamos. Las
        # web tools NATIVAS del SDK (WebSearch/WebFetch, ADR 0076) SÍ van en
        # `allowed_tools` para quedar auto-aprobadas (las gestiona Anthropic, no el
        # host) — así coexisten con las host tools sin disparar el interceptor.
        if allowed_tools:
            extra["allowed_tools"] = list(allowed_tools)
        # Disable the SDK's native tools so the model can only use the HOST (MCP)
        # tools we advertised — otherwise it calls Bash/Read/Write/… which the host
        # rejects. Whatever the caller auto-approved via `allowed_tools` (córtex
        # WebSearch/WebFetch, ADR 0076) is kept enabled (subtracted here).
        _allowed = set(allowed_tools or ())
        disabled = [name for name in _SDK_NATIVE_TOOLS if name not in _allowed]
        if disabled:
            extra["disallowed_tools"] = disabled
        return ClaudeAgentOptions(
            model=model or self._default_model,
            system_prompt=system if system is not None else self._default_system_prompt,
            mcp_servers={_HOST_TOOLS_SERVER: server},
            can_use_tool=_capture_and_deny,
            max_turns=max_turns,
            **extra,
        )

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,  # noqa: ARG002
        temperature: float = 0.7,  # noqa: ARG002
        tools: list[dict[str, Any]] | None = None,  # noqa: ARG002
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        system, prompt = self._flatten(messages)
        options = self._build_options(
            model=model,
            system=system,
            allowed_tools=kwargs.pop("allowed_tools", None),
            # `max_turns=1` agota el loop interno del Claude Code CLI (incluso una
            # respuesta simple cuenta como >1 turno) → "Reached maximum number of
            # turns (1)". 8 deja responder + algún paso interno; overridable.
            max_turns=int(kwargs.pop("max_turns", 8)),
            effort=kwargs.pop("effort", None),
            # F31/P1.6: same chat-shaped guard as complete() — no native SDK tool
            # execution outside the host-mediated loop.
            disallow_native_tools=True,
        )
        query_fn = self._query()
        last_usage: Usage | None = None
        collected: list[Any] = []
        try:
            async for msg in query_fn(prompt=prompt, options=options):
                collected.append(msg)
                content = getattr(msg, "content", None)
                if isinstance(content, list):
                    for block in content:
                        text = getattr(block, "text", None)
                        if text:
                            yield StreamChunk(delta=text)
                u = getattr(msg, "usage", None)
                if u or getattr(msg, "total_cost_usd", None) is not None:
                    last_usage = Usage(
                        input_tokens=_usage_get(u, "input_tokens"),
                        output_tokens=_usage_get(u, "output_tokens"),
                        cache_read_tokens=_usage_get(u, "cache_read_input_tokens"),
                        cache_write_tokens=_usage_get(u, "cache_creation_input_tokens"),
                        cost_usd=float(getattr(msg, "total_cost_usd", 0.0) or 0.0),
                    )
        except Exception as exc:
            raise _run_error(exc, collected) from exc
        yield StreamChunk(delta="", done=True, usage=last_usage)

    # ------------------------------------------------------------------
    # Escape hatch — full agent run, typed AgentRunEvent stream
    # ------------------------------------------------------------------
    async def run_agent(
        self,
        prompt: str,
        *,
        model: str | None = None,
        system_prompt: str | None = None,
        allowed_tools: list[str] | None = None,
        max_turns: int = 10,
        effort: str | None = None,
    ) -> AsyncIterator[AgentRunEvent]:
        """Multi-turn SDK run; yields typed events.

        Use when you want the SDK's full capabilities (tool use, MCP,
        sub-agents) but don't want the rest of the codebase to import
        `claude-agent-sdk` types. ``effort`` enables extended thinking
        (ADR 0070) — like ``complete``/``stream``, it must reach
        ``_build_options`` or it is silently ignored.
        """
        options = self._build_options(
            model=model,
            system=system_prompt,
            allowed_tools=allowed_tools,
            max_turns=max_turns,
            effort=effort,
        )
        query_fn = self._query()
        async for msg in query_fn(prompt=prompt, options=options):
            yield _to_agent_event(msg)

    async def aclose(self) -> None:
        return None

    # Convenience for tests that want to verify api_key handling.
    @staticmethod
    def assert_api_key_present() -> None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise AuthError("ANTHROPIC_API_KEY is not set")


def _to_agent_event(msg: Any) -> AgentRunEvent:
    """Translate one SDK message into a typed `AgentRunEvent`."""
    # ResultMessage carries total_cost_usd and the final usage.
    cost = getattr(msg, "total_cost_usd", None)
    raw_usage = getattr(msg, "usage", None)
    if cost is not None:
        usage = Usage(
            input_tokens=_usage_get(raw_usage, "input_tokens"),
            output_tokens=_usage_get(raw_usage, "output_tokens"),
            cache_read_tokens=_usage_get(raw_usage, "cache_read_input_tokens"),
            cache_write_tokens=_usage_get(raw_usage, "cache_creation_input_tokens"),
            cost_usd=float(cost),
        )
        return AgentRunEvent(kind="result", usage=usage, raw=msg)

    content = getattr(msg, "content", None)
    if isinstance(content, list):
        for block in content:
            if hasattr(block, "name") and hasattr(block, "input"):
                return AgentRunEvent(
                    kind="tool_use",
                    tool_use={
                        "name": getattr(block, "name", ""),
                        "input": dict(getattr(block, "input", {}) or {}),
                        "id": getattr(block, "id", None),
                    },
                    raw=msg,
                )
            text = getattr(block, "text", None)
            if text:
                return AgentRunEvent(kind="text", text=text, raw=msg)

    return AgentRunEvent(kind="other", raw=msg)


# ----------------------------------------------------------------------
# Host-executed tool-calling helpers (ADR 0021 — Protocol `tools` contract)
# ----------------------------------------------------------------------
def _unwrap_tool_schemas(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalise OpenAI-style tool defs to flat ``{name, description, parameters}``.

    Accepts both the wrapped ``{"type":"function","function":{...}}`` envelope and
    a bare ``{"name","description","parameters"}`` dict; skips entries with no name.
    """
    specs: list[dict[str, Any]] = []
    for t in tools or []:
        fn = t.get("function") if isinstance(t, dict) and "function" in t else t
        if not isinstance(fn, dict):
            continue
        name = fn.get("name")
        if not name:
            continue
        specs.append(
            {
                "name": str(name),
                "description": fn.get("description") or "",
                "parameters": fn.get("parameters") or fn.get("input_schema") or {},
            }
        )
    return specs


def _json_schema_to_tool_schema(parameters: dict[str, Any] | None) -> dict[str, Any]:
    """Map a JSON-Schema ``parameters`` object to the ``@tool`` decorator's simple
    ``{field: python_type}`` form. The stub tool is never executed (the host runs
    the real one), so this only needs to advertise field names/types to the model.
    """
    props = (parameters or {}).get("properties") or {}
    typemap: dict[str, type] = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    schema = {
        name: typemap.get(str((spec or {}).get("type")), str)
        for name, spec in props.items()
        if isinstance(name, str)
    }
    return schema or {"input": str}


def _strip_mcp_prefix(name: str) -> str:
    """``mcp__host_tools__remember_about_me`` → ``remember_about_me`` (the bare
    name the host registered). Non-MCP names pass through unchanged."""
    if name.startswith("mcp__"):
        parts = name.split("__")
        if len(parts) >= 3:
            return "__".join(parts[2:])
    return name


def _harvest_tool_calls(messages: list[Any]) -> list[ToolCall]:
    """Collect the model's tool requests from SDK assistant messages.

    A ``tool_use`` block is duck-typed exactly like ``_to_agent_event`` detects it
    (has ``.name`` and ``.input``). The SDK's MCP namespacing is stripped back to
    the bare tool name the host expects."""
    calls: list[ToolCall] = []
    for msg in messages:
        content = getattr(msg, "content", None)
        if not isinstance(content, list):
            continue
        for block in content:
            if hasattr(block, "name") and hasattr(block, "input"):
                calls.append(
                    ToolCall(
                        id=str(getattr(block, "id", "") or ""),
                        name=_strip_mcp_prefix(str(getattr(block, "name", ""))),
                        arguments=dict(getattr(block, "input", {}) or {}),
                    )
                )
    return calls


async def _single_user_prompt_stream(prompt: str) -> AsyncIterator[dict[str, Any]]:
    """Streaming-mode input: ``can_use_tool`` requires an AsyncIterable prompt, not
    a string. Yield the single user turn in the SDK's streaming message shape."""
    yield {"type": "user", "message": {"role": "user", "content": prompt}}


__all__ = ["AgentRunEvent", "ClaudeAgentProvider"]
