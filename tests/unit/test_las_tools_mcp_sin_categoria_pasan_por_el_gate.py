"""Toda tool MCP pasa por el gate de aprobación (`task_cv_23`, D-04).

`register_mcp_server` registra todas las tools que lista el servidor, pero el
gate sólo conocía las que el api-server serializó con `approval_category` en el
`tool_specs`; para cualquier otro nombre `ApprovalGate.review` devolvía `None`,
también bajo «Cliente Externo». Un nombre con namespace (`servidor.tool`) que no
esté catalogado cae ahora al criterio de `spec_approval_category` para
`mcp_tool`: `external_http_post`.
"""

from __future__ import annotations

import pytest
from agent_runtime.approval import ApprovalGate
from shared_domain.approval_categories import APPROVAL_CATEGORIES

pytestmark = pytest.mark.unit

_ALL_HUMAN = {"categories": dict.fromkeys(APPROVAL_CATEGORIES, "human_required")}
_ALL_AUTO = {"categories": dict.fromkeys(APPROVAL_CATEGORIES, "auto")}
_ONLY_HTTP_POST = {
    "categories": {
        **dict.fromkeys(APPROVAL_CATEGORIES, "auto"),
        "external_http_post": "human_required",
    }
}


def test_an_uncatalogued_namespaced_tool_is_gated_as_external_http_post() -> None:
    gate = ApprovalGate(_ONLY_HTTP_POST)
    assert gate.review("jira.create_issue", {"title": "x"}) == "external_http_post"


def test_the_fallback_respects_an_auto_policy() -> None:
    assert ApprovalGate(_ALL_AUTO).review("jira.create_issue", {}) is None


def test_a_catalogued_spec_category_still_wins_over_the_fallback() -> None:
    gate = ApprovalGate(_ALL_HUMAN, tool_categories={"jira.create_issue": "external_communication"})
    assert gate.review("jira.create_issue", {}) == "external_communication"


def test_builtins_without_a_dot_are_not_touched_by_the_fallback() -> None:
    """Un builtin sin categoría en el mapa (no hay ninguno hoy) no debe volverse
    `external_http_post` por accidente: el fallback es SÓLO para nombres con
    namespace, que son los que llegan de un servidor MCP."""
    gate = ApprovalGate(_ALL_HUMAN, tool_categories={})
    assert gate.review("read_file", {"path": "x"}) is None


def test_a_listed_spec_without_category_is_the_operators_opt_out() -> None:
    """La diferencia que el mapa tiene que conservar: «se decidió que no gatea»
    (spec listado sin categoría) frente a «nadie lo catalogó» (fallback)."""
    from agent_runtime.approval import tool_categories_from_specs

    gate = ApprovalGate(
        _ALL_HUMAN, tool_categories=tool_categories_from_specs([{"name": "acme.ping"}])
    )
    assert gate.review("acme.ping", {}) is None
    assert gate.review("acme.other", {}) == "external_http_post"
