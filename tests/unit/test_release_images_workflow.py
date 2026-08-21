"""Static checks on the image publish workflow (Plan prod-01 task_03).

release-images.yml builds & pushes the five app images the installer's compose
references (deploy-2). These pin its structure so it cannot silently lose an
app or build the dependent backends before their api-server base image."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release-images.yml"
APPS = ["api-server", "workers", "orchestrator", "notification-dispatcher", "admin-panel"]


def _load() -> dict[str, Any]:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # YAML 1.1: bare `on:` parses as the boolean True.
    if True in data and "on" not in data:
        data["on"] = data.pop(True)
    return data


def test_triggers_on_release_tag_and_manual_dispatch() -> None:
    on = _load()["on"]
    assert "workflow_dispatch" in on, "release workflow must be manually dispatchable"
    push = on.get("push") or {}
    tags = push.get("tags") or []
    assert any("v" in str(t) for t in tags), "must trigger on a v* release tag"


def test_publishes_all_five_app_images() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    missing = [app for app in APPS if app not in text]
    assert not missing, f"release workflow does not build: {missing}"
    assert "push: true" in text, "build-push-action must push (push: true)"
    assert "ghcr.io/${{ github.repository_owner }}" in text, (
        "must publish to the REPO OWNER's GHCR namespace: it is the only "
        "one the Actions GITHUB_TOKEN can push to. A foreign org would need "
        "a long-lived classic PAT as a secret, which is a worse supply chain "
        "than the problem it solves (see test_ghcr_namespace_is_pushable)."
    )


def test_dependent_backends_build_after_api_server_base() -> None:
    """workers/orchestrator/notification-dispatcher reuse the api-server image as
    BASE_IMAGE, so their job must `needs` api-server (build order, task_01)."""
    jobs = _load()["jobs"]
    backend = jobs.get("backend")
    assert backend is not None, "expected a 'backend' matrix job"
    needs = backend.get("needs") or []
    if isinstance(needs, str):
        needs = [needs]
    assert "api-server" in needs, "dependent backends must build AFTER api-server"
    assert "BASE_IMAGE=" in WORKFLOW.read_text(encoding="utf-8")


def test_every_job_declares_a_timeout() -> None:
    """Forward-compatible with prod-02's harness invariant (no 6h-default jobs)."""
    jobs = _load()["jobs"]
    missing = [name for name, job in jobs.items() if "timeout-minutes" not in job]
    assert not missing, f"jobs without timeout-minutes: {missing}"
