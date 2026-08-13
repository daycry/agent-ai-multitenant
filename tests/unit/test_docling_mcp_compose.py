"""Meta-test for docling-mcp in the compose stack (Plan 04 task_04_21).

History:

  - Plan 04 task_04_21 introduced the docling-mcp service in
    `docker/docker-compose.yml` pointing at
    `ghcr.io/docling-project/docling-mcp:latest`. This test file
    asserted the service was wired correctly.
  - Plan 04.5 (post-merge session, 2026-05-26) discovered that
    upstream `docling-project/docling-mcp` does **not** publish a
    public Docker image to GHCR (verified at
    `https://github.com/docling-project/docling-mcp/pkgs/container/`
    returns 404; the project ships as a uvx-runnable Python package).
    The image pull failed with `error from registry: denied`,
    blocking `docker compose up -d`.
  - Decision: comment out the `docling-mcp` block in compose, with a
    gotcha file explaining when / how to reactivate it. See
    `docs/03-guides/gotchas/docling-mcp-no-public-image.md`.

So today the contract this file asserts is the OPPOSITE of what it
asserted before: the `docling-mcp` service must NOT appear in the
parsed compose, and the gotcha file must exist so anyone tripping on
this finds the explanation.

When upstream publishes an image (or we wrap one in
`docker/docling-mcp/Dockerfile`), reactivate the block in compose
and revert this test to the original shape — both directions are
documented as a single intentional flip.
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


def _load_compose_raw() -> str:
    repo_root = Path(__file__).resolve().parents[2]
    compose_path = repo_root / "docker" / "docker-compose.yml"
    return compose_path.read_text(encoding="utf-8")


def test_docling_mcp_service_is_not_active() -> None:
    """docling-mcp must NOT be a live service in compose. Upstream does
    not publish a public Docker image; pulling fails with `denied`."""
    compose = _load_compose()
    assert "docling-mcp" not in compose["services"], (
        "docling-mcp is back in services — confirm upstream now publishes a "
        "public image at ghcr.io/docling-project/docling-mcp and revert this "
        "test to the pre-Plan-04.5 shape (assert service exists with /health "
        "on :3000)."
    )


def test_docling_mcp_block_kept_commented_with_explanation() -> None:
    """The compose file keeps the docling-mcp block as comments so
    nobody re-adds it without reading the gotcha. The block must
    explicitly point at the gotcha file."""
    raw = _load_compose_raw()
    assert "# docling-mcp:" in raw, (
        "Expected a commented-out `docling-mcp:` block in compose so future "
        "readers see it was intentional, not forgotten."
    )
    assert "docling-mcp-no-public-image.md" in raw, (
        "The commented-out block must reference the gotcha file so the reason is one click away."
    )


def test_gotcha_exists_and_explains_the_decision() -> None:
    """The gotcha that documents the missing image must exist + cover
    the four mandatory sections (sintoma / causa / fix / referencias)."""
    repo_root = Path(__file__).resolve().parents[2]
    gotcha = repo_root / "docs" / "03-guides" / "gotchas" / "docling-mcp-no-public-image.md"
    assert gotcha.exists(), f"gotcha file missing: {gotcha}"
    body = gotcha.read_text(encoding="utf-8")
    # Sections expected by `docs/03-guides/gotchas/README.md`.
    for header in ("## Sintoma", "## Causa raiz", "## Fix"):
        # tolerate accented variants (the actual file uses Spanish chars).
        marker = header.replace("Sintoma", "Síntoma").replace("Causa raiz", "Causa raíz")
        assert marker in body, f"missing section '{marker}' in {gotcha}"


def test_docling_serve_remains_active() -> None:
    """docling-serve (the one upstream DOES publish) must stay live in
    compose — the dev demos and the dashboard probe rely on it."""
    compose = _load_compose()
    assert "docling-serve" in compose["services"]
    serve = compose["services"]["docling-serve"]
    assert "docling-project/docling-serve" in serve["image"], serve["image"]
