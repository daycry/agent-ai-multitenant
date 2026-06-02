"""Unit tests for the active-family filter on the LiteLLM sync (plan
price-sync-active-providers, task_psa_01).

The filter has two **pure** pieces — testable in-process, no DB, no network:

  - the kind→family map + :func:`families_for_kinds` resolver (the union of an
    active-provider kind list onto its LiteLLM families, per ADR 0028);
  - :func:`parse_feed`'s ``allowed_families`` argument: a mapped entry whose
    ``litellm_provider`` family is NOT in the allowlist is dropped as a typed
    ``family_not_active`` skip; ``None`` disables the filter (the old
    behaviour); an EMPTY frozenset keeps nothing.

The DB-backed pieces (the resolver's ``llm_providers`` query + the
period-closing write side + the endpoint wiring) live in
``tests/integration/test_price_sync_active_families.py``.
"""

from __future__ import annotations

import pytest
from api_server.pricing.litellm_sync import (
    KIND_TO_LITELLM_FAMILIES,
    SKIP_FAMILY_NOT_ACTIVE,
    families_for_kinds,
    parse_feed,
)

pytestmark = pytest.mark.unit


# ===========================================================================
# The kind→family map matches ADR 0028 exactly.
# ===========================================================================
def test_kind_to_family_map_is_adr_0028() -> None:
    assert KIND_TO_LITELLM_FAMILIES["claude_sdk"] == frozenset({"anthropic"})
    assert KIND_TO_LITELLM_FAMILIES["azure_foundry"] == frozenset({"azure", "azure_ai", "openai"})
    assert KIND_TO_LITELLM_FAMILIES["copilot"] == frozenset({"openai", "anthropic"})
    assert KIND_TO_LITELLM_FAMILIES["ollama"] == frozenset({"ollama"})
    # The four ADR-0021 kinds and nothing else (a fifth needs an ADR).
    assert set(KIND_TO_LITELLM_FAMILIES) == {
        "claude_sdk",
        "azure_foundry",
        "copilot",
        "ollama",
    }


# ===========================================================================
# families_for_kinds unions the families (pure).
# ===========================================================================
def test_families_for_kinds_single() -> None:
    assert families_for_kinds(["ollama"]) == frozenset({"ollama"})
    assert families_for_kinds(["claude_sdk"]) == frozenset({"anthropic"})


def test_families_for_kinds_unions_multiple() -> None:
    # claude_sdk (anthropic) + azure_foundry (azure/azure_ai/openai).
    assert families_for_kinds(["claude_sdk", "azure_foundry"]) == frozenset(
        {"anthropic", "azure", "azure_ai", "openai"}
    )


def test_families_for_kinds_dedupes_overlap() -> None:
    # copilot (openai, anthropic) + claude_sdk (anthropic) -> anthropic once.
    assert families_for_kinds(["copilot", "claude_sdk"]) == frozenset({"openai", "anthropic"})


def test_families_for_kinds_empty_is_empty() -> None:
    assert families_for_kinds([]) == frozenset()


def test_families_for_kinds_unknown_kind_contributes_nothing() -> None:
    # An unknown kind never crashes — it simply adds no families.
    assert families_for_kinds(["not_a_kind"]) == frozenset()
    assert families_for_kinds(["ollama", "not_a_kind"]) == frozenset({"ollama"})


# ===========================================================================
# parse_feed allowed_families filter.
# ===========================================================================
def _feed() -> dict:
    return {
        "claude-sonnet-4-5": {
            "litellm_provider": "anthropic",
            "mode": "chat",
            "input_cost_per_token": 0.000003,
            "output_cost_per_token": 0.000015,
        },
        "gpt-4o": {
            "litellm_provider": "openai",
            "mode": "chat",
            "input_cost_per_token": 0.0000025,
            "output_cost_per_token": 0.00001,
        },
        "llama3": {
            "litellm_provider": "ollama",
            "mode": "chat",
            "input_cost_per_token": 0.0000001,
            "output_cost_per_token": 0.0000002,
        },
    }


def test_parse_feed_none_keeps_everything_backward_compatible() -> None:
    mapped, skipped = parse_feed(_feed(), allowed_families=None)
    assert {m.provider for m in mapped} == {"anthropic", "openai", "ollama"}
    assert skipped == []


def test_parse_feed_default_arg_keeps_everything() -> None:
    # The default (no kwarg) is None — the old unfiltered behaviour.
    mapped, skipped = parse_feed(_feed())
    assert len(mapped) == 3
    assert skipped == []


def test_parse_feed_filters_out_of_scope_families() -> None:
    mapped, skipped = parse_feed(_feed(), allowed_families=frozenset({"ollama"}))
    # Only the ollama entry survives; anthropic + openai are typed skips.
    assert {m.provider for m in mapped} == {"ollama"}
    assert {s.model_key for s in skipped} == {"claude-sonnet-4-5", "gpt-4o"}
    assert all(s.reason == SKIP_FAMILY_NOT_ACTIVE for s in skipped)


def test_parse_feed_empty_allowlist_keeps_nothing() -> None:
    mapped, skipped = parse_feed(_feed(), allowed_families=frozenset())
    assert mapped == []
    # Every mappable entry is an out-of-scope skip.
    assert {s.model_key for s in skipped} == {"claude-sonnet-4-5", "gpt-4o", "llama3"}
    assert all(s.reason == SKIP_FAMILY_NOT_ACTIVE for s in skipped)


def test_parse_feed_malformed_skip_takes_precedence_over_family_filter() -> None:
    """A malformed entry is a parse skip (its own reason), never a family skip.

    The filter only runs on a successfully-mapped entry, so a row with no
    provider is captured with its parse reason — not ``family_not_active`` —
    even when an allowlist is active."""
    feed = {
        "broken": {"mode": "chat", "input_cost_per_token": 0.000001},  # no provider
        "llama3": {
            "litellm_provider": "ollama",
            "mode": "chat",
            "input_cost_per_token": 0.0000001,
        },
    }
    mapped, skipped = parse_feed(feed, allowed_families=frozenset({"ollama"}))
    assert {m.provider for m in mapped} == {"ollama"}
    by_key = {s.model_key: s.reason for s in skipped}
    assert by_key["broken"] != SKIP_FAMILY_NOT_ACTIVE
    assert "provider" in by_key["broken"]


def test_parse_feed_in_scope_family_with_malformed_others() -> None:
    """An in-scope family is kept even when out-of-scope ones are dropped."""
    mapped, skipped = parse_feed(_feed(), allowed_families=frozenset({"anthropic", "openai"}))
    assert {m.provider for m in mapped} == {"anthropic", "openai"}
    assert {s.model_key for s in skipped} == {"llama3"}
    assert skipped[0].reason == SKIP_FAMILY_NOT_ACTIVE
