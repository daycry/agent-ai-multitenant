"""Single source of truth for review-runtime autostart (C8 F39 / ADR 0063).

When a plan reaches ``pending_human_validation`` it needs a *review-runtime*: a
``review_sessions`` row + (best-effort) a live app-preview container the human
validator visits. Until this module existed the autostart lived inline in
``orchestrator.dispatch`` and so only fired on the LIVE ``task.status_changed →
done`` event. The convergence reconciler (``workers.maintenance._reconcile_
complete_plans``) can ALSO move a plan to ``pending_human_validation`` when that
live event is lost (a Redis blip) — but it never triggered the autostart, so the
plan stalled there forever with no session (the reviewer URLs 404).

This module factors the autostart so BOTH callers (orchestrator + reconciler)
share ONE implementation:

  * the pure payload helpers (:func:`resolve_review_main_image`,
    :func:`resolve_review_main_port`, :func:`build_review_human_checklist`);
  * the shared constants (task name / queue / verdict window / notify event /
    the active-session statuses the idempotency guard reads);
  * :func:`build_review_autostart_request` — the async builder that returns the
    ``compose_review_runtime`` payload, or ``None`` when no spawn is warranted.

IDEMPOTENT by design: :func:`build_review_autostart_request` returns ``None`` when
an active (``running``/``suspended``) review session already exists for the plan,
so a re-driven transition (live event AND reconciler, or two reconciler passes)
never spawns a second runtime.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.domain import Plan, Project
from api_server.db.models import Organization, ReviewSession

_log = structlog.get_logger("api_server.review_autostart")

# The celery task the worker drains to spawn the human-validation review-runtime
# + persist its ``review_sessions`` row. Produced by name (clean app boundary):
# neither the orchestrator nor the reconciler imports the workers package.
COMPOSE_REVIEW_RUNTIME_TASK = "workers.compose_review_runtime"
# Celery queue the review-runtime task lands on (matches workers.celery_app
# QUEUE_NAMES + the beat schedule's review lane).
REVIEW_QUEUE = "review"
# Review-session statuses that still hold a live runtime — used by the autostart
# idempotency guard so a plan already under review never gets a 2nd runtime (the
# reconciler can re-drive the plan transition, so autostart must be safe to invoke
# more than once).
ACTIVE_REVIEW_STATUSES = ("running", "suspended")
# Verdict window the reviewer has before the session auto-expires (ADR 0063).
# Mirrors workers.review_runtime.DEFAULT_VERDICT_TIMEOUT_S (48h) WITHOUT importing
# the workers package.
REVIEW_VERDICT_TIMEOUT_S = 48 * 60 * 60
# hallazgo #4 (QA 2026-07-07): there is NO fallback image anymore. A project
# that pins none gets a session WITHOUT an app-preview container
# (``main_image=None`` → the worker skips the spawn and records
# ``spec.app_configured=false``); the session row + signed URLs (SPA shell,
# checklist, verdict) still work and the proxy answers with an honest 409
# instead of a DNS error against a dead placeholder.
DEFAULT_REVIEW_MAIN_PORT = 8080
# The notification event a freshly-spawned review-runtime fires so the plan owner
# (and the tenant's subscribed channels) learn human validation is pending. The
# worker mints the signed reviewer URLs (it owns the session id) and fans this out
# — see workers.tasks._notify_review_ready.
HUMAN_VALIDATION_NEEDED_EVENT = "human_validation_needed"


# ---------------------------------------------------------------------------
# Pure, unit-testable payload helpers
# ---------------------------------------------------------------------------


def resolve_review_main_image(
    repository_config: dict[str, Any] | None,
    worker_config: dict[str, Any] | None,
) -> str | None:
    """Resolve the project's review-runtime ``main_image`` (ADR 0063, C8 F39).

    The platform never BUILDS this image — the project's own CI does, and the
    review-runtime references it by tag. Provenance is an open product decision,
    so we read it from the project config with a tolerant precedence
    (``repository_config.review_image`` → ``repository_config.main_image`` →
    ``worker_config.review_main_image``). ``None`` when nothing is pinned
    (hallazgo #4): the worker then creates the session WITHOUT an app-preview
    container — the row + signed URLs still work, and the proxy/SPA explain
    honestly that the project has no app-preview configured. A stuck plan is
    never the outcome.
    """
    repo_cfg = repository_config or {}
    worker_cfg = worker_config or {}
    for source, key in (
        (repo_cfg, "review_image"),
        (repo_cfg, "main_image"),
        (worker_cfg, "review_main_image"),
    ):
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def resolve_review_main_port(
    repository_config: dict[str, Any] | None,
    worker_config: dict[str, Any] | None,
) -> int:
    """Resolve the port the review-runtime's main service listens on (C8 F39).

    Same precedence shape as :func:`resolve_review_main_image`; defaults to
    :data:`DEFAULT_REVIEW_MAIN_PORT` when the project pins none."""
    repo_cfg = repository_config or {}
    worker_cfg = worker_config or {}
    for source, key in (
        (repo_cfg, "review_port"),
        (repo_cfg, "main_port"),
        (worker_cfg, "review_main_port"),
    ):
        value = source.get(key)
        if isinstance(value, bool):  # bool is an int subclass — never a port
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return DEFAULT_REVIEW_MAIN_PORT


def build_review_human_checklist(specification: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Parse the plan spec's human-test block into the review checklist (C8 F39).

    The plan's canonical specification carries the ``Tests Humanos del Plan`` under
    ``tests_humans`` (or the legacy ``tests_humanos``); each entry becomes one
    checklist item ``{id, description[, hint, checklist]}`` the reviewer ticks. A
    plain string entry is accepted too (description-only). Tolerant of a missing /
    malformed block → empty list (the reviewer then validates without a checklist —
    graceful degradation)."""
    spec = specification or {}
    raw = spec.get("tests_humans")
    if raw is None:
        raw = spec.get("tests_humanos")
    if not isinstance(raw, list):
        return []
    items: list[dict[str, Any]] = []
    for idx, entry in enumerate(raw, start=1):
        if isinstance(entry, str):
            if entry.strip():
                items.append({"id": f"human_{idx:02d}", "description": entry.strip()})
            continue
        if not isinstance(entry, dict):
            continue
        item: dict[str, Any] = {
            "id": str(entry.get("id") or f"human_{idx:02d}"),
            "description": str(entry.get("description") or entry.get("title") or ""),
        }
        hint = entry.get("hint")
        if isinstance(hint, str) and hint.strip():
            item["hint"] = hint.strip()
        checklist = entry.get("checklist")
        if isinstance(checklist, list):
            item["checklist"] = [str(c) for c in checklist]
        items.append(item)
    return items


# ---------------------------------------------------------------------------
# Async builder — the shared autostart decision
# ---------------------------------------------------------------------------


async def build_review_autostart_request(
    session: AsyncSession, *, plan: Plan, tenant_id: UUID
) -> dict[str, Any] | None:
    """Assemble the ``compose_review_runtime`` payload for a plan that just reached
    ``pending_human_validation`` (C8 F39), or ``None`` when no spawn is warranted.

    IDEMPOTENT: returns ``None`` if an active (``running``/``suspended``) review
    session already exists for the plan, so a re-driven transition never spawns a
    second runtime. Also ``None`` when the project was soft-deleted (nothing to
    review). The worker resolves the actual worktree host path (it owns
    ``data_root`` + the git libraries) from the identifiers we pass.

    Invoked from BOTH convergence paths — the orchestrator's live ``_on_task_done``
    and the reconciler's ``_reconcile_complete_plans`` — so they share one decision
    and can never diverge. The caller runs BYPASSRLS with an explicit tenant
    predicate; ``tenant_id`` is threaded through every query as defence in depth."""
    existing = (
        await session.execute(
            select(ReviewSession.id)
            .where(
                ReviewSession.plan_id == plan.id,
                ReviewSession.tenant_id == tenant_id,
                # ADR 0130: an on-demand PREVIEW for this plan must NOT satisfy
                # the idempotency guard — the human-validation review still needs
                # to autostart when the plan reaches pending_human_validation.
                ReviewSession.kind == "plan",
                ReviewSession.status.in_(ACTIVE_REVIEW_STATUSES),
                ReviewSession.deleted_at.is_(None),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        _log.info("review_autostart.skipped_existing", plan_id=str(plan.id))
        return None

    project = (
        await session.execute(
            select(Project).where(
                Project.id == plan.project_id,
                Project.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if project is None:
        _log.info("review_autostart.skip_deleted_project", plan_id=str(plan.id))
        return None

    org = (
        await session.execute(select(Organization).where(Organization.id == tenant_id))
    ).scalar_one_or_none()

    repo_cfg = getattr(project, "repository_config", None)
    worker_cfg = getattr(project, "worker_config", None)
    request: dict[str, Any] = {
        "tenant_id": str(tenant_id),
        "plan_id": str(plan.id),
        # ADR 0085 decision 2: one bare repo per project (repo name = slug).
        "repo_name": project.slug or str(project.id),
        "tenant_slug": org.slug if org is not None else None,
        "project_slug": project.slug,
        "plan_slug": plan.slug,
        "main_image": resolve_review_main_image(repo_cfg, worker_cfg),
        "main_port": resolve_review_main_port(repo_cfg, worker_cfg),
        # ADR 0129 fase 2: the worker translates the project's declared services
        # (repository_config.services/env) into hardened aux sidecars + connection
        # env so a DB/cache/queue-backed app can actually be previewed.
        "repository_config": dict(repo_cfg) if isinstance(repo_cfg, dict) else {},
        "expires_in_seconds": REVIEW_VERDICT_TIMEOUT_S,
        "human_checklist": build_review_human_checklist(getattr(plan, "specification", None)),
        # Carried into the session spec so the worker can notify the owner and the
        # expiry sweep can escalate to them (C8 F40).
        "owner_user_id": str(plan.created_by) if plan.created_by is not None else None,
        "project_name": project.name,
        "plan_title": plan.title,
        "notify_event": HUMAN_VALIDATION_NEEDED_EVENT,
    }
    return request


__all__ = [
    "ACTIVE_REVIEW_STATUSES",
    "COMPOSE_REVIEW_RUNTIME_TASK",
    "DEFAULT_REVIEW_MAIN_PORT",
    "HUMAN_VALIDATION_NEEDED_EVENT",
    "REVIEW_QUEUE",
    "REVIEW_VERDICT_TIMEOUT_S",
    "build_review_autostart_request",
    "build_review_human_checklist",
    "resolve_review_main_image",
    "resolve_review_main_port",
]
