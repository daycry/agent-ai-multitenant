"""Remaining built-in guardrails bundle (Plan 11 task_11_09).

Exercises the seven guardrail types that close out Phase B, each registered
into the shared-guardrails engine and reachable by its ``type``:

  * ``output_structure``     — validate post_llm output against a JSON Schema
    (malformed / non-conforming output triggers; valid output passes);
  * ``allowed_domains``      — URLs in output / tool-args must be within an
    allowlist (off-allowlist host blocks; allowlisted host + subdomain pass);
  * ``cost_ceiling``         — a per-call / accumulated cost over the ceiling
    triggers (cost is injected via metadata — real pricing is Phase C);
  * ``factuality_citations`` — unsupported numeric / quoted claims are flagged
    (a claim with an inline citation passes);
  * ``topic_restriction``    — output off the allowed topics / on a forbidden
    topic is flagged (on-topic passes);
  * ``rate_per_agent``       — an agent over the per-window rate limit blocks
    (state + clock injected for determinism);
  * ``forbidden_actions``    — a denylisted / not-allowlisted tool blocks at
    ``pre_tool`` (the allowed_tools enforcement deferred by the 06.14 audit).

Pure-Python detection (jsonschema is a lightweight pure-Python base dep; the
rest is stdlib) — no heavy / model dependency, so the whole suite runs
everywhere incl. CI. Stateless / in-process — no DB or tenant-owned rows, so
no ``cross_tenant`` marker.
"""

from __future__ import annotations

import json

import pytest
from shared_guardrails import (
    Action,
    GuardrailConfigError,
    GuardrailContext,
    Severity,
    default_registry,
)
from shared_guardrails.checks.allowed_domains import AllowedDomainsGuardrail
from shared_guardrails.checks.cost_ceiling import CostCeilingGuardrail
from shared_guardrails.checks.factuality_citations import (
    FactualityCitationsGuardrail,
    find_unsupported_claims,
)
from shared_guardrails.checks.forbidden_actions import ForbiddenActionsGuardrail
from shared_guardrails.checks.output_structure import OutputStructureGuardrail
from shared_guardrails.checks.rate_per_agent import (
    InMemoryRateStore,
    RatePerAgentGuardrail,
)
from shared_guardrails.checks.topic_restriction import TopicRestrictionGuardrail

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# Registry wiring — every bundle type is reachable by its `type`.             #
# --------------------------------------------------------------------------- #

_BUNDLE_TYPES = (
    "output_structure",
    "allowed_domains",
    "cost_ceiling",
    "factuality_citations",
    "topic_restriction",
    "rate_per_agent",
    "forbidden_actions",
)


@pytest.mark.parametrize("type_name", _BUNDLE_TYPES)
def test_bundle_type_is_registered(type_name: str) -> None:
    assert default_registry.is_registered(type_name)


def test_build_via_registry_returns_instances() -> None:
    schema = {"type": "object"}
    guard = default_registry.build("output_structure", {"schema": schema})
    assert isinstance(guard, OutputStructureGuardrail)


# --------------------------------------------------------------------------- #
# output_structure — JSON Schema validation                                   #
# --------------------------------------------------------------------------- #

_PERSON_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "age": {"type": "integer", "minimum": 0},
    },
    "required": ["name", "age"],
    "additionalProperties": False,
}


def test_output_structure_conforming_passes() -> None:
    guard = OutputStructureGuardrail({"schema": _PERSON_SCHEMA})
    out = json.dumps({"name": "Ada", "age": 36})
    result = guard.check(GuardrailContext(hook="post_llm", response=out))
    assert result.triggered is False
    assert result.payload["valid"] is True


def test_output_structure_schema_violation_blocks() -> None:
    guard = OutputStructureGuardrail({"schema": _PERSON_SCHEMA})
    out = json.dumps({"name": "Ada"})  # missing required 'age'
    result = guard.check(GuardrailContext(hook="post_llm", response=out))
    assert result.triggered is True
    assert result.payload["reason"] == "schema_violation"
    assert len(result.payload["errors"]) >= 1
    # Default action steers the model back via a corrective re-prompt.
    assert result.suggested_action is Action.RETRY_WITH_FEEDBACK


def test_output_structure_non_json_triggers() -> None:
    guard = OutputStructureGuardrail({"schema": _PERSON_SCHEMA})
    result = guard.check(GuardrailContext(hook="post_llm", response="not json at all {"))
    assert result.triggered is True
    assert result.payload["reason"] == "not_json"


def test_output_structure_validates_structured_tool_result() -> None:
    guard = OutputStructureGuardrail({"schema": _PERSON_SCHEMA})
    ctx = GuardrailContext(
        hook="post_tool", tool_name="lookup", tool_result={"name": "x", "age": -1}
    )
    result = guard.check(ctx)
    assert result.triggered is True
    assert result.payload["reason"] == "schema_violation"


def test_output_structure_reports_error_paths() -> None:
    guard = OutputStructureGuardrail({"schema": _PERSON_SCHEMA})
    out = json.dumps({"name": "Ada", "age": -5})  # age below minimum
    result = guard.check(GuardrailContext(hook="post_llm", response=out))
    assert result.triggered is True
    assert "age" in " ".join(result.payload["error_paths"])


def test_output_structure_requires_schema() -> None:
    with pytest.raises(GuardrailConfigError):
        OutputStructureGuardrail({})


def test_output_structure_rejects_invalid_schema() -> None:
    with pytest.raises(GuardrailConfigError):
        OutputStructureGuardrail({"schema": {"type": "not-a-real-type"}})


def test_output_structure_action_override_wins() -> None:
    guard = OutputStructureGuardrail({"schema": _PERSON_SCHEMA, "suggested_action": "block"})
    result = guard.check(GuardrailContext(hook="post_llm", response="{}"))
    assert result.suggested_action is Action.BLOCK


# --------------------------------------------------------------------------- #
# allowed_domains — URL allowlist                                             #
# --------------------------------------------------------------------------- #


def test_allowed_domains_off_allowlist_blocks() -> None:
    guard = AllowedDomainsGuardrail({"allowed_domains": ["example.com"]})
    text = "See https://evil.example.org/steal for details."
    result = guard.check(GuardrailContext(hook="post_llm", response=text))
    assert result.triggered is True
    assert result.suggested_action is Action.BLOCK
    assert any(h["host"] == "evil.example.org" for h in result.payload["disallowed"])


def test_allowed_domains_allowlisted_host_passes() -> None:
    guard = AllowedDomainsGuardrail({"allowed_domains": ["example.com"]})
    text = "Docs at https://example.com/guide and https://api.example.com/v1."
    result = guard.check(GuardrailContext(hook="post_llm", response=text))
    # Both the bare domain and a subdomain are within the allowlist.
    assert result.triggered is False


def test_allowed_domains_scans_tool_args_at_pre_tool() -> None:
    guard = AllowedDomainsGuardrail({"allowed_domains": ["example.com"]})
    ctx = GuardrailContext(
        hook="pre_tool",
        tool_name="http_fetch",
        tool_args={"url": "https://malware.test/payload"},
    )
    result = guard.check(ctx)
    assert result.triggered is True
    assert any(h["host"] == "malware.test" for h in result.payload["disallowed"])


def test_allowed_domains_no_urls_passes() -> None:
    guard = AllowedDomainsGuardrail({"allowed_domains": ["example.com"]})
    result = guard.check(GuardrailContext(hook="post_llm", response="plain text, no links"))
    assert result.triggered is False


def test_allowed_domains_requires_allowlist() -> None:
    with pytest.raises(GuardrailConfigError):
        AllowedDomainsGuardrail({"allowed_domains": []})


# --------------------------------------------------------------------------- #
# cost_ceiling — injected cost over threshold                                 #
# --------------------------------------------------------------------------- #


def test_cost_ceiling_over_call_cost_blocks() -> None:
    guard = CostCeilingGuardrail({"max_cost": 1.0})
    ctx = GuardrailContext(hook="pre_llm", prompt="...", metadata={"call_cost": 2.0})
    result = guard.check(ctx)
    assert result.triggered is True
    assert result.severity is Severity.HIGH
    assert result.suggested_action is Action.BLOCK
    assert result.payload["reason"] == "budget_exceeded"
    assert result.payload["scope"] == "call"


def test_cost_ceiling_under_threshold_passes() -> None:
    guard = CostCeilingGuardrail({"max_cost": 1.0})
    ctx = GuardrailContext(hook="pre_llm", prompt="...", metadata={"call_cost": 0.5})
    assert guard.check(ctx).triggered is False


def test_cost_ceiling_accumulated_over_blocks() -> None:
    guard = CostCeilingGuardrail({"max_cost": 10.0, "scope": "accumulated"})
    ctx = GuardrailContext(hook="pre_llm", prompt="...", metadata={"accumulated_cost": 12.5})
    result = guard.check(ctx)
    assert result.triggered is True
    assert result.payload["scope"] == "accumulated"


def test_cost_ceiling_no_cost_in_metadata_passes() -> None:
    guard = CostCeilingGuardrail({"max_cost": 1.0})
    assert guard.check(GuardrailContext(hook="pre_llm", prompt="...")).triggered is False


def test_cost_ceiling_requires_positive_max_cost() -> None:
    with pytest.raises(GuardrailConfigError):
        CostCeilingGuardrail({"max_cost": 0})
    with pytest.raises(GuardrailConfigError):
        CostCeilingGuardrail({})


# --------------------------------------------------------------------------- #
# factuality_citations — unsupported claims                                   #
# --------------------------------------------------------------------------- #


def test_factuality_unsupported_numeric_claim_flagged() -> None:
    guard = FactualityCitationsGuardrail({})
    text = "Revenue grew by 47% in the last quarter."
    result = guard.check(GuardrailContext(hook="post_llm", response=text))
    assert result.triggered is True
    assert result.severity is Severity.LOW
    assert result.suggested_action is Action.WARN
    assert result.payload["unsupported_count"] >= 1


def test_factuality_cited_claim_passes() -> None:
    guard = FactualityCitationsGuardrail({})
    text = "Revenue grew by 47% in the last quarter [1]. See https://example.com/report."
    result = guard.check(GuardrailContext(hook="post_llm", response=text))
    assert result.triggered is False


def test_factuality_no_factual_claim_passes() -> None:
    guard = FactualityCitationsGuardrail({})
    text = "Thanks for your question. I will help you with that task."
    assert guard.check(GuardrailContext(hook="post_llm", response=text)).triggered is False


def test_factuality_strict_requires_per_sentence_citation() -> None:
    # A citation elsewhere does not rescue an uncited numeric sentence.
    guard = FactualityCitationsGuardrail({"require_document_citation": True})
    text = "Sales rose 12% this year. For background see https://example.com."
    result = guard.check(GuardrailContext(hook="post_llm", response=text))
    assert result.triggered is True


def test_find_unsupported_claims_marks_quotes() -> None:
    findings = find_unsupported_claims('He said "the project will ship in March".')
    assert any(f.kind == "quote" for f in findings)


# --------------------------------------------------------------------------- #
# topic_restriction — off-topic / forbidden topic                            #
# --------------------------------------------------------------------------- #


def test_topic_restriction_off_topic_flagged() -> None:
    guard = TopicRestrictionGuardrail({"allowed_topics": ["billing", "shipping"]})
    text = "Let me tell you about ancient Roman history and gladiators."
    result = guard.check(GuardrailContext(hook="post_llm", response=text))
    assert result.triggered is True
    assert result.payload["reason"] == "off_topic"
    assert result.suggested_action is Action.WARN


def test_topic_restriction_on_topic_passes() -> None:
    guard = TopicRestrictionGuardrail({"allowed_topics": ["billing", "shipping"]})
    text = "Your billing statement for this month is attached."
    assert guard.check(GuardrailContext(hook="post_llm", response=text)).triggered is False


def test_topic_restriction_forbidden_topic_flagged() -> None:
    guard = TopicRestrictionGuardrail(
        {"forbidden_topics": {"medical": ["diagnosis", "prescription"]}}
    )
    text = "Based on your symptoms, my diagnosis is a viral infection."
    result = guard.check(GuardrailContext(hook="post_llm", response=text))
    assert result.triggered is True
    assert result.payload["reason"] == "forbidden_topic"
    assert "medical" in result.payload["forbidden_hits"]


def test_topic_restriction_requires_some_topics() -> None:
    with pytest.raises(GuardrailConfigError):
        TopicRestrictionGuardrail({})


def test_topic_restriction_word_boundary_no_false_positive() -> None:
    # "art" must not match "start"/"started".
    guard = TopicRestrictionGuardrail({"forbidden_topics": ["art"]})
    text = "We started the deployment and restarted the server."
    assert guard.check(GuardrailContext(hook="post_llm", response=text)).triggered is False


# --------------------------------------------------------------------------- #
# rate_per_agent — per-window call rate                                       #
# --------------------------------------------------------------------------- #


class _FakeClock:
    """Deterministic, manually-advanced clock for the rate-limit window."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_rate_per_agent_over_limit_blocks() -> None:
    clock = _FakeClock()
    guard = RatePerAgentGuardrail(
        {"max_calls": 3, "window_seconds": 60, "store": InMemoryRateStore(), "clock": clock}
    )
    ctx = GuardrailContext(hook="pre_llm", prompt="...", metadata={"agent_id": "agent-a"})

    # First three calls are within the limit.
    for _ in range(3):
        assert guard.check(ctx).triggered is False
    # The fourth call within the window trips the limit.
    result = guard.check(ctx)
    assert result.triggered is True
    assert result.suggested_action is Action.BLOCK
    assert result.payload["agent"] == "agent-a"
    assert result.payload["count"] == 4


def test_rate_per_agent_window_slides() -> None:
    clock = _FakeClock()
    guard = RatePerAgentGuardrail(
        {"max_calls": 2, "window_seconds": 10, "store": InMemoryRateStore(), "clock": clock}
    )
    ctx = GuardrailContext(hook="pre_llm", prompt="...", metadata={"agent_id": "agent-b"})
    assert guard.check(ctx).triggered is False
    assert guard.check(ctx).triggered is False
    # Advance past the window — earlier calls fall out, so we are under again.
    clock.now += 11
    assert guard.check(ctx).triggered is False


def test_rate_per_agent_is_per_agent() -> None:
    clock = _FakeClock()
    store = InMemoryRateStore()
    guard = RatePerAgentGuardrail(
        {"max_calls": 1, "window_seconds": 60, "store": store, "clock": clock}
    )
    a = GuardrailContext(hook="pre_llm", prompt="...", metadata={"agent_id": "a"})
    b = GuardrailContext(hook="pre_llm", prompt="...", metadata={"agent_id": "b"})
    assert guard.check(a).triggered is False
    assert guard.check(b).triggered is False  # different agent, own bucket
    assert guard.check(a).triggered is True  # a's second call trips


def test_rate_per_agent_requires_positive_config() -> None:
    with pytest.raises(GuardrailConfigError):
        RatePerAgentGuardrail({"max_calls": 0, "window_seconds": 60})
    with pytest.raises(GuardrailConfigError):
        RatePerAgentGuardrail({"max_calls": 5, "window_seconds": -1})


# --------------------------------------------------------------------------- #
# forbidden_actions — tool allowlist / denylist at pre_tool                   #
# --------------------------------------------------------------------------- #


def test_forbidden_actions_denylisted_tool_blocks() -> None:
    guard = ForbiddenActionsGuardrail({"denied": ["shell_exec"]})
    ctx = GuardrailContext(hook="pre_tool", tool_name="shell_exec", tool_args={})
    result = guard.check(ctx)
    assert result.triggered is True
    assert result.suggested_action is Action.BLOCK
    assert result.payload["reason"] == "denylisted"
    assert result.payload["tool"] == "shell_exec"


def test_forbidden_actions_not_in_allowlist_blocks() -> None:
    guard = ForbiddenActionsGuardrail({"allowed": ["read_file", "search"]})
    ctx = GuardrailContext(hook="pre_tool", tool_name="delete_file", tool_args={})
    result = guard.check(ctx)
    assert result.triggered is True
    assert result.payload["reason"] == "not_in_allowlist"


def test_forbidden_actions_allowed_tool_passes() -> None:
    guard = ForbiddenActionsGuardrail({"allowed": ["read_file", "search"]})
    ctx = GuardrailContext(hook="pre_tool", tool_name="read_file", tool_args={})
    assert guard.check(ctx).triggered is False


def test_forbidden_actions_enforces_metadata_allowlist() -> None:
    """The deferred 06.14 allowed_tools enforcement: chat-mode allowlist via metadata."""
    guard = ForbiddenActionsGuardrail({})  # rely entirely on per-call metadata
    allowed = GuardrailContext(
        hook="pre_tool", tool_name="read_file", metadata={"allowed_tools": ["read_file"]}
    )
    blocked = GuardrailContext(
        hook="pre_tool", tool_name="write_file", metadata={"allowed_tools": ["read_file"]}
    )
    assert guard.check(allowed).triggered is False
    res = guard.check(blocked)
    assert res.triggered is True
    assert res.payload["reason"] == "not_in_allowlist"


def test_forbidden_actions_noop_outside_tool_hooks() -> None:
    guard = ForbiddenActionsGuardrail({"denied": ["shell_exec"]})
    # No tool name at an LLM hook -> nothing to enforce.
    assert guard.check(GuardrailContext(hook="pre_llm", prompt="run shell_exec")).triggered is False


def test_forbidden_actions_requires_some_policy() -> None:
    with pytest.raises(GuardrailConfigError):
        ForbiddenActionsGuardrail({"allowed_metadata_key": ""})


def test_forbidden_actions_case_insensitive() -> None:
    guard = ForbiddenActionsGuardrail({"denied": ["Shell_Exec"], "case_insensitive": True})
    ctx = GuardrailContext(hook="pre_tool", tool_name="shell_exec")
    assert guard.check(ctx).triggered is True
