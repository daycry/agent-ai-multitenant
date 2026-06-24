"""Shared helpers for the Docker-backed integration tests (Plan 02
Fase B). No `test_` prefix — pytest does not collect it.

Tests that launch containers skip cleanly when the Docker daemon is
unreachable (a dev box without Docker Desktop running). CI's
integration job *does* have a daemon, so they run there.
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

# A small, ubiquitous image with a Python interpreter and a shell — it
# is the base of agent-runtime, so the daemon usually has it cached.
# Plan 02 Fase B verifies the *sandbox*, not the agent loop, so the
# container tests do not need the heavier agent-runtime:v1 image.
BASE_IMAGE = "python:3.12-slim"


def _docker_available() -> bool:
    try:
        import docker

        client = docker.from_env()
        client.ping()
        client.close()
        return True
    except Exception:
        return False


DOCKER_AVAILABLE = _docker_available()

# In CI a missing precondition (no Docker daemon, agent-runtime image not built)
# must FAIL the job, not silently skip — otherwise the Docker-backed gates (e2e
# agent pipeline, sandbox invariants) vanish without anyone noticing, exactly the
# regression finding tests-6 flagged. Locally (no CI env var) they still skip
# cleanly so a dev box without Docker Desktop can run the rest of the suite.
IN_CI = bool(os.environ.get("CI"))

# Skip locally when Docker is unreachable; under CI do NOT skip — let the test
# run and fail loudly (CI's integration job always has a daemon).
requires_docker = pytest.mark.skipif(
    not DOCKER_AVAILABLE and not IN_CI,
    reason="Docker daemon not reachable",
)


def skip_or_fail(reason: str) -> None:
    """Skip a missing-precondition test locally, but FAIL it under CI so a
    silently-disappearing Docker gate is caught (finding tests-6)."""
    if IN_CI:
        pytest.fail(f"[CI] required precondition missing: {reason}")
    pytest.skip(reason)


def docker_client() -> Any:
    """A fresh Docker client — caller is responsible for closing it."""
    import docker

    return docker.from_env()


def ensure_base_image(client: Any) -> None:
    """Pull BASE_IMAGE if the daemon does not already have it."""
    import docker

    try:
        client.images.get(BASE_IMAGE)
    except docker.errors.ImageNotFound:
        client.images.pull(BASE_IMAGE)


def last_json_line(text: str) -> dict[str, Any]:
    """Parse the last JSON object printed on stdout by a probe script."""
    for line in reversed(text.strip().splitlines()):
        stripped = line.strip()
        if stripped.startswith("{"):
            return json.loads(stripped)  # type: ignore[no-any-return]
    raise AssertionError(f"no JSON line in container output: {text!r}")
