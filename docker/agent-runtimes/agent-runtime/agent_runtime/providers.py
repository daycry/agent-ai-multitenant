"""Real ModelClient implementations — adapters over `shared-llm` (ADR 0021).

The agent loop talks to an LLM only through the sync `ModelClient`
protocol (ADR 0013): `decide()` returns one decision, `review()` one
verdict. This module wraps the async `shared_llm.LLMProvider` so the
sync loop can keep its shape.

Four adapters, one per provider in the closed catalog of ADR 0021:

  * `AzureFoundryModelClient` — Azure AI Foundry behind APIM (primary
                                enterprise gateway path).
  * `CopilotModelClient`      — GitHub Copilot via OAuth Device Flow +
                                minted JWT.
  * `ClaudeSDKModelClient`    — Claude Agent SDK, single turn per
                                `decide()` (ADR 0018).
  * `OllamaModelClient`       — Ollama, local or cloud.

`LiteLLMModelClient` is GONE — see ADR 0021 for the rationale.

The HTTP transport (`httpx.AsyncClient`) and the SDK `query` are still
injectable, so the tests exercise every adapter with no network and
no real credentials. Each adapter wraps a `shared_llm.LLMProvider`
instance, calls its async `complete()` via `asyncio.run`, and parses
the typed `CompletionResponse` into the loop's `ModelResponse` /
`ReviewResponse`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx
from shared_llm import (
    AzureFoundryAPIMProvider,
    ClaudeAgentProvider,
    CompletionResponse,
    CopilotProvider,
    LLMProvider,
    Message,
    OllamaProvider,
)

from agent_runtime.model import (
    DecisionKind,
    ModelClient,
    ModelDecision,
    ModelResponse,
    ReviewResponse,
)

# ---------------------------------------------------------------------------
# Prompts + message construction (same shape as before the refactor)
# ---------------------------------------------------------------------------
_DECIDE_SYSTEM = (
    "You are an autonomous agent executing one task inside a loop. On each "
    "turn either call exactly one tool to make progress, or — when the task "
    "is complete — reply with the final result as plain text and no tool call."
)
_REVIEW_SYSTEM = (
    "You are a reviewer. Decide whether the candidate output satisfies the "
    "task. Reply with a JSON object and nothing else: "
    '{"passed": <true|false>, "feedback": "<short reason>"}.'
)

# How many context fragments to feed the model — the loop's context list
# grows unbounded; the tail is the relevant part.
_CONTEXT_WINDOW = 8


def _decide_messages(state: dict[str, Any]) -> list[Message]:
    """Turn the agent-loop state into the chat messages for a decision."""
    task = state.get("task") or {}
    lines = [f"Task: {task.get('title', '')}".strip()]
    if task.get("description"):
        lines.append(str(task["description"]))
    context = state.get("context") or []
    if context:
        lines.append("Context so far:")
        lines += [f"- {json.dumps(item, default=str)}" for item in context[-_CONTEXT_WINDOW:]]
    observation = state.get("last_observation")
    if observation:
        lines.append(f"Last observation: {json.dumps(observation, default=str)}")
    return [
        Message(role="system", content=_DECIDE_SYSTEM),
        Message(role="user", content="\n".join(line for line in lines if line)),
    ]


def _review_messages(state: dict[str, Any]) -> list[Message]:
    """Turn the agent-loop state into the chat messages for a review."""
    task = state.get("task") or {}
    body = (
        f"Task: {task.get('title', '')}\n{task.get('description', '')}\n\n"
        f"Candidate output:\n{state.get('output') or ''}"
    )
    return [
        Message(role="system", content=_REVIEW_SYSTEM),
        Message(role="user", content=body),
    ]


# ---------------------------------------------------------------------------
# Response parsing — translate shared_llm types into agent-runtime types
# ---------------------------------------------------------------------------
def _extract_json(text: str) -> Any:
    """Best-effort: parse `text` as JSON, or the first `{...}` span in it."""
    with contextlib.suppress(json.JSONDecodeError):
        return json.loads(text)
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        with contextlib.suppress(json.JSONDecodeError):
            return json.loads(text[start : end + 1])
    return None


def _parse_verdict(content: str) -> tuple[bool, str]:
    """Turn a review reply into a (passed, feedback) pair.

    Prefers the documented JSON object; falls back to keyword sniffing
    so a model that ignores the format instruction still yields a verdict.
    """
    obj = _extract_json(content.strip())
    if isinstance(obj, dict) and "passed" in obj:
        return bool(obj["passed"]), str(obj.get("feedback", ""))
    lowered = content.lower()
    passed = "fail" not in lowered and ("pass" in lowered or "approve" in lowered)
    return passed, content.strip()


def _decision_from(resp: CompletionResponse, *, model: str) -> ModelResponse:
    """Turn one `CompletionResponse` into a `ModelResponse`.

    If the model emitted a tool call, this is an ACT; otherwise a
    FINISH whose output is the text content.
    """
    if resp.tool_calls:
        call = resp.tool_calls[0]
        decision = ModelDecision(
            kind=DecisionKind.ACT,
            tool=call.name,
            tool_args=dict(call.arguments),
            rationale=resp.content or "",
        )
    else:
        decision = ModelDecision(kind=DecisionKind.FINISH, output=resp.content)
    return ModelResponse(
        decision=decision,
        model=resp.model or model,
        tokens_in=resp.usage.input_tokens,
        tokens_out=resp.usage.output_tokens,
        cost_usd=resp.usage.cost_usd,
    )


def _review_from(resp: CompletionResponse, *, model: str) -> ReviewResponse:
    passed, feedback = _parse_verdict(resp.content or "")
    return ReviewResponse(
        passed=passed,
        feedback=feedback,
        model=resp.model or model,
        tokens_in=resp.usage.input_tokens,
        tokens_out=resp.usage.output_tokens,
        cost_usd=resp.usage.cost_usd,
    )


def _run(coro: Any) -> Any:
    """Run an async call from a sync context.

    The agent loop is sync (LangGraph state machine, sync nodes). The
    `LLMProvider` Protocol is async. We bridge with `asyncio.run` per
    call — the providers are stateless across calls except for the
    Copilot JWT cache which lives on the provider instance.
    """
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Shared adapter — a `ModelClient` over any `LLMProvider`
# ---------------------------------------------------------------------------
class _ProviderModelClient:
    """Adapter from `LLMProvider` (async) to `ModelClient` (sync)."""

    def __init__(
        self,
        *,
        provider: LLMProvider,
        model: str,
        tools: list[dict[str, Any]] | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self._tools = tools

    def decide(self, state: dict[str, Any]) -> ModelResponse:
        resp = _run(
            self.provider.complete(
                _decide_messages(state),
                model=self.model,
                tools=self._tools,
            )
        )
        return _decision_from(resp, model=self.model)

    def review(self, state: dict[str, Any]) -> ReviewResponse:
        resp = _run(
            self.provider.complete(
                _review_messages(state),
                model=self.model,
            )
        )
        return _review_from(resp, model=self.model)


# ---------------------------------------------------------------------------
# Per-provider adapters — thin constructors that build the right provider
# ---------------------------------------------------------------------------
class AzureFoundryModelClient(_ProviderModelClient):
    """Azure AI Foundry behind APIM — the enterprise gateway path."""

    def __init__(
        self,
        *,
        model: str,
        apim_base_url: str,
        deployment: str,
        subscription_key: str | None = None,
        bearer_token: str | None = None,
        api_version: str = "2024-10-21",
        tools: list[dict[str, Any]] | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            provider=AzureFoundryAPIMProvider(
                apim_base_url=apim_base_url,
                deployment=deployment,
                subscription_key=subscription_key,
                bearer_token=bearer_token,
                api_version=api_version,
                http_client=http_client,
            ),
            model=model,
            tools=tools,
        )


class CopilotModelClient(_ProviderModelClient):
    """GitHub Copilot. `github_token` is the long-lived OAuth token
    (obtained out-of-band by the admin-panel's device-flow screen)."""

    def __init__(
        self,
        *,
        model: str,
        github_token: str,
        tools: list[dict[str, Any]] | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            provider=CopilotProvider(github_token=github_token, http_client=http_client),
            model=model,
            tools=tools,
        )


class OllamaModelClient(_ProviderModelClient):
    """Ollama (local or cloud). Pass the right base_url + api_key."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "http://localhost:11434/v1",
        api_key: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            provider=OllamaProvider(
                base_url=base_url,
                api_key=api_key,
                http_client=http_client,
                default_model=model,
            ),
            model=model,
            tools=tools,
        )


# ---------------------------------------------------------------------------
# Claude Agent SDK — keeps its own adapter shape (no tool_calls path)
# ---------------------------------------------------------------------------
SdkQuery = Callable[..., AsyncIterator[Any]]


class ClaudeSDKModelClient:
    """The Claude Agent SDK as a single-decision `ModelClient`.

    Wraps `shared_llm.providers.ClaudeAgentProvider`. The SDK does not
    expose `/chat/completions`-style tool_calls in this path — when the
    loop needs tool use it should run inside the SDK's own agent loop
    via `provider.run_agent()`, which is a different code path.

    For the per-decision adapter, every turn is FINISH (text). The
    LangGraph loop drives multi-turn behaviour itself (ADR 0018).
    """

    def __init__(
        self,
        *,
        model: str,
        query_fn: SdkQuery | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_turns: int = 1,
    ) -> None:
        self.model = model
        self._tools = tools
        self._max_turns = max_turns
        self.provider = ClaudeAgentProvider(
            default_model=model,
            query_fn=query_fn,
        )

    def decide(self, state: dict[str, Any]) -> ModelResponse:
        resp = _run(self.provider.complete(_decide_messages(state), model=self.model))
        # SDK never emits OpenAI-style tool_calls through complete();
        # this is always a FINISH (the SDK's text output).
        decision = ModelDecision(kind=DecisionKind.FINISH, output=resp.content)
        return ModelResponse(
            decision=decision,
            model=resp.model or self.model,
            tokens_in=resp.usage.input_tokens,
            tokens_out=resp.usage.output_tokens,
            cost_usd=resp.usage.cost_usd,
        )

    def review(self, state: dict[str, Any]) -> ReviewResponse:
        resp = _run(self.provider.complete(_review_messages(state), model=self.model))
        return _review_from(resp, model=self.model)


# ---------------------------------------------------------------------------
# Provider config resolution — DB row (llm_providers) + Vault > env/installer
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ResolvedProviderConfig:
    """A provider's runtime config resolved from an active `llm_providers`
    row plus its Vault-stored credential (Plan 11.2, ADR 0028).

    `base_url` is the row's endpoint (the APIM gateway / the Ollama URL;
    `None` for the subscription-based Claude SDK path). `secret` is the
    `{field: value}` dict read from Vault for the provider — the well-known
    field names the admin layer writes (`oauth_token` / `api_key` /
    `bearer_token`). NEITHER is ever logged; this object only ever lives in
    memory long enough to build the provider client.
    """

    base_url: str | None = None
    secret: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class ProviderConfigResolver(Protocol):
    """Resolve a provider `kind` to its DB+Vault config, or `None`.

    The single seam the factory uses to let an active `llm_providers` row
    win over the env/installer spec (precedence: **DB row > env**). A
    resolver returns a :class:`ResolvedProviderConfig` when an active row
    exists for the requested kind, or `None` to keep the current
    env/installer behaviour. The agent-runtime container has no DB/Vault
    access (CLAUDE.md principle 2) so it never passes a resolver — `None`
    is the default and every existing call site is unchanged. The server
    side (api-server / worker) injects a DB+Vault-backed resolver to build
    the spec from `llm_providers`.
    """

    def __call__(self, kind: str) -> ResolvedProviderConfig | None: ...


# How a resolved config overlays onto a spec, per kind. Each kind reads
# different spec keys, so the overlay maps the DB `base_url` + the Vault
# secret field onto the spec key that kind's client constructor consumes.
# A resolved value WINS over whatever the env/installer put in the spec.
def _overlay_resolved(
    spec: dict[str, Any], kind: str, resolved: ResolvedProviderConfig
) -> dict[str, Any]:
    """Return a copy of `spec` with the resolved DB+Vault config applied.

    Precedence is **DB row > env**: a non-empty resolved field overwrites
    the spec's env/installer-derived value; an absent resolved field leaves
    the spec untouched (so e.g. an Ollama row with no bearer keeps any
    env api_key). The input `spec` is never mutated.
    """
    merged = dict(spec)
    base_url = resolved.base_url
    secret = resolved.secret
    if kind == "azure_foundry":
        if base_url:
            merged["apim_base_url"] = base_url
        if secret.get("api_key"):
            merged["subscription_key"] = secret["api_key"]
    elif kind == "copilot":
        if secret.get("oauth_token"):
            merged["github_token"] = secret["oauth_token"]
    elif kind in ("claude_sdk", "claude"):
        # Claude SDK uses ambient subscription auth — the row carries no
        # spec-level override beyond the (rare) base_url; the OAuth token in
        # Vault is consumed out-of-band by the SDK environment. Nothing to
        # overlay onto the spec here.
        pass
    elif kind == "ollama":
        if base_url:
            merged["base_url"] = base_url
        if secret.get("bearer_token"):
            merged["api_key"] = secret["bearer_token"]
    return merged


# ---------------------------------------------------------------------------
# Factory — model_from_spec delegates here for non-scripted kinds
# ---------------------------------------------------------------------------
def build_provider_client(
    spec: dict[str, Any],
    *,
    resolver: ProviderConfigResolver | None = None,
) -> ModelClient:
    """Build a real `ModelClient` from a JSON model spec.

    Kinds (ADR 0021 closed catalog):
      * `azure_foundry` — Azure AI Foundry vía APIM (primary gateway).
      * `copilot`       — GitHub Copilot via OAuth + JWT.
      * `claude_sdk`    — Claude Agent SDK (alias `claude`).
      * `ollama`        — Ollama local or cloud.

    Provider config precedence (Plan 11.2 / ADR 0028): **DB row > env**.
    When a `resolver` is supplied AND it returns a config for the spec's
    `kind` (i.e. an active `llm_providers` row exists), that row's
    `base_url` + its Vault-stored credential overlay the spec before the
    client is built. With no resolver (the default — the runtime container
    has no DB/Vault access) or when the resolver returns `None` (no active
    row), the spec's env/installer-derived fields are used unchanged. No
    call site or signature breaks: `resolver` is optional and defaults to
    the historical behaviour.

    The `scripted` kind is handled by `agent_runtime.model.model_from_spec`.
    The historical `litellm` kind is rejected (see ADR 0021).
    """
    kind = spec.get("kind")
    if resolver is not None and isinstance(kind, str):
        resolved = resolver(kind)
        if resolved is not None:
            spec = _overlay_resolved(spec, kind, resolved)
    model = spec.get("model", "")
    tools = spec.get("tools")
    if kind == "azure_foundry":
        return AzureFoundryModelClient(
            model=model,
            apim_base_url=spec["apim_base_url"],
            deployment=spec.get("deployment", model),
            subscription_key=spec.get("subscription_key"),
            bearer_token=spec.get("bearer_token"),
            api_version=spec.get("api_version", "2024-10-21"),
            tools=tools,
        )
    if kind == "copilot":
        return CopilotModelClient(
            model=model,
            github_token=spec["github_token"],
            tools=tools,
        )
    if kind in ("claude_sdk", "claude"):
        return ClaudeSDKModelClient(
            model=model,
            tools=tools,
            max_turns=int(spec.get("max_turns", 1)),
        )
    if kind == "ollama":
        return OllamaModelClient(
            model=model,
            base_url=spec.get("base_url", "http://localhost:11434/v1"),
            api_key=spec.get("api_key"),
            tools=tools,
        )
    if kind == "litellm":
        raise ValueError(
            "kind='litellm' is no longer supported (ADR 0021). "
            "Use one of: azure_foundry, copilot, claude_sdk, ollama."
        )
    raise ValueError(f"unknown provider kind: {kind!r}")


__all__ = [
    "AzureFoundryModelClient",
    "ClaudeSDKModelClient",
    "CopilotModelClient",
    "OllamaModelClient",
    "ProviderConfigResolver",
    "ResolvedProviderConfig",
    "build_provider_client",
]
