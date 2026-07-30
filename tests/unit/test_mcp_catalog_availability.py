"""g5: the MCP picker must not offer templates that cannot start.

`docling-mcp` is a stdio template whose binary upstream publishes no image (the
service is commented out in docker-compose). Offering it in the picker sets an
agent up to fail. It stays in CATALOG (KB ingestion references Docling via the
separate docling-serve HTTP path) but is withheld from the assignable list
(audit 2026-07-03, g5).
"""

from __future__ import annotations

from api_server.routers.mcp_catalog import (
    _UNAVAILABLE_TEMPLATE_IDS,
    offered_catalog,
)
from shared_mcp.catalog import CATALOG


def test_docling_mcp_is_known_but_not_offered() -> None:
    assert "docling-mcp" in CATALOG  # still catalogued (KB pipeline reference)
    assert "docling-mcp" in _UNAVAILABLE_TEMPLATE_IDS


def test_offered_templates_exclude_unavailable_ones() -> None:
    offered = {tid for tid in CATALOG if tid not in _UNAVAILABLE_TEMPLATE_IDS}
    assert "docling-mcp" not in offered
    # Sanity: withholding one template does not empty the picker.
    assert len(offered) >= len(CATALOG) - len(_UNAVAILABLE_TEMPLATE_IDS)
    assert offered  # at least some templates remain assignable


def test_unavailable_ids_are_real_catalog_entries() -> None:
    # A guard against a typo silently withholding nothing (or drifting).
    assert set(CATALOG) >= _UNAVAILABLE_TEMPLATE_IDS


# ---------------------------------------------------------------------------
# ADR 0117: the agent-runtime cannot spawn stdio binaries (none are packaged
# in the image), so the picker offers ONLY HTTP-transport templates. The stdio
# templates stay CATALOGUED (conduct-time validation + audit trail) but are
# never offered. Atlassian ships as an HTTP sidecar, Context7/GitHub as remote.
# ---------------------------------------------------------------------------
def test_catalog_offers_only_http_transports() -> None:
    offered = offered_catalog()
    assert offered, "the picker must not be empty"
    assert all(t.transport in ("sse", "streamable_http") for t in offered)


def test_stdio_templates_are_never_offered() -> None:
    offered_ids = {t.id for t in offered_catalog()}
    stdio_ids = {tid for tid, t in CATALOG.items() if t.transport == "stdio"}
    assert stdio_ids  # sanity: the stdio templates are still catalogued…
    assert offered_ids.isdisjoint(stdio_ids)  # …but none are offered


def test_working_http_templates_are_offered() -> None:
    offered_ids = {t.id for t in offered_catalog()}
    assert {"context7", "atlassian", "github-remote"} <= offered_ids


# ---------------------------------------------------------------------------
# ADR 0127: every template declares HOW its requests authenticate, so the
# picker knows whether to show a token field ("static"), a "Connect" button
# ("oauth"), a "deploy the sidecar" hint ("sidecar"), or nothing ("none").
# The OAuth runtime (token store + connect flow) is a separate, interactively-
# verified phase.
# ---------------------------------------------------------------------------
def test_templates_declare_a_valid_auth_kind() -> None:
    assert all(t.auth_kind in ("none", "static", "oauth", "sidecar") for t in CATALOG.values())


def test_offered_http_templates_have_expected_auth_kind() -> None:
    kinds = {t.id: t.auth_kind for t in offered_catalog()}
    assert kinds["context7"] == "none"  # optional key; no required per-request auth
    assert kinds["atlassian"] == "sidecar"  # self-hosted sidecar authenticates via its own env
    assert kinds["github-remote"] == "static"  # PAT bearer from Vault per request


def test_oauth_remote_is_catalogued_and_offered() -> None:
    # ADR 0127: the official OAuth remote (auth_kind "oauth") is now OFFERED —
    # the interactive «Connect» flow is wired (routers/mcp_oauth.py + frontend).
    assert CATALOG["atlassian-remote"].auth_kind == "oauth"
    assert "atlassian-remote" in {t.id for t in offered_catalog()}
