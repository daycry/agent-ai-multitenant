"""Meta-test for docling-mcp in the compose stack (Plan 04 task_04_21).

Same shape as `test_docling_serve_compose.py`: the roadmap's
automated check is a curl probe against the live container, which
unit tests can't run. We verify the compose file declares the
service on port 3000 with a /health probe on the agentic network.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit


def _load_compose() -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[2]
    compose_path = repo_root / "docker" / "docker-compose.yml"
    return yaml.safe_load(compose_path.read_text(encoding="utf-8"))


def test_docling_mcp_service_exists() -> None:
    compose = _load_compose()
    assert (
        "docling-mcp" in compose["services"]
    ), "docling-mcp service missing from docker/docker-compose.yml"


def test_docling_mcp_image_is_open_source_docling() -> None:
    compose = _load_compose()
    image = compose["services"]["docling-mcp"]["image"]
    assert "docling-project/docling-mcp" in image, image


def test_docling_mcp_has_healthcheck_on_port_3000() -> None:
    compose = _load_compose()
    svc = compose["services"]["docling-mcp"]
    hc = svc.get("healthcheck")
    assert hc is not None, "no healthcheck on docling-mcp"
    test_cmd = " ".join(hc["test"]) if isinstance(hc["test"], list) else hc["test"]
    assert "3000" in test_cmd
    assert "/health" in test_cmd


def test_docling_mcp_joins_agentic_net() -> None:
    compose = _load_compose()
    svc = compose["services"]["docling-mcp"]
    networks = svc.get("networks") or []
    assert "agentic-net" in networks, networks


def test_docling_mcp_and_docling_serve_are_separate_services() -> None:
    """The two services should NOT share the image — they expose
    different surfaces (HTTP REST vs MCP-over-HTTP)."""
    compose = _load_compose()
    serve_image = compose["services"]["docling-serve"]["image"]
    mcp_image = compose["services"]["docling-mcp"]["image"]
    assert serve_image != mcp_image
