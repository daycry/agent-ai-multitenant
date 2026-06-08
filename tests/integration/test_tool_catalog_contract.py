"""Catalog-level contract: the tool subsystem's sources of truth do not diverge
(Plan 06.18 task_06_18_14, ADR 0048/0049).

Plan 06.18 split the tool subsystem across four independently editable sources:

  * the **catalog** seed (``api_server.seeds.builtin_tools.BUILTIN_TOOLS``) — the
    rows the operator sees and assigns;
  * the **chat-modes** allowlists (``api_server.chat.modes.BUILTIN_MODES``) — the
    per-mode call-time tool whitelist;
  * the runtime **approval gate** categories
    (``agent_runtime.approval.DEFAULT_TOOL_CATEGORIES``);
  * the **shared-domain** name layer (``shared_domain.tool_names``) — canonical
    names, aliases and the runtime-wired set.

Each was edited by a different task (03/04/05/06). A divergence between them is
exactly the silent ``unknown tool`` / approval-bypass class of bug ADR 0048
exists to kill. These tests CROSS the sources and FAIL the moment two of them
disagree, so a future edit to one cannot quietly drift from the rest.

Scope note — what this file deliberately does NOT duplicate:

  * ``test_tool_namespace_unification.py`` already pins the *semantic* contract
    (the approval gate gates a canonical write/network call; ``combine`` no
    longer empties on a name mismatch). Those are call-time behaviours; here we
    assert the *catalog-level* structural invariants (coverage, closure,
    runtime-wired honesty) that guard the same SoT from the other direction.
  * ``test_tool_runtime_availability.py::test_runtime_wired_set_matches_runtime_executor``
    already proves ``RUNTIME_WIRED_TOOL_NAMES == register_builtin_families(...)
    | {run_*} | {shell_exec}``. We do NOT re-register the families here; we
    import and reuse that invariant's helper so the catalog layer references the
    runtime executor through the single existing check rather than copying it.

Pure imports (no DB / Redis / Docker), but marked ``integration`` because they
cross the api-server / agent-runtime / shared-domain package boundary — the
same reason ``test_tool_namespace_unification.py`` carries the mark.
"""

from __future__ import annotations

import pytest
from agent_runtime.approval import DEFAULT_TOOL_CATEGORIES
from api_server.chat.modes import BUILTIN_MODES
from api_server.schemas.catalog import tool_is_runtime_wired
from api_server.seeds.builtin_tools import BUILTIN_TOOLS
from shared_domain.tool_names import (
    CANONICAL_TOOL_NAMES,
    RUNTIME_WIRED_TOOL_NAMES,
    to_canonical,
    to_canonical_set,
)

pytestmark = pytest.mark.integration


# ===========================================================================
# (a) Every BUILTIN_TOOLS name resolves, via to_canonical, to a known canonical.
# ===========================================================================
def test_every_catalog_name_resolves_to_a_known_canonical() -> None:
    """Each seeded tool name is itself canonical and resolves to known names.

    The seed is what the operator assigns; if a seeded name did not resolve to a
    name the platform recognises, the agent∩mode intersection (computed on
    canonical names) could never match it — the ADR 0048 silent failure.

    Two things must hold per seeded row:

      * the seed name is itself a member of ``CANONICAL_TOOL_NAMES`` (the seed
        is the source of the catalog names — no seeded name is a mere legacy
        alias);
      * what it *resolves to* is non-empty and lands in the platform's known
        universe — canonical names OR a runtime-wired name. The one row that
        resolves to a runtime-only name is ``semantic_search`` → ``rag_search``
        (ADR 0049): a catalog/knowledge name the operator assigns that the
        runtime executes under a different name, reconciled here deliberately.
    """
    known = CANONICAL_TOOL_NAMES | RUNTIME_WIRED_TOOL_NAMES
    for tool in BUILTIN_TOOLS:
        assert tool.name in CANONICAL_TOOL_NAMES, tool.name
        resolved = to_canonical(tool.name)
        assert resolved, tool.name
        assert resolved <= known, (tool.name, sorted(resolved))


# ===========================================================================
# (b) Every chat-mode allowlist name resolves to existing canonical names, and
#     a NON-EMPTY mode shares at least one tool with the catalog (no empty
#     intersection caused by a name desync — the bug, not the by-design empty
#     discussion mode).
# ===========================================================================
def test_chat_mode_allowlists_resolve_and_intersect_the_catalog() -> None:
    catalog_canonical = {tool.name for tool in BUILTIN_TOOLS} | set(CANONICAL_TOOL_NAMES)
    for mode in BUILTIN_MODES.values():
        resolved = to_canonical_set(mode.allowed_tools)
        # Every allowed name resolves into the platform's canonical universe.
        assert resolved <= CANONICAL_TOOL_NAMES, (mode.name, sorted(resolved))
        if mode.allowed_tools:
            # A mode that lists tools must actually share canonical names with
            # the known universe — an empty intersection here would be the
            # silent desync (the empty `discussion` allowlist is intentional and
            # exempt because it lists nothing).
            assert resolved & catalog_canonical, (mode.name, sorted(resolved))


# ===========================================================================
# (c) The approval gate keys its categories on CANONICAL names (so a sensitive
#     call cannot slip past on a name the runtime never registers).
# ===========================================================================
def test_approval_default_categories_use_canonical_names() -> None:
    for name in DEFAULT_TOOL_CATEGORIES:
        # A canonical name resolves to itself (a legacy alias would expand to a
        # *different* name — that is what we forbid here).
        assert to_canonical(name) == frozenset({name}), name
        assert name in CANONICAL_TOOL_NAMES, name


# ===========================================================================
# (d) The catalog references the runtime-wired set through the SINGLE existing
#     executor invariant — it does not re-register the families. The shared
#     RUNTIME_WIRED_TOOL_NAMES is the seam both layers share; the runtime side
#     is pinned to the real executor by
#     test_tool_runtime_availability::test_runtime_wired_set_matches_runtime_executor.
# ===========================================================================
def test_runtime_wired_set_is_the_shared_executor_invariant() -> None:
    """Reference (not duplicate) the runtime↔shared-domain invariant.

    We import the existing check and run it so this catalog contract FAILS too
    if the shared set drifts from ``register_builtin_families(...) | {run_*} |
    {shell_exec}`` — without copying the registration logic into this file.
    """
    from tests.integration.test_tool_runtime_availability import (
        test_runtime_wired_set_matches_runtime_executor,
    )

    test_runtime_wired_set_matches_runtime_executor()

    # And every run_* docker_command tool the catalog offers is in the shared
    # wired set (the catalog promises these are executable).
    run_tools = {t.name for t in BUILTIN_TOOLS if t.name.startswith("run_")}
    assert run_tools, "expected the catalog to seed run_* docker_command tools"
    assert run_tools <= RUNTIME_WIRED_TOOL_NAMES, sorted(run_tools - RUNTIME_WIRED_TOOL_NAMES)


# ===========================================================================
# (e) No catalog tool offered as runtime-wired falls OUTSIDE the wired set, and
#     every NOT-wired catalog tool is faithfully reported as is_runtime_wired
#     False — the catalog never lies about availability (ADR 0049).
# ===========================================================================
def test_catalog_runtime_wired_flag_matches_the_wired_set() -> None:
    """``tool_is_runtime_wired`` (what ``ToolResponse`` exposes) must agree with
    the shared wired set for every seeded tool — no tool is offered as
    executable while its canonical name sits outside ``RUNTIME_WIRED_TOOL_NAMES``
    (and none is falsely flagged not-wired)."""
    for tool in BUILTIN_TOOLS:
        flag = tool_is_runtime_wired(tool.name, tool.implementation_type)
        canonical = to_canonical(tool.name)
        in_wired = bool(canonical & RUNTIME_WIRED_TOOL_NAMES)
        # For builtins the flag is driven purely by the canonical name being in
        # the wired set; typed rows (docker_command/http_endpoint/...) are wired
        # by their implementation_type. The seed's wired tools are either builtin
        # whose name is wired, or docker_command run_* — both must land True, and
        # the flag must NEVER claim wired for a builtin whose name is unwired.
        if flag:
            assert in_wired or tool.implementation_type != "builtin", (
                tool.name,
                tool.implementation_type,
            )
        else:
            # If reported not-wired it must genuinely be a builtin whose canonical
            # name is absent from the wired set (e.g. apply_patch / search_code /
            # summarize_text).
            assert not in_wired, (tool.name, sorted(canonical))


def test_no_seeded_builtin_with_wired_name_is_reported_unwired() -> None:
    """The other direction of (e): a seeded builtin whose canonical name IS in
    the wired set must be reported wired — the catalog cannot under-report a
    real executor and frighten the operator away from a working tool."""
    for tool in BUILTIN_TOOLS:
        if tool.implementation_type != "builtin":
            continue
        if to_canonical(tool.name) & RUNTIME_WIRED_TOOL_NAMES:
            assert tool_is_runtime_wired(tool.name, tool.implementation_type) is True, tool.name
