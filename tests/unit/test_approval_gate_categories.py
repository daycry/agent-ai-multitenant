"""g6 regression: the runtime approval gate must speak the preset vocabulary.

The gate mapped tools to categories (``code_execution``/``file_write``/...) that
intersected NONE of the 13 canonical preset categories, so ``requires_human``
always returned ``auto`` and no tool was ever gated — not even under the
``customer-external`` preset (audit 2026-07-03, g6, fail-open). These tests pin
(a) every gate category to the single-source ``APPROVAL_CATEGORIES`` and (b) the
end-to-end behaviour: a strict preset actually stops a sensitive tool.
"""

from __future__ import annotations

from agent_runtime.approval import DEFAULT_TOOL_CATEGORIES, ApprovalGate
from shared_domain.approval_categories import APPROVAL_CATEGORIES


def _preset(decision: str) -> dict[str, dict[str, str]]:
    """A policy where every canonical category takes `decision` (mirrors the
    sandbox = all-auto / customer-external = all-human_required seeds)."""
    return {"categories": {cat: decision for cat in APPROVAL_CATEGORIES}}


def test_every_gate_category_is_canonical() -> None:
    """The fail-open bug: gate categories that are not in the preset vocabulary
    can never be `human_required`, so the tool silently runs."""
    canonical = set(APPROVAL_CATEGORIES)
    for tool, category in DEFAULT_TOOL_CATEGORIES.items():
        assert category in canonical, (
            f"{tool} maps to non-canonical category {category!r} → it would "
            f"fail-open (never gated) under any preset"
        )


def test_customer_external_preset_gates_sensitive_tools() -> None:
    gate = ApprovalGate(_preset("human_required"))
    # The dangerous tools are now actually parked for human approval.
    assert gate.review("http_post") == "external_http_post"
    assert gate.review("http_get") == "external_http_get"
    assert gate.review("shell_exec") == "code_changes"
    assert gate.review("stack_exec") == "code_changes"
    assert gate.review("write_file") == "code_changes"


def test_sandbox_preset_gates_nothing() -> None:
    gate = ApprovalGate(_preset("auto"))
    for tool in DEFAULT_TOOL_CATEGORIES:
        assert gate.review(tool) is None


def test_unmapped_tool_is_never_gated() -> None:
    gate = ApprovalGate(_preset("human_required"))
    assert gate.review("read_file") is None
    assert gate.review(None) is None


def test_old_broken_categories_are_not_canonical() -> None:
    """Documents the regression: the previous vocabulary had zero overlap."""
    old = {"code_execution", "file_write", "network_access", "agent_delegation"}
    assert old.isdisjoint(set(APPROVAL_CATEGORIES))
