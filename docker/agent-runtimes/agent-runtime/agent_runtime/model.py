"""The model client the agent loop talks to (task_02_10 / task_02_12).

The loop never imports an LLM SDK directly — it depends on the
`ModelClient` protocol. Two calls:

  decide(state)  → ModelResponse  — what to do next (act / finish).
  review(state)  → ReviewResponse — does the final output pass?

`ScriptedModelClient` replays a fixed sequence — deterministic, offline,
and the way the loop / capture / safeguard tests drive the graph. Real
providers (Azure Foundry APIM, GitHub Copilot, Claude Agent SDK, Ollama)
plug in behind the same protocol via `agent_runtime.providers`, which
adapts the async `shared_llm.LLMProvider` layer (ADR 0021).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class DecisionKind(enum.StrEnum):
    ACT = "act"  # call a tool
    FINISH = "finish"  # produce the final output


@dataclass(frozen=True)
class ModelDecision:
    """What the model decided the agent should do next."""

    kind: DecisionKind
    tool: str | None = None
    tool_args: dict[str, Any] = field(default_factory=dict)
    output: str | None = None
    rationale: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": str(self.kind),
            "tool": self.tool,
            "tool_args": dict(self.tool_args),
            "output": self.output,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class ModelResponse:
    """A `decide` result, with usage accounting for the capture layer."""

    decision: ModelDecision
    model: str = "scripted"
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


@dataclass(frozen=True)
class ReviewResponse:
    """A `review` result — did the output pass self-review?

    The self-review is an AUTHORITATIVE gate (ADR 0087). The verdict is
    three-state: ``passed`` is True (certified) or False, and when the verdict
    could NOT be determined reliably — no structured verdict and ambiguous prose,
    or malformed tool args — ``inconclusive`` is True (``passed`` stays False so
    it never auto-passes). The loop ESCALATES an inconclusive verdict (and an
    exhausted retry budget) to a human instead of passing or aborting.
    """

    passed: bool
    feedback: str = ""
    model: str = "scripted"
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    # True when the verdict is untrustworthy → escalate to human (ADR 0087).
    inconclusive: bool = False


@runtime_checkable
class ModelClient(Protocol):
    """The seam between the agent loop and an LLM provider."""

    def decide(self, state: dict[str, Any]) -> ModelResponse: ...

    def review(self, state: dict[str, Any]) -> ReviewResponse: ...


@dataclass
class ScriptedModelClient:
    """Deterministic model client — replays a fixed script.

    When `decisions` is exhausted the last entry repeats, so a loop with
    no FINISH runs until a safeguard trips it (used by the safeguard and
    loop-detection tests). `reviews` defaults to a single pass.
    """

    decisions: list[ModelResponse]
    reviews: list[ReviewResponse] = field(default_factory=lambda: [ReviewResponse(passed=True)])
    _decide_cursor: int = 0
    _review_cursor: int = 0

    def decide(self, state: dict[str, Any]) -> ModelResponse:  # noqa: ARG002
        if not self.decisions:
            raise ValueError("ScriptedModelClient needs at least one decision")
        index = min(self._decide_cursor, len(self.decisions) - 1)
        self._decide_cursor += 1
        return self.decisions[index]

    def review(self, state: dict[str, Any]) -> ReviewResponse:  # noqa: ARG002
        if not self.reviews:
            return ReviewResponse(passed=True)
        index = min(self._review_cursor, len(self.reviews) - 1)
        self._review_cursor += 1
        return self.reviews[index]


def _decision_response(raw: dict[str, Any]) -> ModelResponse:
    decision = ModelDecision(
        kind=DecisionKind(raw["kind"]),
        tool=raw.get("tool"),
        tool_args=dict(raw.get("tool_args", {})),
        output=raw.get("output"),
        rationale=raw.get("rationale", ""),
    )
    return ModelResponse(
        decision=decision,
        model=raw.get("model", "scripted"),
        tokens_in=int(raw.get("tokens_in", 0)),
        tokens_out=int(raw.get("tokens_out", 0)),
        cost_usd=float(raw.get("cost_usd", 0.0)),
    )


def _review_response(raw: dict[str, Any]) -> ReviewResponse:
    return ReviewResponse(
        passed=bool(raw.get("passed", True)),
        feedback=raw.get("feedback", ""),
        model=raw.get("model", "scripted"),
        tokens_in=int(raw.get("tokens_in", 0)),
        tokens_out=int(raw.get("tokens_out", 0)),
        cost_usd=float(raw.get("cost_usd", 0.0)),
    )


def model_from_spec(spec: dict[str, Any], *, resolver: Any | None = None) -> ModelClient:
    """Build a ModelClient from a JSON spec — the agent-runtime entrypoint
    uses this to deserialise the model for a containerised run.

    `kind: "scripted"` builds the deterministic test client here; the
    real provider kinds (`azure_foundry`, `copilot`, `claude_sdk`,
    `ollama` — ADR 0021) are built by `agent_runtime.providers` —
    imported lazily to keep the loop independent of `httpx` / the
    Claude SDK when a scripted run is used.

    `resolver` is the optional `ProviderConfigResolver` seam (Plan 11.2 /
    ADR 0028): when supplied, an active `llm_providers` row + its Vault
    credential win over the spec's env/installer fields (precedence: **DB
    row > env**). It defaults to `None`, so the runtime container (no
    DB/Vault access) and every existing caller keep the historical
    behaviour unchanged.
    """
    # `provider` is the agents' model_config key (the ADR 0021/0055 catalog
    # kind); the worker resolves it to `kind` + endpoint/credential BEFORE the
    # container (ADR 0057 F1). If an unresolved spec slips through, honour the
    # real-provider INTENT — fail loudly downstream on a missing endpoint or
    # credential — instead of silently degrading to the scripted client (the
    # critical bug behind ADR 0057). A bare `{}` spec stays scripted.
    kind = spec.get("kind") or spec.get("provider") or "scripted"
    if kind == "scripted":
        return ScriptedModelClient(
            decisions=[_decision_response(d) for d in spec.get("decisions", [])],
            reviews=[_review_response(r) for r in spec.get("reviews", [])],
        )
    from agent_runtime.providers import build_provider_client

    return build_provider_client(spec, resolver=resolver)
