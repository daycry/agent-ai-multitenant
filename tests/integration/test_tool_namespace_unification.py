"""Contract tests: the catalog, chat-modes, runtime approval gate and the
per-agent ∩ mode intersection all speak ONE canonical tool namespace
(ADR 0048, Plan 06.18 task_06_18_03).

These pin the unification so the three historical namespaces
(``read_file`` / ``file_read`` / ``file_read``) cannot silently diverge again:

  * every catalog ``Tool.name`` is canonical;
  * every chat-mode allowlist name resolves to canonical names;
  * the runtime approval gate (``agent_runtime.approval``) keys its sensitive
    categories on canonical names AND tolerates a legacy alias arriving at
    call time — so a write / network call cannot slip past the human-approval
    gate by mere name mismatch (the latent bypass in ADR 0048);
  * ``combine_tool_allowlists`` no longer intersects to the empty set just
    because the agent used the catalog name and the mode used the legacy one.

Pure imports (no DB/Redis), but marked ``integration`` because they cross the
api-server / agent-runtime / shared-domain package boundary.
"""

from __future__ import annotations

import pytest
from agent_runtime.approval import DEFAULT_TOOL_CATEGORIES, ApprovalGate
from api_server.agent_tools_enforcement import combine_tool_allowlists
from api_server.chat.modes import BUILTIN_MODES
from api_server.seeds.builtin_tools import BUILTIN_TOOLS
from shared_domain.tool_names import CANONICAL_TOOL_NAMES, to_canonical, to_canonical_set

pytestmark = pytest.mark.integration


def test_catalog_names_are_canonical() -> None:
    for tool in BUILTIN_TOOLS:
        assert tool.name in CANONICAL_TOOL_NAMES, tool.name


def test_chat_mode_allowlists_resolve_to_canonical_names() -> None:
    for mode in BUILTIN_MODES.values():
        for name in mode.allowed_tools:
            resolved = to_canonical_set([name])
            assert resolved <= CANONICAL_TOOL_NAMES, (mode.name, name, resolved)


def test_approval_categories_are_keyed_on_canonical_names() -> None:
    # Every key in the approval gate's category map must already be canonical
    # (so it matches what the runtime registers), not a legacy chat-mode alias.
    for name in DEFAULT_TOOL_CATEGORIES:
        assert to_canonical(name) == frozenset({name}), name


def test_approval_gate_gates_canonical_write_and_network() -> None:
    # A write (canonical write_file) and a network call (canonical http_get/
    # http_post) must be classified as sensitive — the bypass ADR 0048 flags.
    write_gate = ApprovalGate({"categories": {"code_changes": "human_required"}})
    assert write_gate.review("write_file") == "code_changes"

    net_gate = ApprovalGate(
        {
            "categories": {
                "external_http_get": "human_required",
                "external_http_post": "human_required",
            }
        }
    )
    assert net_gate.review("http_get") == "external_http_get"
    assert net_gate.review("http_post") == "external_http_post"


def test_approval_gate_tolerates_legacy_alias_at_call_time() -> None:
    # Defence in depth: even if a legacy alias name reaches the gate, it still
    # resolves to the canonical category (no bypass).
    write_gate = ApprovalGate({"categories": {"code_changes": "human_required"}})
    assert write_gate.review("file_write") == "code_changes"


def test_combine_no_longer_empties_on_name_mismatch() -> None:
    # The whole point of ADR 0048: catalog name vs chat-mode alias for the same
    # action must intersect to the canonical name, not the empty set.
    assert combine_tool_allowlists({"read_file"}, ["file_read"]) == ["read_file"]
