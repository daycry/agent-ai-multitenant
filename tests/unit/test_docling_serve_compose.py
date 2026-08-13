"""Meta-test for docling-serve in the compose stack (Plan 04 task_04_10).

The roadmap's automated check is ``curl -f http://docling-serve:5001/health``
— a shell probe that needs the stack up. We can't run that from unit
tests, so we verify the same intent at the compose level: the
service is declared, listens on 5001, joins the right network and
has a health probe wired.
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


def test_docling_serve_service_exists() -> None:
    compose = _load_compose()
    assert "docling-serve" in compose["services"], (
        "docling-serve service missing from docker/docker-compose.yml"
    )


def test_docling_serve_image_is_open_source_docling() -> None:
    compose = _load_compose()
    image = compose["services"]["docling-serve"]["image"]
    # Allow any docling-project image — the registry / tag can vary
    # but the project must stay the upstream `docling-project` repo
    # (no third-party forks).
    assert "docling-project/docling-serve" in image, image


def test_docling_serve_has_healthcheck_on_port_5001() -> None:
    compose = _load_compose()
    svc = compose["services"]["docling-serve"]
    hc = svc.get("healthcheck")
    assert hc is not None, "no healthcheck on docling-serve"
    # The test is a CMD-SHELL probe; it must hit the canonical /health.
    test_cmd = " ".join(hc["test"]) if isinstance(hc["test"], list) else hc["test"]
    assert "5001" in test_cmd
    assert "/health" in test_cmd


def test_docling_serve_joins_agentic_net() -> None:
    compose = _load_compose()
    svc = compose["services"]["docling-serve"]
    networks = svc.get("networks") or []
    assert "agentic-net" in networks, networks
