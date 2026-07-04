"""g5: the MCP picker must not offer templates that cannot start.

`docling-mcp` is a stdio template whose binary upstream publishes no image (the
service is commented out in docker-compose). Offering it in the picker sets an
agent up to fail. It stays in CATALOG (KB ingestion references Docling via the
separate docling-serve HTTP path) but is withheld from the assignable list
(audit 2026-07-03, g5).
"""

from __future__ import annotations

from api_server.routers.mcp_catalog import _UNAVAILABLE_TEMPLATE_IDS
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
