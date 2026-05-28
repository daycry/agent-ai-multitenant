"""Integration tests: review-runtime composition (Plan 06 task_06_26)."""

from __future__ import annotations

import time

import pytest

pytestmark = pytest.mark.integration


def _spec(tenant_id: str = "t", **overrides: object) -> object:
    from workers.review_runtime import AuxComposeService, HumanCheckItem, ReviewRuntimeSpec

    base = {
        "plan_id": "plan-1",
        "project_id": "proj-1",
        "tenant_id": tenant_id,
        "repo_name": "backend",
        "worktree_host_path": "/data/worktrees/plan-1",
        "main_image": "backend:plan-1",
        "main_port": 8080,
        "aux_services": (
            AuxComposeService(name="postgres", image="postgres:16-alpine"),
            AuxComposeService(name="redis", image="redis:7-alpine"),
        ),
        "human_checklist": (HumanCheckItem(id="human_06_01", description="cycle end-to-end"),),
    }
    base.update(overrides)
    return ReviewRuntimeSpec(**base)  # type: ignore[arg-type]


def test_create_spawns_containers_and_sets_expires_at() -> None:
    from workers.review_runtime import ReviewRuntimeManager

    spawned: list[object] = []

    def spawn(s: object) -> tuple[str, ...]:
        spawned.append(s)
        return ("main", "postgres", "redis")

    mgr = ReviewRuntimeManager(spawn=spawn)
    spec = _spec()
    session = mgr.create(spec)

    assert session.status == "running"
    assert session.container_ids == ("main", "postgres", "redis")
    assert session.expires_at > time.time() + 47 * 3600
    assert session.expires_at < time.time() + 49 * 3600
    assert len(spawned) == 1


def test_create_carries_checklist_and_aux_services() -> None:
    from workers.review_runtime import ReviewRuntimeManager

    mgr = ReviewRuntimeManager(spawn=lambda _s: ("c",))
    session = mgr.create(_spec())
    assert len(session.spec.aux_services) == 2
    assert session.spec.aux_services[0].name == "postgres"
    assert session.spec.human_checklist[0].id == "human_06_01"
