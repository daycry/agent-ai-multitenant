"""Backfill ``acceptance_criteria`` for tasks that have none, using the per-task
LLM generator (the SAME service the "Generar con IA" button and the endpoint
use). Idempotent: tasks that already have criteria are skipped unless ``--force``.

Runs under the BYPASSRLS admin engine, so it can backfill a whole project/tenant
in one pass. The project's chat provider is resolved ONCE per project (ADR 0021 /
0065 inheritance) and reused across its tasks.

Run INSIDE the api-server container (its env has the DB + Vault + provider):

    docker cp scripts/dev/backfill_acceptance_criteria.py \
        agentic-platform-api-server-1:/tmp/backfill.py
    docker exec agentic-platform-api-server-1 \
        python /tmp/backfill.py --project <PROJECT_UUID> [--force] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
from uuid import UUID

import structlog
from api_server.chat.criteria_llm import format_sibling_context, generate_task_acceptance_criteria
from api_server.chat.planning_llm import _clean_acceptance_criteria
from api_server.chat.responder import _resolve_chat_provider, resolve_chat_model_config

# Register every mapper before any session/flush (mirrors seeds/__main__).
from api_server.db import models as _models  # noqa: F401
from api_server.db.domain import Project, Task
from api_server.db.session import get_admin_sessionmaker
from api_server.logging import configure_logging
from api_server.routers.llm_providers import get_provider_vault_store
from shared_llm.base import LLMProvider
from sqlalchemy import select

# project_id -> (project|None, provider|None, api_model). Providers built once, closed once.
_ProviderCache = dict[UUID, "tuple[Project | None, LLMProvider | None, str]"]


async def _project_provider(session, providers: _ProviderCache, project_id: UUID, vault):
    """Resolve (and cache) the project's chat provider + API model for this run."""
    if project_id not in providers:
        project = (
            await session.execute(select(Project).where(Project.id == project_id))
        ).scalar_one_or_none()
        if project is None:
            providers[project_id] = (None, None, "")
        else:
            effective = await resolve_chat_model_config(session, project)
            prov, _kind, api_model = await _resolve_chat_provider(session, effective, vault)
            providers[project_id] = (project, prov, api_model)
    return providers[project_id]


async def _sibling_context(session, task: Task) -> str:
    """Digest of the plan's OTHER tasks (title + criteria) so generation stays
    coherent with a sibling's decisions (shared contract / error format)."""
    if task.plan_id is None:
        return ""
    rows = (
        await session.execute(
            select(Task.title, Task.acceptance_criteria).where(
                Task.plan_id == task.plan_id, Task.id != task.id
            )
        )
    ).all()
    siblings = [(str(title), _clean_acceptance_criteria(criteria)) for title, criteria in rows]
    return format_sibling_context(siblings)


async def _backfill_task(session, task: Task, providers, vault, *, force, dry_run, log) -> str:
    """Generate + (unless dry-run) set criteria for one task. Returns the outcome
    bucket: ``generated`` / ``skipped`` / ``failed``."""
    existing = _clean_acceptance_criteria(task.acceptance_criteria)
    if existing and not force:
        return "skipped"

    project, provider, api_model = await _project_provider(
        session, providers, task.project_id, vault
    )
    if project is None or provider is None:
        log.warning("backfill.no_provider", project=str(task.project_id))
        return "failed"

    sibling_context = await _sibling_context(session, task)

    try:
        proposal = await generate_task_acceptance_criteria(
            provider,
            title=task.title,
            description=task.description,
            existing=existing,
            project_context={"name": project.name, "description": project.description or ""},
            model=api_model,
            sibling_context=sibling_context,
        )
    except Exception as exc:
        # One transient LLM/network failure must NOT abort the whole batch (and
        # roll back the already-generated tasks via the single terminal commit).
        log.warning("backfill.generate_error", task=str(task.id), title=task.title, error=str(exc))
        return "failed"
    if not proposal:
        log.warning("backfill.empty_proposal", task=str(task.id), title=task.title)
        return "failed"

    log.info(
        "backfill.generated",
        task=str(task.id),
        title=task.title,
        count=len(proposal),
        criteria=proposal,
    )
    if not dry_run:
        task.acceptance_criteria = proposal
    return "generated"


async def _run(args: argparse.Namespace, log) -> None:
    vault = get_provider_vault_store()
    providers: _ProviderCache = {}
    counts = {"generated": 0, "skipped": 0, "failed": 0}

    async with get_admin_sessionmaker()() as session:
        stmt = select(Task)
        if args.project is not None:
            stmt = stmt.where(Task.project_id == args.project)
        if args.tenant is not None:
            stmt = stmt.where(Task.tenant_id == args.tenant)
        tasks = list((await session.execute(stmt)).scalars().all())
        log.info("backfill.start", tasks=len(tasks), force=args.force, dry_run=args.dry_run)

        try:
            for task in tasks:
                outcome = await _backfill_task(
                    session, task, providers, vault, force=args.force, dry_run=args.dry_run, log=log
                )
                counts[outcome] += 1
            if not args.dry_run:
                await session.commit()
        finally:
            for _project, provider, _model in providers.values():
                if provider is not None:
                    with contextlib.suppress(Exception):
                        await provider.aclose()

    log.info("backfill.done", dry_run=args.dry_run, **counts)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill task acceptance_criteria via the LLM.")
    parser.add_argument("--project", type=UUID, default=None, help="Limit to one project id")
    parser.add_argument("--tenant", type=UUID, default=None, help="Limit to one tenant id")
    parser.add_argument(
        "--force", action="store_true", help="Regenerate even for tasks that already have criteria"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Generate and log, but do not write to the DB"
    )
    args = parser.parse_args()
    if args.project is None and args.tenant is None:
        parser.error("pass --project and/or --tenant (refusing to scan every tenant)")

    configure_logging(service="backfill-acceptance-criteria")
    await _run(args, structlog.get_logger("backfill-acceptance-criteria"))


if __name__ == "__main__":
    asyncio.run(main())
