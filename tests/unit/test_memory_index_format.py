"""task_mem_01 / task_mem_02 — formato del hit del índice y uso idempotente por run."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from api_server.memorizer.index_format import (
    HEADLINE_CHARS,
    estimate_tokens,
    headline_of,
    index_hit,
)
from api_server.memorizer.usage import plan_usage


def test_headline_is_the_first_120_characters_with_whitespace_collapsed() -> None:
    long = "palabra " * 100
    head = headline_of(long)
    assert len(head) <= HEADLINE_CHARS == 120
    assert head.startswith("palabra palabra")
    assert headline_of("  una\n\nlínea   corta  ") == "una línea corta"
    assert headline_of("") == ""


def test_token_estimate_is_a_quarter_of_the_characters_rounded_up() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2
    assert estimate_tokens("x" * 4000) == 1000


def test_index_hit_has_exactly_the_contract_keys_and_no_content() -> None:
    mid = uuid4()
    created = datetime(2026, 9, 30, 10, 0, tzinfo=UTC)
    hit = index_hit(
        memory_id=mid,
        content="x" * 400,
        type_="semantic",
        kind=None,
        scope="project_shared",
        created_at=created,
        agent="Backend Dev",
        project="Facturas",
        score=0.0328,
    )
    assert set(hit) == {
        "id",
        "headline",
        "type",
        "kind",
        "scope",
        "created_at",
        "agent",
        "project",
        "score",
        "tokens",
    }
    assert hit["id"] == mid
    assert hit["kind"] is None
    assert hit["tokens"] == 100
    assert len(hit["headline"]) == 120
    assert "content" not in hit


def test_usage_is_counted_once_per_memory_and_run() -> None:
    a, b, c = UUID(int=1), UUID(int=2), UUID(int=3)
    # `b` ya se recuperó en este run (índice): no vuelve a contar aunque ahora se pida su detalle
    plan = plan_usage([a, b, a, c], already_used_in_run={b})
    assert plan.to_count == [a, c]  # sin duplicados y sin lo ya contado
    assert plan.to_log == [a, b, c]  # el log registra cada consulta distinta, sin repetir


def test_usage_without_a_run_counts_every_call() -> None:
    a = UUID(int=1)
    plan = plan_usage([a], already_used_in_run=None)
    assert plan.to_count == [a] and plan.to_log == [a]
