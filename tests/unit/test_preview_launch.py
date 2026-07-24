"""ADR 0130 — on-demand app-preview request builder (pure).

``build_preview_request`` shapes the ``compose_review_runtime`` payload for a
PROJECT preview (default branch, no plan) or a PLAN preview (plan branch), or
returns ``None`` when the project pins no app-preview image.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from api_server.preview_launch import (
    PREVIEW_EXPIRES_S,
    build_preview_request,
    default_branch_of,
)

pytestmark = pytest.mark.unit


def _project(**overrides: object) -> SimpleNamespace:
    base = {
        "id": uuid4(),
        "slug": "backend",
        "repository_config": {"review_image": "backend:latest", "review_port": 3000},
        "worker_config": None,
        "git_config": {"default_branch": "develop"},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


_ORG = SimpleNamespace(slug="acme")


def test_default_branch_of() -> None:
    assert default_branch_of({"default_branch": "trunk"}) == "trunk"
    assert default_branch_of({}) == "main"
    assert default_branch_of(None) == "main"
    assert default_branch_of({"default_branch": "  "}) == "main"


def test_project_preview_uses_default_branch_and_no_plan() -> None:
    proj = _project()
    req = build_preview_request(tenant_id=uuid4(), project=proj, org=_ORG, plan=None)
    assert req is not None
    assert req["kind"] == "preview"
    assert "plan_id" not in req
    assert req["preview_ref"] == "develop"
    assert req["project_id"] == str(proj.id)
    assert req["main_image"] == "backend:latest"
    assert req["main_port"] == 3000
    assert req["tenant_slug"] == "acme"
    assert req["project_slug"] == "backend"
    assert req["repo_name"] == "backend"
    assert req["expires_in_seconds"] == PREVIEW_EXPIRES_S == 24 * 60 * 60
    assert req["repository_config"]["review_image"] == "backend:latest"


def test_plan_preview_uses_plan_branch_not_default() -> None:
    proj = _project()
    plan = SimpleNamespace(id=uuid4(), slug="my-plan")
    req = build_preview_request(tenant_id=uuid4(), project=proj, org=_ORG, plan=plan)
    assert req is not None
    assert req["kind"] == "preview"
    assert req["plan_id"] == str(plan.id)
    assert req["plan_slug"] == "my-plan"
    assert "preview_ref" not in req  # plan preview follows the plan branch


def test_no_image_yields_none() -> None:
    proj = _project(repository_config={})
    assert build_preview_request(tenant_id=uuid4(), project=proj, org=_ORG, plan=None) is None


def test_missing_repository_config_yields_none() -> None:
    proj = _project(repository_config=None)
    assert build_preview_request(tenant_id=uuid4(), project=proj, org=_ORG, plan=None) is None
