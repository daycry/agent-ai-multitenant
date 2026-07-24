"""On-demand app-preview launch (ADR 0130).

The human-validation review-runtime (ADR 0062/0063) serves a project's built app
only while a plan sits in ``pending_human_validation``. ADR 0130 lets the
operator launch that same app-preview ON DEMAND — for the project's default
branch or for a specific plan's branch — for 24h, WITHOUT a verdict.

This module is the pure builder of the ``compose_review_runtime`` request for a
preview (``kind='preview'``); the endpoints (projects/plans routers) enqueue it
and later hand back the signed app URL. Keeping it pure makes the payload
shape unit-testable without a broker or DB.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from api_server.review_autostart import resolve_review_main_image, resolve_review_main_port

#: On-demand previews live 24h (the operator's stated window) — shorter than the
#: 48h human-validation verdict timeout, since a preview has no verdict to wait
#: on. The idle-suspend sweep (24h) may pause it earlier if untouched.
PREVIEW_EXPIRES_S = 24 * 60 * 60


def default_branch_of(git_config: Any) -> str:
    """The project's default branch (``git_config.default_branch``), ``main``
    fallback — the same key the PR machinery reads (``plan_pr``/``repo_clone``)."""
    if isinstance(git_config, dict):
        branch = git_config.get("default_branch")
        if isinstance(branch, str) and branch.strip():
            return branch.strip()
    return "main"


def build_preview_request(
    *,
    tenant_id: UUID,
    project: Any,
    org: Any,
    plan: Any | None = None,
) -> dict[str, Any] | None:
    """Assemble the ``compose_review_runtime`` payload for an on-demand preview.

    Returns ``None`` when the project pins no app-preview image (the caller
    surfaces an actionable 409 — "configure the app-preview image first"). When
    ``plan`` is given the preview runs that plan's branch; otherwise the
    project's default branch. The app image + port reuse the same project config
    as human validation (``repository_config.review_image``/``review_port``)."""
    repo_cfg = getattr(project, "repository_config", None)
    worker_cfg = getattr(project, "worker_config", None)
    main_image = resolve_review_main_image(repo_cfg, worker_cfg)
    if not main_image:
        return None
    request: dict[str, Any] = {
        "tenant_id": str(tenant_id),
        "kind": "preview",
        # project_id lands in the session spec (spec = request minus tenant_id),
        # so the poll endpoint can find a project preview by spec->>'project_id'.
        "project_id": str(project.id),
        "tenant_slug": org.slug if org is not None else None,
        "project_slug": project.slug,
        # ADR 0085: one bare repo per project (repo name = slug).
        "repo_name": project.slug or str(project.id),
        "main_image": main_image,
        "main_port": resolve_review_main_port(repo_cfg, worker_cfg),
        # ADR 0129: bring up the project's declared services for the preview too.
        "repository_config": dict(repo_cfg) if isinstance(repo_cfg, dict) else {},
        "expires_in_seconds": PREVIEW_EXPIRES_S,
    }
    if plan is not None:
        request["plan_id"] = str(plan.id)
        request["plan_slug"] = plan.slug
    else:
        request["preview_ref"] = default_branch_of(getattr(project, "git_config", None))
    return request


__all__ = ["PREVIEW_EXPIRES_S", "build_preview_request", "default_branch_of"]
