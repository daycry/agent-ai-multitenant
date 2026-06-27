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
from shared_llm.reasoning import reasoning_call_kwargs

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
    "You are an autonomous agent executing ONE task to completion inside a loop, "
    "working in the current directory (a git worktree). On each turn, either call "
    "exactly ONE tool to make concrete progress, or — once the task is satisfied — "
    "reply with a short final summary as plain text and NO tool call.\n"
    "Let the TASK drive what you do: an implementation task means writing/editing "
    "files (write_file); an analysis or review task means reading what you need and "
    "returning a written conclusion; a testing task means running the tests. The "
    "task's acceptance criteria, when given, define what 'done' means — work toward "
    "them and stop once they are met. Use the research tools (memory_recall, "
    "rag_search, list_files, read_file) only to gather what you genuinely need, "
    "then act; never repeat a search or re-read a file you have already seen, and "
    "ignore files unrelated to the task."
)
_REVIEW_SYSTEM = (
    "You are a reviewer. Decide whether the candidate output satisfies the task. "
    "Call the `submit_verdict` tool with `passed` (true/false) and a short "
    "`feedback`. Do not reply with prose."
)

# ADR 0086: the verdict travels as a TOOL CALL, not formatted text — the contract
# every provider handles well (HTTP: tool_choice; claude_sdk: the host-tool path it
# already uses reliably). `_review_from` reads this call; prose is the fallback.
_SUBMIT_VERDICT_TOOL: dict[str, Any] = {
    "name": "submit_verdict",
    "description": "Submit the self-review verdict for the candidate output.",
    "parameters": {
        "type": "object",
        "properties": {
            "passed": {
                "type": "boolean",
                "description": "True if the output satisfies the task's acceptance criteria.",
            },
            "feedback": {
                "type": "string",
                "description": "Short reason; if not passed, what is missing or wrong.",
            },
        },
        "required": ["passed"],
        "additionalProperties": False,
    },
}

# How many context fragments to feed the model — the loop's context list
# grows unbounded; the tail is the relevant part.
_CONTEXT_WINDOW = 8


def _system_content(state: dict[str, Any]) -> str:
    """The EFFECTIVE system prompt for this run (Plan 06.18 task_06_18_13).

    Prepends the assigned skills' prompt fragments (``state['system_preamble']``,
    ADR 0050) to the base agent instruction. Absent/empty preamble → the
    historical ``_DECIDE_SYSTEM`` verbatim (backward-compat). The preamble goes
    first so the skill cues frame the agent's behaviour before the loop rules.
    """
    preamble = state.get("system_preamble")
    if preamble and str(preamble).strip():
        return f"{str(preamble).strip()}\n\n{_DECIDE_SYSTEM}"
    return _DECIDE_SYSTEM


def _criterion_text(criterion: Any) -> str:
    """A readable one-liner for one acceptance criterion (dict or string)."""
    if isinstance(criterion, str):
        return criterion
    if isinstance(criterion, dict):
        for key in ("description", "text", "criterion", "name"):
            value = criterion.get(key)
            if value:
                return str(value)
    return json.dumps(criterion, default=str)


def _decide_messages(state: dict[str, Any]) -> list[Message]:
    """Turn the agent-loop state into the chat messages for a decision."""
    task = state.get("task") or {}
    lines = [f"Task: {task.get('title', '')}".strip()]
    if task.get("description"):
        lines.append(str(task["description"]))
    criteria = task.get("acceptance_criteria") or []
    if criteria:
        lines.append("Acceptance criteria (the definition of done — work toward these):")
        lines += [f"- {_criterion_text(c)}" for c in criteria]
    context = state.get("context") or []
    if context:
        lines.append("Context so far:")
        lines += [f"- {json.dumps(item, default=str)}" for item in context[-_CONTEXT_WINDOW:]]
    observation = state.get("last_observation")
    if observation:
        lines.append(f"Last observation: {json.dumps(observation, default=str)}")
    return [
        Message(role="system", content=_system_content(state)),
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


# Explicit rejection signals in a PROSE self-review (fallback path only — the
# structured `submit_verdict` tool is preferred). Deliberately CONSERVATIVE: only
# verdict-context phrases, NOT bare domain words. The previous set ("falla",
# "fallo", "rechaz", "incompleto", "reject"…) caused FALSE rejections on auth/JWT
# reviews ("el filtro RECHAZA tokens", "maneja el FALLO de auth", "no FALLA ante
# expirados") — which is why only the JWT task aborted while specs/migrations
# passed. Prose-sniffing is fragile by nature, so we err toward PASS unless the
# review clearly states a negative verdict; the authoritative gates are the
# test-runtime + human plan-level validation (CLAUDE.md), not this heuristic.
_REVIEW_FAIL_MARKERS = (
    '"passed": false',
    '"passed":false',
    "passed: false",
    "passed=false",
    "no cumple",
    "no se cumplen",
    "no satisface",
    "no supera la",
    "no aprobad",
    "veredicto: no",
    "does not satisfy",
    "doesn't satisfy",
    "not satisfied",
    "fails the review",
    "verdict: fail",
)


def _parse_verdict(content: str) -> tuple[bool, str]:
    """Turn a review reply into a (passed, feedback) pair.

    Prefers the documented JSON object. When the model ignores the format and
    replies in prose — the claude_sdk CLI routinely does — default to PASS unless
    the text carries an EXPLICIT rejection signal. The old logic REQUIRED the word
    "pass"/"approve" to pass, so an approving prose review ("la implementación
    satisface los criterios") was mis-read as a failure and looped the run to
    ``max_review_retries_exceeded`` even though the deliverable was complete.
    """
    obj = _extract_json(content.strip())
    if isinstance(obj, dict) and "passed" in obj:
        return bool(obj["passed"]), str(obj.get("feedback", ""))
    lowered = content.lower()
    is_explicit_fail = any(marker in lowered for marker in _REVIEW_FAIL_MARKERS)
    return (not is_explicit_fail), content.strip()


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


def _verdict_from_tool_calls(resp: CompletionResponse) -> tuple[bool, str] | None:
    """The structured verdict if the model called `submit_verdict` (ADR 0086);
    None if it didn't (then the prose fallback in `_review_from` kicks in)."""
    for call in resp.tool_calls or []:
        if call.name == "submit_verdict":
            args = dict(call.arguments)
            return bool(args.get("passed", True)), str(args.get("feedback", "") or "")
    return None


def _review_from(resp: CompletionResponse, *, model: str) -> ReviewResponse:
    # Prefer the structured tool-call verdict; fall back to prose parsing (the
    # claude_sdk CLI may still answer in text — kept as the safety net, c8b78c2).
    verdict = _verdict_from_tool_calls(resp)
    passed, feedback = verdict if verdict is not None else _parse_verdict(resp.content or "")
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
        extra_call_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self._tools = tools
        # ADR 0070: extra params del proveedor (p.ej. reasoning_effort/think) que
        # se vuelcan al body de /chat/completions vía el **kwargs del provider.
        self._extra_call_kwargs = extra_call_kwargs or {}

    def decide(self, state: dict[str, Any]) -> ModelResponse:
        resp = _run(
            self.provider.complete(
                _decide_messages(state),
                model=self.model,
                tools=self._tools,
                **self._extra_call_kwargs,
            )
        )
        return _decision_from(resp, model=self.model)

    def review(self, state: dict[str, Any]) -> ReviewResponse:
        resp = _run(
            self.provider.complete(
                _review_messages(state),
                model=self.model,
                tools=[_SUBMIT_VERDICT_TOOL],  # ADR 0086: verdict as a tool call
                **self._extra_call_kwargs,
            )
        )
        return _review_from(resp, model=self.model)


# La traducción reasoning_effort → kwarg nativo vive en `shared_llm.reasoning`
# (fuente única, compartida con el asistente personal — ADR 0070).


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
        reasoning_effort: str | None = None,
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
            extra_call_kwargs=reasoning_call_kwargs("azure_foundry", reasoning_effort),
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
        reasoning_effort: str | None = None,
    ) -> None:
        super().__init__(
            provider=CopilotProvider(github_token=github_token, http_client=http_client),
            model=model,
            tools=tools,
            extra_call_kwargs=reasoning_call_kwargs("copilot", reasoning_effort),
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
        reasoning_effort: str | None = None,
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
            extra_call_kwargs=reasoning_call_kwargs("ollama", reasoning_effort),
        )


# ---------------------------------------------------------------------------
# Claude Agent SDK — keeps its own adapter shape (no tool_calls path)
# ---------------------------------------------------------------------------
SdkQuery = Callable[..., AsyncIterator[Any]]


class ClaudeSDKModelClient:
    """The Claude Agent SDK as a single-decision `ModelClient`.

    Wraps `shared_llm.providers.ClaudeAgentProvider`. That provider's
    `complete()` now HONOURS `tools` and surfaces the model's requests as
    `CompletionResponse.tool_calls` (host-executed tool-calling: it advertises
    the schemas as an in-process MCP server and captures the call via
    `can_use_tool`). So claude_sdk reaches ACT exactly like the
    OpenAI-compatible providers — provider-agnostic parity — and the LangGraph
    loop drives the multi-turn tool use (ADR 0018).
    """

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        oauth_token: str | None = None,
        query_fn: SdkQuery | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_turns: int = 1,
        reasoning_effort: str | None = None,
    ) -> None:
        self.model = model
        self._tools = tools
        self._max_turns = max_turns
        # ADR 0070: el SDK de Claude usa `effort` (low/medium/high/xhigh/max).
        # `off`/vacío → None (sin extended thinking forzado).
        self._effort = reasoning_call_kwargs("claude_sdk", reasoning_effort).get("effort")
        # Feed the resolved credential to the SDK: api_key → ANTHROPIC_API_KEY,
        # oauth_token → CLAUDE_CODE_OAUTH_TOKEN (subscription Pro/Max, ADR 0063).
        self.provider = ClaudeAgentProvider(
            api_key=api_key,
            oauth_token=oauth_token,
            default_model=model,
            query_fn=query_fn,
        )

    def decide(self, state: dict[str, Any]) -> ModelResponse:
        resp = _run(
            self.provider.complete(
                _decide_messages(state),
                model=self.model,
                tools=self._tools,
                effort=self._effort,
            )
        )
        # complete() now emits tool_calls when the model asks for a tool, so this
        # ramifies to ACT/FINISH like every other provider (no hardcoded FINISH).
        return _decision_from(resp, model=self.model)

    def review(self, state: dict[str, Any]) -> ReviewResponse:
        resp = _run(
            self.provider.complete(
                _review_messages(state),
                model=self.model,
                tools=[_SUBMIT_VERDICT_TOOL],  # ADR 0086: verdict as a tool call
                effort=self._effort,
            )
        )
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
        # Two auth modes on the same kind (ADR 0063): API key
        # (secret['api_key'] → ANTHROPIC_API_KEY) and Pro/Max subscription
        # (secret['oauth_token'] from `claude setup-token` →
        # CLAUDE_CODE_OAUTH_TOKEN). Carry whichever Vault field is present onto
        # the spec; `build_provider_client` feeds it to the SDK env. Mirror of
        # the worker's `model_resolver._overlay_provider_fields`.
        if secret.get("api_key"):
            merged["api_key"] = secret["api_key"]
        if secret.get("oauth_token"):
            merged["oauth_token"] = secret["oauth_token"]
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
    # `provider` is the agents' model_config key (catalog kind, ADR 0055) —
    # honoured as a fallback so an unresolved dispatch spec targets the real
    # provider (and fails loudly on missing fields) instead of `kind=None`.
    kind = spec.get("kind") or spec.get("provider")
    if resolver is not None and isinstance(kind, str):
        resolved = resolver(kind)
        if resolved is not None:
            spec = _overlay_resolved(spec, kind, resolved)
    model = spec.get("model", "")
    tools = spec.get("tools")
    # ADR 0070: esfuerzo de razonamiento por proveedor (clave de model_config que
    # viaja en el spec). Cada adaptador lo traduce a su parámetro nativo.
    reasoning = spec.get("reasoning_effort")
    if kind == "azure_foundry":
        return AzureFoundryModelClient(
            model=model,
            apim_base_url=spec["apim_base_url"],
            deployment=spec.get("deployment", model),
            subscription_key=spec.get("subscription_key"),
            bearer_token=spec.get("bearer_token"),
            api_version=spec.get("api_version", "2024-10-21"),
            tools=tools,
            reasoning_effort=reasoning,
        )
    if kind == "copilot":
        return CopilotModelClient(
            model=model,
            github_token=spec["github_token"],
            tools=tools,
            reasoning_effort=reasoning,
        )
    if kind in ("claude_sdk", "claude"):
        return ClaudeSDKModelClient(
            model=model,
            api_key=spec.get("api_key"),
            oauth_token=spec.get("oauth_token"),
            tools=tools,
            max_turns=int(spec.get("max_turns", 1)),
            reasoning_effort=reasoning,
        )
    if kind == "ollama":
        return OllamaModelClient(
            model=model,
            base_url=spec.get("base_url", "http://localhost:11434/v1"),
            api_key=spec.get("api_key"),
            tools=tools,
            reasoning_effort=reasoning,
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
